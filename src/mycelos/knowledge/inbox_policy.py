"""Pure inbox policy — what needs a human, and what may collapse.

No storage, no LLM, no I/O. The organizing question for every entry is
"what happens if I ignore this forever?":

* Nothing breaks, the brain is just less tidy  -> optimization, not shown
* Something is lost or structurally changed    -> consequence, shown
* A commitment of mine lapses                  -> obligation, shown

Spec: docs/superpowers/specs/2026-W33-inbox-design.md
"""
from __future__ import annotations

# Suggestion kinds whose consequences are irreversible, structural, or
# invisible-if-ignored. Everything else is an optimization: it is applied
# and marked uncertain instead of queuing for confirmation.
INBOX_KINDS = frozenset({
    "merge",               # destructive: archives the secondary note
    "new_topic",           # structural: creates a folder
    "new_topic_confirm",   # structural: new main category under a source
    "scope_violation",     # a source proposed a path outside its subtrees
    "failed_run",          # silence would mean a dead sync nobody notices
    "unclassifiable",      # the organizer gave up; nobody else will look
})

_OPTIMIZATION_KINDS = frozenset({"move", "link", "refine_type"})


def needs_human(kind: str) -> bool:
    """Whether an entry of this kind belongs in the inbox.

    Fail closed: an unrecognised kind is shown rather than hidden — a
    new entry type must never disappear silently because nobody
    classified it.
    """
    if kind in _OPTIMIZATION_KINDS:
        return False
    return True


def is_collapsible(kind: str) -> bool:
    """Whether many entries of this kind may become one summary line."""
    return kind in _OPTIMIZATION_KINDS


def collapse_key(entry: dict) -> str | None:
    """Group key for collapsing, or None when the entry must stand alone.

    Collapsing happens per run, per source, per kind — two sources in the
    same run stay two entries.
    """
    kind = entry.get("kind", "")
    if not is_collapsible(kind):
        return None
    run_id = entry.get("run_id")
    if not run_id:
        return None
    return f"{run_id}|{entry.get('source', '')}|{kind}"
