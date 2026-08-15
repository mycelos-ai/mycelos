"""Tests for the yt-summary connector ingest — pagination, update-in-place,
and the high-water mark that resumes an incremental sync.

Mirrors the gmail ingest test pattern (tests/test_connector_ingest.py):
app fixture from mycelos.app.App, fake MCP object exposing call_tool.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
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


def test_update_with_missing_note_file_leaves_timestamp_and_counts_failure(app) -> None:
    """DB row present, .md file gone (e.g. deleted out-of-band): kb.update()
    returns False. The provenance timestamp must NOT be rewritten to the new
    value — doing so would make the next run's dedup check (stored ts ==
    new ts) wrongly believe the note is already current, hiding the failed
    write forever behind "skipped_unchanged"."""
    ingest_yt_summary(app, mcp=_FakeMCP([
        {"items": [_item()], "next_cursor": "", "has_more": False}]))
    row = app.storage.fetchone(
        "SELECT path, source FROM knowledge_notes WHERE title='Retrieval 101'")
    note_file = app.knowledge_base._knowledge_dir / (row["path"] + ".md")
    note_file.unlink()

    result = ingest_yt_summary(app, mcp=_FakeMCP([{
        "items": [_item(timestamp="2026-08-14T10:00:00+00:00",
                        content="## Summary\n\nRewritten after resummarize.")],
        "next_cursor": "", "has_more": False}]))

    assert result["failed_updates"] == 1
    assert result["updated"] == 0

    after = app.storage.fetchone(
        "SELECT source FROM knowledge_notes WHERE title='Retrieval 101'")
    # Timestamp unchanged — old value preserved so the item is re-offered.
    assert json.loads(after["source"])["timestamp"] == \
           json.loads(row["source"])["timestamp"]
    assert json.loads(after["source"])["timestamp"] != "2026-08-14T10:00:00+00:00"

    # A subsequent run with the same (still-new) item retries the update
    # instead of skipping it as "unchanged" — because the stored timestamp
    # was never advanced past the failed write.
    result2 = ingest_yt_summary(app, mcp=_FakeMCP([{
        "items": [_item(timestamp="2026-08-14T10:00:00+00:00",
                        content="## Summary\n\nRewritten after resummarize.")],
        "next_cursor": "", "has_more": False}]))
    assert result2["skipped_unchanged"] == 0
    assert result2["failed_updates"] == 1


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


def test_non_dict_item_is_skipped_malformed_and_does_not_abort_the_run(app) -> None:
    """items: ["oops", ...] must not raise AttributeError out of the loop —
    that would abort the whole run on a single poisoned item, and every
    future run would re-fetch and crash on it again (permanent block)."""
    result = ingest_yt_summary(app, mcp=_FakeMCP([{
        "items": ["oops", _item(id="1:ok", title="OK")],
        "next_cursor": "", "has_more": False}]))
    assert result["skipped_malformed"] == 1
    assert result["created"] == 1


def test_bad_tags_type_is_skipped_malformed_and_does_not_abort_the_run(app) -> None:
    """items: [{..., tags: 123}] must not raise TypeError out of the loop."""
    result = ingest_yt_summary(app, mcp=_FakeMCP([{
        "items": [_item(id="1:badtags", tags=123), _item(id="1:ok", title="OK")],
        "next_cursor": "", "has_more": False}]))
    # tags=123 is coerced to [] by the mapper, not rejected — both items
    # import successfully; the point of the test is that nothing raises.
    assert result["created"] == 2
    assert result.get("skipped_malformed", 0) == 0
    note = app.storage.fetchone(
        "SELECT source FROM knowledge_notes "
        "WHERE json_extract(source, '$.external_id')='1:badtags'")
    assert note is not None


def test_garbage_timestamp_is_skipped_malformed_and_mark_unchanged(app) -> None:
    """"~~~" sorts lexicographically above any ISO digit string — if it
    reached the raw string compare it would permanently poison the mark."""
    mark_before = app.storage.fetchone(
        "SELECT value FROM knowledge_config WHERE key='ingest.yt-summary.since'")
    result = ingest_yt_summary(app, mcp=_FakeMCP([{
        "items": [_item(timestamp="~~~")],
        "next_cursor": "", "has_more": False}]))
    assert result["skipped_malformed"] == 1
    assert result["created"] == 0
    mark_after = app.storage.fetchone(
        "SELECT value FROM knowledge_config WHERE key='ingest.yt-summary.since'")
    assert (mark_after["value"] if mark_after else "") == \
           (mark_before["value"] if mark_before else "")


def test_far_future_timestamp_is_imported_but_does_not_advance_mark_past_clamp(app) -> None:
    """A forward-skewed producer clock (e.g. a Pi without an RTC) must not
    be able to push the high-water mark past consumer-now + tolerance —
    the item itself is still imported, only the mark is capped."""
    from mycelos.knowledge.connector_ingest import HIGH_WATER_CLOCK_SKEW_TOLERANCE

    result = ingest_yt_summary(app, mcp=_FakeMCP([{
        "items": [_item(timestamp="9999-01-01T00:00:00+00:00")],
        "next_cursor": "", "has_more": False}]))
    assert result["created"] == 1

    note = app.storage.fetchone(
        "SELECT source FROM knowledge_notes WHERE title='Retrieval 101'")
    assert json.loads(note["source"])["timestamp"] == "9999-01-01T00:00:00+00:00"

    mark = app.storage.fetchone(
        "SELECT value FROM knowledge_config WHERE key='ingest.yt-summary.since'")
    stored = datetime.fromisoformat(mark["value"])
    limit = datetime.now(timezone.utc) + HIGH_WATER_CLOCK_SKEW_TOLERANCE
    assert stored <= limit
    assert stored.year < 9999

    # And the clamp holds even after the poisoned run: a fresh sync must
    # not receive "9999-..." as `since` (which would starve it forever).
    mcp2 = _FakeMCP([{"items": [], "next_cursor": "", "has_more": False}])
    ingest_yt_summary(app, mcp=mcp2)
    since_sent = mcp2.calls[0]["arguments"]["since"]
    assert since_sent < "9999-01-01T00:00:00+00:00"


class _InfiniteFakeMCP:
    """Always reports has_more=True with a fresh cursor and one item per
    page — a runaway backlog bigger than MAX_SYNC_PAGES."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._n = 0

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append({"name": name, "arguments": arguments})
        self._n += 1
        # Valid, monotonically increasing ISO dates (well in the past, so
        # the clock-skew clamp in ingest_yt_summary never kicks in here).
        ts = (datetime(2020, 1, 1, tzinfo=timezone.utc)
              + timedelta(days=self._n)).isoformat()
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
    newest_processed = (datetime(2020, 1, 1, tzinfo=timezone.utc)
                         + timedelta(days=MAX_SYNC_PAGES)).isoformat()
    assert mark["value"] == newest_processed   # never advances past what was consumed


# --- Wire-through: generic API route and auto-ingest scheduler ---------
#
# No new production behavior is exercised here — the generic route
# dispatches over INGEST_SOURCES and auto_ingest_check iterates it. These
# tests PROVE the hyphenated "yt-summary" key survives both paths.


@pytest.fixture
def api_client():
    """Mirrors tests/test_connector_ingest.py::TestIngestEndpoint.client."""
    from starlette.testclient import TestClient
    from mycelos.gateway.server import create_app
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-yt-summary-ingest-api"
        from mycelos.app import App
        from mycelos.setup import web_init
        a = App(data_dir)
        a.initialize()
        web_init(a, api_key="sk-ant-api03-FAKETESTKEYYTSUMMARYAPI")
        fastapi_app = create_app(data_dir, no_scheduler=True,
                                 host="0.0.0.0", allow_insecure_bind=True)
        yield TestClient(fastapi_app), a


def test_generic_ingest_route_reaches_yt_summary(api_client, monkeypatch) -> None:
    """POST /api/knowledge/ingest/yt-summary dispatches to our function."""
    calls = {}
    def _fake_ingest(app, user_id="default", **kw):
        calls["user"] = user_id
        return {"fetched": 0, "created": 0, "updated": 0,
                "skipped_unchanged": 0, "skipped_malformed": 0}
    monkeypatch.setitem(
        __import__("mycelos.knowledge.connector_ingest",
                   fromlist=["INGEST_SOURCES"]).INGEST_SOURCES,
        "yt-summary", _fake_ingest)
    client, _ = api_client
    resp = client.post("/api/knowledge/ingest/yt-summary")
    assert resp.status_code == 200
    assert "user" in calls


def _register_yt_summary(app, status: str = "active") -> None:
    """Mirrors tests/test_scheduled_ingest.py::_register_gmail."""
    app.connector_registry.register(
        "yt-summary", "YouTube Summaries", "mcp", ["yt-summary.read"],
        description="test",
    )
    if status != "active":
        app.connector_registry.set_status("yt-summary", status)


def test_auto_ingest_check_includes_yt_summary(app, monkeypatch) -> None:
    """The scheduler runs it once opted in and the connector is active."""
    from mycelos.scheduler.jobs import auto_ingest_check

    calls = {}
    def _fake_ingest(app, user_id="default", **kw):
        calls["user"] = user_id
        return {"fetched": 0, "created": 0, "updated": 0,
                "skipped_unchanged": 0, "skipped_malformed": 0}
    monkeypatch.setitem(
        __import__("mycelos.knowledge.connector_ingest",
                   fromlist=["INGEST_SOURCES"]).INGEST_SOURCES,
        "yt-summary", _fake_ingest)

    _register_yt_summary(app)
    app.memory.set("default", "system", "auto_ingest_enabled", True)

    result = auto_ingest_check(app)
    assert result["enabled"] is True
    assert "yt-summary" in result["ran"]
    assert calls["user"] == "default"
