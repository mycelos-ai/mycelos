"""Run rows for the routine kinds that have no workflow.

Three of the four routine kinds used to write nothing durable. A source sync
that stopped working looked exactly like a source nobody had connected, and
the briefing recorded one date string. This module gives the briefing and
source syncs the same start/finish/fail discipline that
:class:`~mycelos.workflows.run_manager.WorkflowRunManager` gives workflows,
writing to the same ``workflow_runs`` table.

**It is deliberately not a second WorkflowRunManager.** That class owns
pause/resume, clarification, conversation state, retry counting and budget.
A sync has none of those: it starts, it ends, and it says what it did. The
two overlap only in the INSERT and two UPDATEs, and merging them would drag
the workflow state machine into a code path that has no states. The overlap
is noted and left.

Two rules shape this module, and both are the opposite of what a general
bookkeeping helper would do.

**Fixed causes, not exception text.** :meth:`RunRecorder.fail` stores only
the strings in :data:`CAUSES`, chosen by failure mode. Anything else is
replaced with a generic cause and dropped. An ingest exception is the most
likely message in the whole system to carry the content that failed to parse
— a note title, an address, an account number — because it is built from
exactly that data.

This is an allowlist rather than a sanitizer on purpose.
:func:`~mycelos.workflows.run_cause.sanitize_cause_text` documents its own
limit: it removes paths, quoted spans, addresses and data-shaped tokens, but
it cannot classify free prose. A street name or a company name carried as
ordinary unquoted words survives it. For the ingest path that is not good
enough, so the recorder refuses instead of cleaning. The sanitizer still runs
on the allowed strings, as a second line of defence.

**Recording never breaks the job it observes.** A run row that cannot be
written is logged and dropped; the sync still runs and the next source still
runs. This is the opposite of the run-start decision for workflows, where a
failure to record is fatal — there, refusing one execution loses nothing,
while here the work is the user's data arriving.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from mycelos.protocols import StorageBackend
from mycelos.workflows.run_cause import sanitize_cause_text

logger = logging.getLogger("mycelos.scheduler")

# The kinds this recorder writes. `workflow` and `scheduled_task` belong to
# WorkflowRunManager and are not written here. The column carries a CHECK on
# the same four values (Task 1); a kind outside it raises rather than writing
# a row that every kind-filtered read would miss.
RECORDED_KINDS = ("briefing", "source_sync")

# Fixed causes, one per failure mode. These are the strings that reach the
# `error` column. They name what failed and why; they never name what the
# data contained.
CAUSES: dict[str, str] = {
    # The connector answered and refused, and we do not know why. Deliberately
    # carries no remedy: `mcp_manager.call_tool` returns this same shape for a
    # dead subprocess, a refused connection, an expired token, a stale session,
    # a misconfigured proxy and an unparseable response. Only one of those is
    # an authorisation problem, and the one signal that would tell them apart
    # is the connector's own error string — which is the text this column
    # exists to keep out. A cause that guessed 'reauthorise' would be wrong
    # five times out of six, and a wrong cause sends the reader looking in the
    # wrong place. So it says what is known and points at the log for the rest.
    "source_failed": (
        "The sync reached the source but it did not return the data. The "
        "server log has the reason this time."
    ),
    # Reserved for a failure we can actually attribute to authorisation. The
    # returned-error branch must not use this: it cannot tell an expired token
    # from a closed socket, and this string tells the reader to redo an OAuth
    # dance.
    "source_rejected": (
        "The source rejected the request. Check that the connector is still "
        "authorised, then run the sync again."
    ),
    "source_unreachable": (
        "The source could not be reached. It may be offline or the connector "
        "may no longer be running."
    ),
    "response_unreadable": (
        "The response from the source could not be read. The connector "
        "returned something this sync does not understand."
    ),
    "briefing_undeliverable": (
        "The briefing was built but could not be delivered. Check that a "
        "Telegram channel is configured and reachable."
    ),
    "briefing_failed": (
        "The briefing did not finish. It will be tried again at the next tick."
    ),
    # What a cause the recorder does not recognise is replaced with. Honest
    # and useless beats readable and leaking.
    "unrecognised": (
        "The run failed. No cause was recorded that is safe to show here; "
        "the server log has the detail."
    ),
}

# The exact strings this package is willing to store. `fail` is an allowlist,
# not a sanitizer: see :meth:`RunRecorder._safe_cause` for why.
_ALLOWED_CAUSES = frozenset(CAUSES.values())

# Count keys we are willing to store. Everything else is dropped: a count is
# a number, and a dict that carries a title or a body is not a count. The
# allowlist is the guarantee — a connector that grows a new field cannot leak
# it into the row by accident.
_ALLOWED_COUNT_KEYS = frozenset(
    {
        "created",
        "updated",
        "fetched",
        "skipped_existing",
        "skipped_unchanged",
        "skipped_malformed",
        "failed_updates",
        "sent",
        "items",
        "notes",
        "messages",
    }
)

# `truncated` is the one flag a run row may carry. It is not a count — it is
# a bool — but it is the signal that a sync stopped at MAX_SYNC_PAGES with a
# backlog behind it, which a row reporting only counts would state as a
# complete sync. It is admitted by name and coerced to bool, so a connector
# cannot use the key to carry anything else.
_TRUNCATED_KEY = "truncated"

# The one non-numeric value a run row may carry: the routine's own name,
# which the caller already put in `routine_key`.
_SOURCE_KEY = "source"


class RunRecorder:
    """Write a run row for a briefing or a source sync.

    Three calls, in order: :meth:`start` before the work, then exactly one of
    :meth:`finish` or :meth:`fail` after it. Each is a single statement; there
    is no state machine, because these runs have no states between start and
    end.

    None of the three methods swallows a storage error — the caller decides.
    The callers in :mod:`mycelos.scheduler.jobs` catch and log, because the
    sync must outlive its own bookkeeping.
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def start(self, kind: str, routine_key: str, user_id: str = "default") -> str:
        """Open a run row and return its id.

        Args:
            kind: One of :data:`RECORDED_KINDS`. A value the column's CHECK
                rejects raises ``sqlite3.IntegrityError``, deliberately: a
                typo'd kind writes a row that no kind-filtered read finds.
            routine_key: The routine's identity — the source name, or
                ``'briefing'``. This is what a later reader groups by and what
                the inbox names when the routine fails.
            user_id: Owner of the run.

        Returns:
            The new run id.

        Raises:
            Exception: Whatever the storage backend raises. The caller decides
                whether that is fatal; for a sync it is not.
        """
        run_id = str(uuid.uuid4())[:16]
        self._storage.execute(
            """INSERT INTO workflow_runs
               (id, kind, routine_key, workflow_id, user_id, status,
                completed_steps, artifacts)
               VALUES (?, ?, ?, NULL, ?, 'running', '[]', '{}')""",
            (run_id, kind, routine_key, user_id),
        )
        return run_id

    def finish(self, run_id: str, counts: dict[str, Any] | None = None) -> None:
        """Close a run row as completed, with what the run did.

        Args:
            run_id: The run opened by :meth:`start`.
            counts: What the run did — numbers, plus the source name. Every
                other key is dropped; see :meth:`_safe_counts`.
        """
        row = self._storage.fetchone(
            "SELECT routine_key FROM workflow_runs WHERE id = ?", (run_id,)
        )
        routine_key = row["routine_key"] if row else None
        self._storage.execute(
            """UPDATE workflow_runs
               SET status = 'completed', artifacts = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE id = ?""",
            (json.dumps(self._safe_counts(counts, routine_key)), run_id),
        )

    def fail(self, run_id: str, cause: str) -> None:
        """Close a run row as failed, with a cause a human can act on.

        Args:
            run_id: The run opened by :meth:`start`.
            cause: A fixed cause from :data:`CAUSES`. It is sanitized anyway —
                the fixed strings pass through untouched, and a caller that
                hands in something else does not get to write it verbatim.
        """
        self._storage.execute(
            """UPDATE workflow_runs
               SET status = 'failed', error = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE id = ?""",
            (self._safe_cause(cause), run_id),
        )

    @staticmethod
    def _safe_cause(cause: str) -> str:
        """Return a cause safe to store. An allowlist, not a sanitizer.

        Only the fixed strings in :data:`CAUSES` are stored. Anything else is
        replaced, and the caller's text is dropped rather than cleaned.

        The reason is a documented limit of
        :func:`~mycelos.workflows.run_cause.sanitize_cause_text`: it cannot
        classify free prose. It removes paths, quoted spans, addresses and
        data-shaped tokens, but a message that carries content as ordinary
        unquoted words — a street name, a company name, a note title — is
        indistinguishable from a description of a failure and survives. For
        the ingest path that limit is not acceptable: an ingest exception is
        the most likely message in the system to be built from exactly that
        kind of text.

        So this method does not try to clean anything. A cause is either one
        this package wrote, or it is not stored. The sanitizer still runs, as
        a second line of defence for a value that is somehow in the allowlist
        and should not be.
        """
        text = (cause or "").strip()
        if text in _ALLOWED_CAUSES:
            return sanitize_cause_text(text) or CAUSES["unrecognised"]
        logger.warning(
            "Refused an unrecognised run cause — storing the generic one instead"
        )
        return CAUSES["unrecognised"]

    @staticmethod
    def _safe_counts(
        counts: dict[str, Any] | None, routine_key: str | None = None
    ) -> dict[str, Any]:
        """Keep the numbers and the source name; drop everything else.

        An allowlist rather than a denylist: a connector that adds a field
        carrying a subject line or a sender should lose it by default, not
        keep it until somebody notices.

        ``source`` is the one string a run row may carry, and it survives only
        when it equals the row's own ``routine_key``. That makes the value a
        repetition of something already stored rather than a free-text field
        a caller could fill with anything.

        ``truncated`` is the one bool, coerced rather than copied, so the key
        cannot smuggle a value of another type.
        """
        if not isinstance(counts, dict):
            return {}
        safe: dict[str, Any] = {}
        for key, value in counts.items():
            if key == _SOURCE_KEY:
                if routine_key is not None and value == routine_key:
                    safe[key] = value
                continue
            if key == _TRUNCATED_KEY:
                safe[key] = bool(value)
                continue
            if key not in _ALLOWED_COUNT_KEYS:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            safe[key] = value
        return safe
