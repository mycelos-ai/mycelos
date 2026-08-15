"""Day-one knowledge — pull content from connected services into the KB.

The onboarding moment: after connecting Gmail (more connectors to follow),
recent content flows into the knowledge base as notes with full provenance
(created_by='import', source.kind='connector', source.external_id) and the
organizer classifies them into topics. Idempotent: the external id (e.g.
the Gmail thread id) is the dedup key, so re-running an ingest never
duplicates notes.

Everything stays inside the user's own infrastructure — the connector is
called through the MCP layer (credential-blind for agents, EU-clean).
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("mycelos.knowledge")

# Conservative default: recent, personal mail only.
GMAIL_DEFAULT_QUERY = "newer_than:30d -category:promotions -category:social"
DEFAULT_MAX_ITEMS = 25


def external_id_exists(storage: Any, connector: str, external_id: str) -> bool:
    """True if a note from this connector with this external id exists."""
    row = storage.fetchone(
        "SELECT 1 FROM knowledge_notes "
        "WHERE json_extract(source, '$.connector') = ? "
        "AND json_extract(source, '$.external_id') = ?",
        (connector, external_id),
    )
    return row is not None


def _unwrap_result(result: Any) -> Any:
    """Normalize MCP tool results.

    Servers return either a plain JSON object or MCP content blocks
    (``{"content": [{"type": "text", "text": "<json>"}]}``). Unwrap both.
    """
    if isinstance(result, dict) and "content" in result and isinstance(result["content"], list):
        for block in result["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    return json.loads(block.get("text") or "")
                except (json.JSONDecodeError, TypeError):
                    continue
    if isinstance(result, dict) and "result" in result and len(result) == 1:
        return _unwrap_result(result["result"])
    return result


def _extract_threads(data: Any) -> list[dict]:
    """Pull the thread list out of whatever envelope the server used."""
    if isinstance(data, list):
        return [t for t in data if isinstance(t, dict)]
    if isinstance(data, dict):
        for key in ("threads", "results", "items", "messages"):
            value = data.get(key)
            if isinstance(value, list):
                return [t for t in value if isinstance(t, dict)]
    return []


def ingest_gmail(
    app: Any,
    user_id: str = "default",
    max_items: int = DEFAULT_MAX_ITEMS,
    query: str = GMAIL_DEFAULT_QUERY,
    mcp: Any = None,
) -> dict:
    """Pull recent Gmail threads into the knowledge base.

    Args:
        mcp: object with ``call_tool(name, arguments)`` — defaults to
            ``app.mcp_manager``; injectable for tests.

    Returns a summary dict: fetched / created / skipped_existing, or an
    ``error`` key when the connector call failed (fail closed: nothing is
    written on error).
    """
    mcp = mcp or app.mcp_manager
    kb = app.knowledge_base

    result = mcp.call_tool(
        "gmail.search_threads",
        {"query": query, "max_results": max_items},
    )
    if isinstance(result, dict) and result.get("error"):
        logger.warning("gmail ingest failed: %s", result["error"])
        return {"error": str(result["error"]), "fetched": 0, "created": 0,
                "skipped_existing": 0}

    threads = _extract_threads(_unwrap_result(result))

    created = 0
    skipped = 0
    for thread in threads[:max_items]:
        external_id = str(thread.get("id") or thread.get("thread_id") or "").strip()
        if not external_id:
            continue
        if external_id_exists(app.storage, "gmail", external_id):
            skipped += 1
            continue

        subject = (thread.get("subject") or "").strip() or f"Email {external_id}"
        snippet = (thread.get("snippet") or thread.get("body") or "").strip()
        sender = thread.get("from") or thread.get("sender") or ""
        date = thread.get("date") or ""

        content_lines = [snippet] if snippet else []
        meta = " · ".join(p for p in (sender, date) if p)
        if meta:
            content_lines.append(f"\n---\n*From: {meta}*")

        kb.write(
            title=subject,
            content="\n".join(content_lines) or f"Email from {sender}",
            type="note",
            created_by="import",
            source={
                "kind": "connector",
                "connector": "gmail",
                "external_id": external_id,
                "from": sender,
                "date": date,
            },
        )
        created += 1

    summary = {"fetched": len(threads), "created": created,
               "skipped_existing": skipped}
    app.audit.log(
        "knowledge.ingest.completed",
        user_id=user_id,
        details={"connector": "gmail", **summary},
    )
    logger.info("gmail ingest: %s", summary)
    return summary


YT_SUMMARY_CONNECTOR = "yt-summary"
_HIGH_WATER_KEY = "ingest.yt-summary.since"
MAX_SYNC_PAGES = 50          # hard stop against a runaway producer


def _stored_timestamp(storage: Any, connector: str, external_id: str) -> str | None:
    """The provenance timestamp currently stored for this external id."""
    row = storage.fetchone(
        "SELECT json_extract(source, '$.timestamp') AS ts FROM knowledge_notes "
        "WHERE json_extract(source, '$.connector') = ? "
        "AND json_extract(source, '$.external_id') = ?",
        (connector, external_id),
    )
    return row["ts"] if row else None


def _update_existing(app: Any, kb: Any, connector: str, note: dict) -> None:
    """Update an existing note's content/tags and refresh its provenance.

    Note: kb.update() has no title parameter, so a title change upstream
    does not retitle the note (deliberate — see task brief).
    """
    row = app.storage.fetchone(
        "SELECT path, source FROM knowledge_notes "
        "WHERE json_extract(source, '$.connector') = ? "
        "AND json_extract(source, '$.external_id') = ?",
        (connector, note["external_id"]),
    )
    if row is None:
        return
    path = row["path"]
    kb.update(path, content=note["content"], tags=note["tags"])

    source = json.loads(row["source"]) if row["source"] else {}
    source.update({
        "kind": "connector",
        "connector": connector,
        "external_id": note["external_id"],
        "url": note["url"],
        "timestamp": note["timestamp"],
    })
    app.storage.execute(
        "UPDATE knowledge_notes SET source = ? WHERE path = ?",
        (json.dumps(source), path),
    )


def ingest_yt_summary(
    app: Any,
    user_id: str = "default",
    max_items: int = DEFAULT_MAX_ITEMS,
    mcp: Any = None,
) -> dict:
    """Pull summaries from yt-summary into the knowledge base.

    Incremental: resumes from the stored high-water mark and advances it
    only after a fully successful run (fail closed — re-fetching beats
    skipping). Idempotent by external id; changed items update the
    existing note in place so topic placement and links survive.
    """
    from mycelos.knowledge.okf_import import okf_item_to_note

    mcp = mcp or app.mcp_manager
    kb = app.knowledge_base
    counts = {"fetched": 0, "created": 0, "updated": 0,
              "skipped_unchanged": 0, "skipped_malformed": 0}

    row = app.storage.fetchone(
        "SELECT value FROM knowledge_config WHERE key = ?", (_HIGH_WATER_KEY,))
    since = row["value"] if row else ""

    cursor = ""
    newest_ts = since
    truncated = True  # flips to False only when a page reports has_more=False
    for _ in range(MAX_SYNC_PAGES):
        result = mcp.call_tool(
            f"{YT_SUMMARY_CONNECTOR}.export_since",
            {"since": since, "cursor": cursor, "limit": min(max_items, 100)},
        )
        result = _unwrap_result(result)
        if not isinstance(result, dict) or result.get("error"):
            err = result.get("error") if isinstance(result, dict) else "bad response"
            logger.warning("yt-summary ingest failed: %s", err)
            return {"error": str(err), **counts}

        for item in result.get("items", []):
            counts["fetched"] += 1
            try:
                note = okf_item_to_note(item)
            except ValueError:
                counts["skipped_malformed"] += 1
                continue
            ts = note["timestamp"]
            if ts > newest_ts:
                newest_ts = ts
            if external_id_exists(app.storage, YT_SUMMARY_CONNECTOR,
                                  note["external_id"]):
                if _stored_timestamp(app.storage, YT_SUMMARY_CONNECTOR,
                                     note["external_id"]) == ts:
                    counts["skipped_unchanged"] += 1
                    continue
                _update_existing(app, kb, YT_SUMMARY_CONNECTOR, note)
                counts["updated"] += 1
            else:
                kb.write(
                    title=note["title"], content=note["content"],
                    type=note["type"], tags=note["tags"],
                    created_by="import",
                    source={"kind": "connector",
                            "connector": YT_SUMMARY_CONNECTOR,
                            "external_id": note["external_id"],
                            "url": note["url"],
                            "timestamp": ts},
                )
                counts["created"] += 1

        cursor = result.get("next_cursor", "")
        if not result.get("has_more"):
            truncated = False
            break

    if truncated:
        # Hit MAX_SYNC_PAGES with more pages still available. The cursor
        # walk consumed so far is contiguous from `since`, so newest_ts is
        # still a safe high-water mark for exactly what was processed —
        # advancing it does not skip anything. But it must not look like a
        # complete sync: flag it so the caller (and the next run) knows a
        # backlog remains behind the cap.
        counts["truncated"] = True
        logger.warning(
            "yt-summary ingest hit MAX_SYNC_PAGES=%d with more pages pending; "
            "mark advanced to last fully-consumed page, resume next run",
            MAX_SYNC_PAGES,
        )

    if newest_ts:
        app.storage.execute(
            "INSERT OR REPLACE INTO knowledge_config (key, value) VALUES (?, ?)",
            (_HIGH_WATER_KEY, newest_ts),
        )
    app.audit.log("knowledge.ingest.yt_summary", user_id=user_id,
                  details=counts)          # counts only — no item content
    return counts


# Registry for the API endpoint — more connectors land here (github, calendar).
INGEST_SOURCES = {
    "gmail": ingest_gmail,
    "yt-summary": ingest_yt_summary,
}
