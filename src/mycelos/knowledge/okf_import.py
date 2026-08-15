"""Open Knowledge Format import — the inbound boundary mapper.

Mirror of ``okf_export.py`` and, with it, the only place that knows OKF.
Pure: no storage, no LLM, no I/O. Item text is data — this module never
interprets it; classification happens later in the organizer, which
frames note content as data-not-instructions.
"""
from __future__ import annotations

from datetime import datetime

# Content types allowed in imports. Structural types (topic, reminder) must never
# be created by external items — they anchor directory nodes and trigger special
# processing. Imported items are always leaf content, never structure.
_CONTENT_TYPES = frozenset({"note", "task"})


def _validate_timestamp(raw: str) -> str:
    """Reject anything that is not a parseable ISO-8601 timestamp.

    A raw string is compared and stored lexicographically downstream
    (ingest_yt_summary's high-water mark), so garbage like "~~~" or an
    unparseable value must never reach that comparison — it would sort
    above any real ISO date and permanently poison the mark. Python's
    fromisoformat is stricter than ISO-8601 (rejects a trailing "Z" on
    older versions), so normalize that form first.
    """
    if not raw:
        raise ValueError("OKF item without timestamp")
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"OKF item with unparseable timestamp: {raw!r}") from exc
    return raw


def okf_item_to_note(item: dict) -> dict:
    """Map one OKF sync item to Mycelos note fields.

    Returns {title, content, type, tags, external_id, url, timestamp}.
    Raises ValueError when the item lacks the identity fields an
    idempotent import depends on, or is not a mapping at all. Unknown
    type values are degraded to "note"; unknown keys are ignored, never
    written blindly. A malformed `tags` value never explodes the sync —
    it is dropped in favor of an empty list rather than raising, since a
    bad tags field is a cosmetic problem, not a reason to reject content.
    """
    if not isinstance(item, dict):
        raise ValueError(f"OKF item is not a mapping: {type(item).__name__}")

    external_id = str(item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    if not external_id:
        raise ValueError("OKF item without id")
    if not title:
        raise ValueError("OKF item without title")
    timestamp = _validate_timestamp(str(item.get("timestamp") or ""))

    note_type = item.get("type")
    if note_type not in _CONTENT_TYPES:
        note_type = "note"

    url = str(item.get("resource") or "").strip()
    header_bits = []
    if url:
        header_bits.append(f"Source: {url}")
    kind = item.get("kind")
    if kind:
        header_bits.append(f"Kind: {kind}")
    duration = item.get("duration_seconds")
    if duration:
        header_bits.append(f"Duration: {duration}s")

    parts = []
    if header_bits:
        parts.append(" · ".join(header_bits))
    body = str(item.get("content") or "")
    if body:
        parts.append(body)
    highlights = item.get("highlights") or []
    lines = [
        f"- {str(h.get('text') or '').strip()}"
        + (f" — {str(h.get('reason') or '').strip()}" if h.get("reason") else "")
        for h in highlights
        if isinstance(h, dict) and str(h.get("text") or "").strip()
    ]
    if lines:
        parts.append("## Highlights\n\n" + "\n".join(lines))

    raw_tags = item.get("tags")
    # A bare string is iterable char-by-char ("abc" -> ["a","b","c"]), which
    # is never the intent — treat any non-list tags value as absent.
    tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []

    return {
        "title": title,
        "content": "\n\n".join(parts),
        "type": note_type,
        "tags": tags,
        "external_id": external_id,
        "url": url,
        "timestamp": timestamp,
    }
