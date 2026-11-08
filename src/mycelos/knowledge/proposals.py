"""Approval cards for capture, attach and correct (Package 8, slice 5).

Every change the conversation proposes to the filing structure becomes an
``approval_card`` view whose buttons resolve a stored proposal. The card
carries only the proposal id. What an action does (which note moves where,
which source attaches to which folder, which rule is taught) is stored here
when the card is built, so a client can only pick one of the allowed
actions, never change a target.

Nothing is written before the user presses a button, except for capture:
the user asked to remember something, so the note exists and the card shows
where it went, with accept / elsewhere / make a task.

Resolving is fail-closed: an unknown id, a proposal of another user, an
expired or already resolved proposal and an action outside the kind's set
are refused without touching anything. A proposal is claimed atomically, so
two clicks never apply it twice. Every create and resolve is audited, with
paths and ids only, never titles or rule text.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from mycelos.i18n import t
from mycelos.knowledge.source_attachment import (
    SourceAttachmentService,
    local_source_key,
    source_key,
)
from mycelos.widgets.types import Ref
from mycelos.widgets.views import make_view, view_to_dict

logger = logging.getLogger(__name__)

PROPOSAL_TTL = timedelta(days=7)

# The actions each kind may resolve with. Anything else is refused.
KIND_ACTIONS: dict[str, tuple[str, ...]] = {
    "capture": ("accept", "elsewhere", "make_task"),
    "attach_source": ("approve", "cancel"),
    "correct": ("accept", "teach_rule", "cancel"),
}

# How many other notes "teach the rule" may move with the corrected one.
RULE_PREVIEW_LIMIT = 5
_RULE_MAX = 2000
_PREVIEW_SAMPLES = 5
_ID = re.compile(r"^[0-9a-f]{32}$")
# Unfiled notes sit in these roots until the organizer sorts them.
_SYSTEM_ROOTS = frozenset({"notes", "tasks"})


class ProposalError(Exception):
    """A proposal cannot be resolved. ``code`` is stable for clients."""

    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class FilingError(ValueError):
    """A card cannot be built (unknown note, folder or source)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- Helpers over the knowledge base --------------------------------------


def _meta(app: Any, path: str) -> dict | None:
    if not isinstance(path, str) or not path:
        return None
    return app.knowledge_base._indexer.get_note_meta(path)


def topic_label(app: Any, path: str | None) -> str:
    """A folder as the user reads it: titles from the top, joined by ' / '."""
    if not path or path in _SYSTEM_ROOTS:
        return t("filing.inbox")
    titles: list[str] = []
    seen: set[str] = set()
    current: str | None = path
    while current and current not in seen and len(titles) < 8:
        seen.add(current)
        row = _meta(app, current)
        if not row:
            titles.append(current.rsplit("/", 1)[-1])
            break
        titles.append(row.get("title") or current.rsplit("/", 1)[-1])
        current = row.get("parent_path")
        if current in _SYSTEM_ROOTS or current == "topics":
            break
    return " / ".join(reversed(titles))


def resolve_topic(app: Any, value: str) -> str | None:
    """An active topic by path, or by exact title (case-insensitive)."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    row = _meta(app, value)
    if row and row.get("type") == "topic" and row.get("status") == "active":
        return value
    rows = app.storage.fetchall(
        "SELECT path FROM knowledge_notes WHERE type='topic' AND status='active' "
        "AND lower(title)=lower(?) ORDER BY length(path) LIMIT 2",
        (value,),
    )
    return rows[0]["path"] if len(rows) == 1 else None


def rule_candidates(app: Any, meta: dict, before: str | None) -> list[dict]:
    """Other notes that a taught rule would move with this one.

    Deterministic and shown on the card before anything moves: notes in the
    same folder, from the same source, that the search finds for the
    corrected note's title. At most ``RULE_PREVIEW_LIMIT``.
    """
    if not before:
        return []
    key = source_key(meta.get("source"))
    # Search each significant title word and rank by how many words a note
    # shares: a whole-title query would miss "Q2" when the title says "Q3".
    words = list(dict.fromkeys(
        w for w in re.findall(r"\w+", (meta.get("title") or "").lower())
        if len(w) >= 4 and not w.isdigit()))[:6]
    if not words:
        return []
    scores: dict[str, int] = {}
    for word in words:
        try:
            hits = app.knowledge_base.search(word, limit=40)
        except Exception:
            logger.warning("rule preview search failed", exc_info=True)
            return []
        for hit in hits:
            path = hit.get("path") if isinstance(hit, dict) else None
            if path and path != meta["path"]:
                scores[path] = scores.get(path, 0) + 1
    needed = max(1, (len(words) + 1) // 2)
    found: list[dict] = []
    for path, score in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
        if score < needed:
            break
        row = _meta(app, path)
        if not row or row.get("parent_path") != before:
            continue
        if row.get("type") == "topic" or row.get("status") == "archived":
            continue
        if source_key(row.get("source")) != key:
            continue
        found.append({"path": path, "title": row.get("title") or path})
        if len(found) >= RULE_PREVIEW_LIMIT:
            break
    return found


# --- The store --------------------------------------------------------------


class ProposalService:
    def __init__(self, app: Any, user_id: str = "default") -> None:
        self.app = app
        self.user_id = user_id
        self.db = app.storage

    def _audit(self, event: str, details: dict) -> None:
        try:
            self.app.audit.log(event, user_id=self.user_id, details=details)
        except Exception:
            logger.warning("proposal audit failed", exc_info=True)

    def create(self, kind: str, payload: dict, session_id: str | None = None) -> str:
        if kind not in KIND_ACTIONS:
            raise ValueError(f"unknown proposal kind {kind!r}")
        proposal_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO view_proposals (id, user_id, kind, payload, session_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (proposal_id, self.user_id, kind, json.dumps(payload), session_id, _iso(_now())),
        )
        self._audit("proposal.created", {"id": proposal_id, "kind": kind,
                                         "path": payload.get("path") or payload.get("topic")})
        return proposal_id

    def get(self, proposal_id: str) -> dict | None:
        if not isinstance(proposal_id, str) or not _ID.match(proposal_id):
            return None
        row = self.db.fetchone(
            "SELECT * FROM view_proposals WHERE id=? AND user_id=?",
            (proposal_id, self.user_id),
        )
        if not row:
            return None
        row = dict(row)
        try:
            row["payload"] = json.loads(row["payload"])
        except (TypeError, ValueError):
            row["payload"] = {}
        return row

    def resolve(self, proposal_id: str, action: str) -> dict[str, Any]:
        row = self.get(proposal_id)
        if row is None:
            raise ProposalError("not_found", 404)
        kind = row["kind"]
        if action not in KIND_ACTIONS.get(kind, ()):
            self._audit("proposal.refused", {"id": proposal_id, "reason": "action"})
            raise ProposalError("invalid_action")
        if row["status"] != "pending":
            raise ProposalError("already_resolved", 409)
        created = _parse(row.get("created_at"))
        if created is None or _now() - created > PROPOSAL_TTL:
            self._audit("proposal.refused", {"id": proposal_id, "reason": "expired"})
            raise ProposalError("expired", 410)

        claimed = self.db.execute(
            "UPDATE view_proposals SET status='running' WHERE id=? AND user_id=? AND status='pending'",
            (proposal_id, self.user_id),
        )
        if not getattr(claimed, "rowcount", 1):
            raise ProposalError("already_resolved", 409)

        try:
            result = _EXECUTORS[kind](self, row["payload"], action)
        except Exception as exc:
            # Nothing was applied, or the executor rolled back: the card can
            # be pressed again.
            self.db.execute(
                "UPDATE view_proposals SET status='pending' WHERE id=?", (proposal_id,)
            )
            code = exc.code if isinstance(exc, (ProposalError, FilingError)) else "failed"
            self._audit("proposal.failed", {"id": proposal_id, "kind": kind,
                                            "action": action, "error": code})
            if not isinstance(exc, (ProposalError, FilingError)):
                logger.warning("proposal %s failed", proposal_id, exc_info=True)
            raise ProposalError(code, 422) from None

        self.db.execute(
            "UPDATE view_proposals SET status='resolved', action=?, result=?, resolved_at=? WHERE id=?",
            (action, json.dumps(result), _iso(_now()), proposal_id),
        )
        self._audit("proposal.resolved", {"id": proposal_id, "kind": kind, "action": action})
        return {"id": proposal_id, "kind": kind, "action": action, **result}

    # --- Executors ----------------------------------------------------------

    def _attachments(self) -> SourceAttachmentService:
        return SourceAttachmentService(
            self.db,
            notifier=getattr(self.app, "config_notifier", None),
            audit=self.app.audit,
        )

    def _confirm_placement(self, path: str) -> None:
        self.db.execute(
            "UPDATE knowledge_notes SET placement_confidence=NULL, "
            "organizer_state=CASE WHEN parent_path IS NULL OR parent_path IN ('notes', 'tasks') "
            "THEN organizer_state ELSE 'done' END WHERE path=?",
            (path,),
        )
        self._audit("knowledge.placement_confirmed", {"path": path})

    def _capture(self, payload: dict, action: str) -> dict:
        path = payload.get("path", "")
        meta = _meta(self.app, path)
        if not meta:
            raise FilingError("note_not_found")
        if action == "accept":
            self._confirm_placement(path)
            return {"status": "accepted", "path": path}
        if action == "make_task":
            if not self.app.knowledge_base.update(path, note_type="task", status="open"):
                raise FilingError("note_not_found")
            return {"status": "task", "path": path}
        # elsewhere: nothing changes here; the user says where, and the chat
        # builds a correction card for it.
        return {"status": "ask_where", "path": path, "title": meta.get("title") or path}

    def _attach_source(self, payload: dict, action: str) -> dict:
        if action == "cancel":
            return {"status": "cancelled"}
        source_id = payload["source_id"]
        topic = payload["topic"]
        if resolve_topic(self.app, topic) != topic:
            raise FilingError("topic_not_found")
        from mycelos.knowledge.local_sources import LocalSourceService

        sources = LocalSourceService(self.app, self.user_id)
        sources.get(source_id)  # raises for an unknown source
        key = local_source_key(source_id)
        attachments = self._attachments()
        attachments.attach(key, topic, self.user_id)
        rule = (payload.get("rule") or "").strip()
        if rule:
            attachments.set_rule(key, rule, self.user_id)
        # A source that was imported before keeps its root folder; move it
        # into the attached folder so old and new knowledge sit together.
        root = self.db.fetchone(
            "SELECT n.path, n.parent_path FROM source_article_roots r "
            "JOIN knowledge_notes n ON n.id=r.note_id WHERE r.source_id=?",
            (source_id,),
        )
        if root and root.get("parent_path") != topic:
            self.app.knowledge_base.move_to_topic(root["path"], topic)
        try:
            started = sources.enqueue(source_id)
            import_status = started.get("status", "queued")
        except Exception:
            # The attachment stands; the import can be started later.
            logger.warning("import after attach did not start", exc_info=True)
            import_status = "not_started"
        return {"status": "attached", "topic": topic, "import": import_status}

    def _correct(self, payload: dict, action: str) -> dict:
        if action == "cancel":
            return {"status": "cancelled"}
        kb = self.app.knowledge_base
        path = payload["path"]
        target = payload["after"]
        if not _meta(self.app, path):
            raise FilingError("note_not_found")
        if not kb.move_to_topic(path, target):
            raise FilingError("move_failed")
        self._confirm_placement(path)
        result: dict[str, Any] = {"status": "moved", "path": path, "topic": target, "moved": 1}
        if action != "teach_rule":
            return result
        for other in payload.get("others") or []:
            row = _meta(self.app, other)
            # Only notes still where the card showed them move along.
            if row and row.get("parent_path") == payload.get("before") and kb.move_to_topic(other, target):
                self._confirm_placement(other)
                result["moved"] += 1
        key = payload.get("rule_key")
        line = (payload.get("rule_line") or "").strip()
        if key and line:
            attachments = self._attachments()
            existing = attachments.get_rule(key, self.user_id).strip()
            if line not in existing:
                combined = (existing + "\n" + line).strip() if existing else line
                attachments.set_rule(key, combined[-_RULE_MAX:], self.user_id)
            result["rule"] = True
        return result


_EXECUTORS = {
    "capture": ProposalService._capture,
    "attach_source": ProposalService._attach_source,
    "correct": ProposalService._correct,
}


# --- Card builders ------------------------------------------------------------


def _actions(*ids: str) -> list[dict[str, str]]:
    return [{"id": i, "label": t("filing.action." + i)} for i in ids]


def _card(title: str, reason: str, data: dict, refs: list[Ref]) -> dict:
    return view_to_dict(make_view("approval_card", title=title, reason=reason,
                                  data=data, refs=refs))


def capture_card(app: Any, path: str, *, user_id: str = "default",
                 session_id: str | None = None) -> dict | None:
    """Where a captured note went, with accept / elsewhere / make a task."""
    meta = _meta(app, path)
    if not meta:
        return None
    title = meta.get("title") or path
    confidence = meta.get("placement_confidence")
    actions = ["accept", "elsewhere"] + ([] if meta.get("type") == "task" else ["make_task"])
    proposal = ProposalService(app, user_id).create(
        "capture", {"path": path}, session_id=session_id)
    data = {
        "summary": t("filing.capture.summary", title=title),
        "changes": [{
            "title": title,
            "before": None,
            "after": topic_label(app, meta.get("parent_path")),
            "confidence": float(confidence) if isinstance(confidence, (int, float)) else None,
        }],
        "effects": ([t("filing.capture.inbox_effect")]
                    if (meta.get("parent_path") or "notes") in _SYSTEM_ROOTS else []),
        "actions": _actions(*actions),
        "proposal": proposal,
    }
    return _card(t("filing.capture.title"), t("filing.capture.reason"), data,
                 [Ref(path=path, title=title)])


def correct_card(app: Any, path: str, topic: str, *, user_id: str = "default",
                 session_id: str | None = None) -> dict:
    """A misfiling: before/after, and what teaching the rule would also move."""
    meta = _meta(app, path)
    if not meta or meta.get("type") == "topic":
        raise FilingError("note_not_found")
    target = resolve_topic(app, topic)
    if not target:
        raise FilingError("topic_not_found")
    before = meta.get("parent_path")
    if before == target:
        raise FilingError("already_there")
    title = meta.get("title") or path
    before_label = topic_label(app, before)
    after_label = topic_label(app, target)
    others = rule_candidates(app, meta, before)
    key = source_key(meta.get("source"))
    rule_line = (f'Notes like "{title}" belong in "{after_label}", not in "{before_label}".'
                 if key else "")
    proposal = ProposalService(app, user_id).create("correct", {
        "path": path, "before": before, "after": target,
        "others": [o["path"] for o in others], "rule_key": key, "rule_line": rule_line,
    }, session_id=session_id)

    effects = [t("filing.correct.effect_accept")]
    if others:
        effects.append(t("filing.correct.effect_teach", count=len(others)))
        effects += [t("filing.correct.also_moves", title=o["title"]) for o in others]
    if key:
        effects.append(t("filing.correct.effect_rule"))
    actions = ["accept"] + (["teach_rule"] if (others or key) else []) + ["cancel"]
    data = {
        "summary": t("filing.correct.summary", title=title, after=after_label),
        "changes": [{"title": title, "before": before_label, "after": after_label,
                     "confidence": None}],
        "effects": effects,
        "actions": _actions(*actions),
        "proposal": proposal,
    }
    refs = [Ref(path=path, title=title)] + [Ref(path=o["path"], title=o["title"]) for o in others]
    return _card(t("filing.correct.title"), t("filing.correct.reason"), data, refs)


def attach_cards(app: Any, source_id: str, topic: str, rule: str = "", *,
                 user_id: str = "default", session_id: str | None = None) -> list[dict]:
    """Attaching a local source: what, where, rule, and an import preview.

    Reads the source like ``local_sources preview`` does: file names and
    counts, no model call, nothing written except the proposal.
    """
    from mycelos.knowledge.local_sources import LocalSourceService

    sources = LocalSourceService(app, user_id)
    try:
        source = sources.get(source_id)
    except Exception:
        raise FilingError("source_not_found") from None
    target = resolve_topic(app, topic)
    if not target:
        raise FilingError("topic_not_found")
    rule = (rule or "").strip()[:_RULE_MAX]
    name = source.get("name") or source_id
    where = topic_label(app, target)
    try:
        preview = sources.preview(source_id, limit=_PREVIEW_SAMPLES)
    except Exception:
        preview = None

    proposal = ProposalService(app, user_id).create("attach_source", {
        "source_id": source_id, "topic": target, "rule": rule,
    }, session_id=session_id)

    effects = [t("filing.attach.where", where=where),
               t("filing.attach.rule", rule=rule) if rule else t("filing.attach.no_rule"),
               t("filing.attach.import_after")]
    changes = []
    if preview:
        changes = [{"title": s.get("title") or s.get("path") or "?", "before": None,
                    "after": where + " / " + name, "confidence": None}
                   for s in (preview.get("samples") or [])[:_PREVIEW_SAMPLES]]
    data = {
        "summary": t("filing.attach.summary", name=name, where=where),
        "changes": changes,
        "effects": effects,
        "actions": _actions("approve", "cancel"),
        "proposal": proposal,
    }
    views = [_card(t("filing.attach.title", name=name), t("filing.attach.reason"), data, [])]
    if preview and (preview.get("files") or preview.get("units")):
        approx = t("filing.attach.estimated") if preview.get("counts_estimated") else ""
        views.append(view_to_dict(make_view(
            "counts",
            title=t("filing.attach.preview_title", name=name),
            reason=t("filing.attach.preview_reason") + (f" ({approx})" if approx else ""),
            data={"items": [
                {"label": t("filing.attach.files"), "value": int(preview.get("files") or 0)},
                {"label": t("filing.attach.units"), "value": int(preview.get("units") or 0)},
            ]},
        )))
    return views
