"""Open Knowledge Format import — the inbound boundary mapper.

Mirror of ``okf_export.py`` and, with it, the only place that knows OKF.
Pure: no storage, no LLM, no I/O. Item text is data — this module never
interprets it; classification happens later in the organizer, which
frames note content as data-not-instructions.
"""
from __future__ import annotations

# Content types allowed in imports. Structural types (topic, reminder) must never
# be created by external items — they anchor directory nodes and trigger special
# processing. Imported items are always leaf content, never structure.
_CONTENT_TYPES = frozenset({"note", "task"})


def okf_item_to_note(item: dict) -> dict:
    """Map one OKF sync item to Mycelos note fields.

    Returns {title, content, type, tags, external_id, url, timestamp}.
    Raises ValueError when the item lacks the identity fields an
    idempotent import depends on. Unknown type values are degraded to
    "note"; unknown keys are ignored, never written blindly.
    """
    external_id = str(item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    if not external_id:
        raise ValueError("OKF item without id")
    if not title:
        raise ValueError("OKF item without title")

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

    return {
        "title": title,
        "content": "\n\n".join(parts),
        "type": note_type,
        "tags": [str(t) for t in (item.get("tags") or [])],
        "external_id": external_id,
        "url": url,
        "timestamp": str(item.get("timestamp") or ""),
    }
