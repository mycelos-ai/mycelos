"""Tests for the yt-summary connector ingest — pagination, update-in-place,
and the high-water mark that resumes an incremental sync.

Mirrors the gmail ingest test pattern (tests/test_connector_ingest.py):
app fixture from mycelos.app.App, fake MCP object exposing call_tool.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mycelos.knowledge.connector_ingest import ingest_yt_summary


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-yt-summary-ingest"
        a = App(Path(tmp))
        a.initialize()
        yield a


def _item(**overrides) -> dict:
    """A shipped export_since item, fixed key set as the producer emits it."""
    base = {
        "id": "1:dQw4w9WgXcQ",
        "source": "yt-summary",
        "type": "note",
        "title": "Retrieval 101",
        "description": "A talk about retrieval.",
        "resource": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "timestamp": "2026-08-13T09:12:00+00:00",
        "created": "2026-08-01T07:00:00+00:00",
        "tags": ["ai", "retrieval"],
        "kind": "youtube",
        "language": "de",
        "summary_model": "gemini-2.5-flash",
        "playlists": [],
        "duration_seconds": 1234,
        "highlights": [{"text": "Key point", "rank": 1, "reason": "central"}],
        "content": "## Summary\n\nThe talk explains RRF.",
    }
    base.update(overrides)
    return base


class _FakeMCP:
    """Serves export_since pages like the shipped producer."""

    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.calls: list[dict] = []

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append({"name": name, "arguments": arguments})
        if not self.pages:
            return {"items": [], "next_cursor": "", "has_more": False}
        return self.pages.pop(0)


def test_first_sync_creates_notes_with_provenance(app) -> None:
    mcp = _FakeMCP([{"items": [_item()], "next_cursor": "", "has_more": False}])
    result = ingest_yt_summary(app, mcp=mcp)
    assert result["created"] == 1 and not result.get("error")
    note = app.storage.fetchone(
        "SELECT source FROM knowledge_notes WHERE title='Retrieval 101'")
    src = json.loads(note["source"])
    assert src["connector"] == "yt-summary"
    assert src["external_id"] == "1:dQw4w9WgXcQ"
    assert src["timestamp"] == "2026-08-13T09:12:00+00:00"


def test_rerun_with_unchanged_item_skips(app) -> None:
    page = {"items": [_item()], "next_cursor": "", "has_more": False}
    ingest_yt_summary(app, mcp=_FakeMCP([dict(page)]))
    result = ingest_yt_summary(app, mcp=_FakeMCP([dict(page)]))
    assert result["created"] == 0
    assert result["skipped_unchanged"] == 1
    row = app.storage.fetchone(
        "SELECT COUNT(*) AS c FROM knowledge_notes WHERE title='Retrieval 101'")
    assert row["c"] == 1                     # idempotent, never duplicates


def test_changed_item_updates_content_in_place(app) -> None:
    ingest_yt_summary(app, mcp=_FakeMCP([
        {"items": [_item()], "next_cursor": "", "has_more": False}]))
    old = app.storage.fetchone(
        "SELECT path FROM knowledge_notes WHERE title='Retrieval 101'")
    ingest_yt_summary(app, mcp=_FakeMCP([{
        "items": [_item(timestamp="2026-08-14T10:00:00+00:00",
                        content="## Summary\n\nRewritten after resummarize.")],
        "next_cursor": "", "has_more": False}]))
    new = app.storage.fetchone(
        "SELECT path, source FROM knowledge_notes WHERE title='Retrieval 101'")
    assert new["path"] == old["path"]        # same note — placement survives
    assert json.loads(new["source"])["timestamp"] == "2026-08-14T10:00:00+00:00"
    # content updated on disk
    body = (app.knowledge_base._knowledge_dir / (new["path"] + ".md")).read_text()
    assert "Rewritten after resummarize" in body


def test_pagination_consumes_all_pages(app) -> None:
    mcp = _FakeMCP([
        {"items": [_item(id="1:a", title="A")], "next_cursor": "c1", "has_more": True},
        {"items": [_item(id="1:b", title="B")], "next_cursor": "", "has_more": False},
    ])
    result = ingest_yt_summary(app, mcp=mcp)
    assert result["created"] == 2
    assert mcp.calls[1]["arguments"]["cursor"] == "c1"


def test_error_writes_nothing_and_keeps_high_water_mark(app) -> None:
    ingest_yt_summary(app, mcp=_FakeMCP([
        {"items": [_item()], "next_cursor": "", "has_more": False}]))
    mark_before = app.storage.fetchone(
        "SELECT value FROM knowledge_config WHERE key='ingest.yt-summary.since'")
    result = ingest_yt_summary(app, mcp=_FakeMCP([{"error": "boom"}]))
    assert result["error"]
    assert result["created"] == 0
    mark_after = app.storage.fetchone(
        "SELECT value FROM knowledge_config WHERE key='ingest.yt-summary.since'")
    assert mark_after["value"] == mark_before["value"]   # not advanced


def test_successful_run_advances_high_water_mark_and_passes_it_as_since(app) -> None:
    ingest_yt_summary(app, mcp=_FakeMCP([
        {"items": [_item(timestamp="2026-08-14T10:00:00+00:00")],
         "next_cursor": "", "has_more": False}]))
    mcp2 = _FakeMCP([{"items": [], "next_cursor": "", "has_more": False}])
    ingest_yt_summary(app, mcp=mcp2)
    assert mcp2.calls[0]["arguments"]["since"] == "2026-08-14T10:00:00+00:00"


def test_malformed_item_is_counted_and_does_not_abort_the_batch(app) -> None:
    result = ingest_yt_summary(app, mcp=_FakeMCP([{
        "items": [_item(id=""), _item(id="1:ok", title="OK")],
        "next_cursor": "", "has_more": False}]))
    assert result["skipped_malformed"] == 1
    assert result["created"] == 1


class _InfiniteFakeMCP:
    """Always reports has_more=True with a fresh cursor and one item per
    page — a runaway backlog bigger than MAX_SYNC_PAGES."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._n = 0

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append({"name": name, "arguments": arguments})
        self._n += 1
        ts = f"2026-08-{self._n:02d}T00:00:00+00:00"
        return {
            "items": [_item(id=f"1:page{self._n}", title=f"Page {self._n}",
                            timestamp=ts)],
            "next_cursor": f"c{self._n}",
            "has_more": True,
        }


def test_page_cap_is_flagged_truncated_and_mark_never_exceeds_processed(app) -> None:
    from mycelos.knowledge.connector_ingest import MAX_SYNC_PAGES

    mcp = _InfiniteFakeMCP()
    result = ingest_yt_summary(app, mcp=mcp)

    assert len(mcp.calls) == MAX_SYNC_PAGES
    assert result["truncated"] is True
    assert result["created"] == MAX_SYNC_PAGES

    mark = app.storage.fetchone(
        "SELECT value FROM knowledge_config WHERE key='ingest.yt-summary.since'")
    newest_processed = f"2026-08-{MAX_SYNC_PAGES:02d}T00:00:00+00:00"
    assert mark["value"] == newest_processed   # never advances past what was consumed
