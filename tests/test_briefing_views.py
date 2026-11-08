"""Daily briefing as catalog views (Package 8, slice 3).

Spec: docs/superpowers/specs/2026-W45-generative-ui-design.md,
"The daily briefing as the first message" and success criteria 1, 4, 6, 7.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-briefing-views"
        a = App(Path(tmp))
        a.initialize()
        a._llm = None
        yield a


def _age(app, path: str, days: int) -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    app.storage.execute(
        "UPDATE knowledge_notes SET created_at=?, updated_at=? WHERE path=?",
        (old, old, path),
    )


def _id(app, path: str) -> int:
    return app.storage.fetchone("SELECT id FROM knowledge_notes WHERE path=?", (path,))["id"]


def _contradict(app, a: str, b: str, reason: str = "A says yes, B says no") -> None:
    app.storage.execute(
        "INSERT INTO knowledge_relations(from_id,to_id,kind,reason,created_by) "
        "VALUES (?,?,'contradicts',?,'source-analysis')",
        (_id(app, a), _id(app, b), reason),
    )


# ---- facts ---------------------------------------------------------------

class TestArrivals:
    def test_counts_per_source_uncapped(self, app):
        from mycelos.knowledge.briefing import MAX_LIST_ITEMS, gather_facts
        kb = app.knowledge_base
        for i in range(MAX_LIST_ITEMS + 3):
            kb.write(title=f"Mail {i}", content="x", type="note", created_by="import",
                     source={"kind": "connector", "connector": "gmail"})
        kb.write(title="Video", content="x", type="note", created_by="import",
                 source={"kind": "connector", "connector": "yt-summary"})
        kb.write(title="Thought", content="x", type="note",
                 source={"kind": "quick_capture"})

        arrivals = gather_facts(app)["arrivals_by_source"]
        by_label = {a["label"]: a["count"] for a in arrivals}
        assert by_label["gmail"] == MAX_LIST_ITEMS + 3
        assert by_label["yt-summary"] == 1
        assert by_label["quick_capture"] == 1
        assert arrivals[0]["label"] == "gmail"  # largest first

    def test_old_notes_not_counted(self, app):
        from mycelos.knowledge.briefing import gather_facts
        path = app.knowledge_base.write(title="Old", content="x", type="note")
        _age(app, path, 3)
        assert gather_facts(app)["arrivals_by_source"] == []

    def test_since_last_briefing(self, app):
        """Arrivals are counted from the previous briefing, not a fixed 24h."""
        from mycelos.knowledge.briefing import gather_facts
        path = app.knowledge_base.write(title="Two days old", content="x", type="note")
        _age(app, path, 2)
        since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        labels = gather_facts(app, since=since)["arrivals_by_source"]
        assert sum(a["count"] for a in labels) == 1

    def test_since_is_clamped(self, app):
        from mycelos.knowledge.briefing import MAX_SINCE_DAYS, _clamp_since
        far = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        clamped = datetime.fromisoformat(_clamp_since(far))
        assert datetime.now(timezone.utc) - clamped <= timedelta(days=MAX_SINCE_DAYS, minutes=1)
        assert _clamp_since("garbage") is None
        assert _clamp_since(None) is None

    def test_previous_cache_sets_since(self, app):
        from mycelos.knowledge.briefing import get_or_build_briefing
        earlier = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        app.memory.set("default", "system", "briefing_cache",
                       {"date": "2000-01-01", "generated_at": earlier}, created_by="test")
        briefing = get_or_build_briefing(app)
        assert briefing["since"][:10] == earlier[:10]


class TestContradictions:
    def test_new_contradiction_found(self, app):
        from mycelos.knowledge.briefing import gather_facts
        kb = app.knowledge_base
        old = kb.write(title="Contract says 30 days", content="x", type="note")
        _age(app, old, 40)
        new = kb.write(title="Mail says 14 days", content="x", type="note")
        _contradict(app, new, old)

        found = gather_facts(app)["contradictions"]
        assert len(found) == 1
        assert found[0]["from_title"] == "Mail says 14 days"
        assert found[0]["to_title"] == "Contract says 30 days"
        assert found[0]["reason"] == "A says yes, B says no"

    def test_old_contradiction_not_new(self, app):
        from mycelos.knowledge.briefing import gather_facts
        kb = app.knowledge_base
        a = kb.write(title="A", content="x", type="note")
        b = kb.write(title="B", content="x", type="note")
        _age(app, a, 10)
        _age(app, b, 10)
        _contradict(app, a, b)
        assert gather_facts(app)["contradictions"] == []

    def test_other_relation_kinds_ignored(self, app):
        from mycelos.knowledge.briefing import gather_facts
        kb = app.knowledge_base
        a = kb.write(title="A", content="x", type="note")
        b = kb.write(title="B", content="x", type="note")
        app.storage.execute(
            "INSERT INTO knowledge_relations(from_id,to_id,kind,reason) VALUES (?,?,'supports','r')",
            (_id(app, a), _id(app, b)),
        )
        assert gather_facts(app)["contradictions"] == []


class TestRediscovered:
    def _seed(self, app) -> tuple[str, str]:
        kb = app.knowledge_base
        old = kb.write(title="Kubernetes migration plan", content="cluster", type="note")
        _age(app, old, 200)
        new = kb.write(title="Kubernetes upgrade notes", content="today", type="note")
        return old, new

    def test_finds_old_related_note(self, app):
        from mycelos.knowledge.briefing import gather_facts
        old, new = self._seed(app)
        found = gather_facts(app)["rediscovered"]
        assert found is not None
        assert found["path"] == old
        assert found["because_path"] == new

    def test_recent_note_is_not_rediscovered(self, app):
        from mycelos.knowledge.briefing import gather_facts
        kb = app.knowledge_base
        kb.write(title="Kubernetes migration plan", content="x", type="note")
        kb.write(title="Kubernetes upgrade notes", content="x", type="note")
        assert gather_facts(app)["rediscovered"] is None

    def test_not_repeated_within_30_days(self, app):
        from mycelos.knowledge.briefing import _remember_rediscovered, gather_facts
        old, _ = self._seed(app)
        _remember_rediscovered(app, "default", old, date.today().isoformat())
        assert gather_facts(app)["rediscovered"] is None

    def test_build_records_rediscovery(self, app):
        from mycelos.knowledge.briefing import get_or_build_briefing
        old, _ = self._seed(app)
        get_or_build_briefing(app)
        log = app.memory.get("default", "system", "briefing_rediscovered_log")
        assert log == {old: date.today().isoformat()}

    def test_search_failure_is_soft(self, app, monkeypatch):
        from mycelos.knowledge.briefing import gather_facts
        self._seed(app)

        def boom(*a, **k):
            raise RuntimeError("index down")

        monkeypatch.setattr(app.knowledge_base, "find_relevant", boom)
        assert gather_facts(app)["rediscovered"] is None


# ---- views ---------------------------------------------------------------

def _facts(**kw) -> dict:
    base = {
        "arrivals_by_source": [],
        "contradictions": [],
        "rediscovered": None,
    }
    base.update(kw)
    return base


class TestViews:
    def test_empty_facts_no_views(self):
        from mycelos.knowledge.briefing_views import build_views
        assert build_views(_facts()) == []

    def test_counts_view(self):
        from mycelos.knowledge.briefing_views import build_views
        views = build_views(_facts(arrivals_by_source=[
            {"label": "gmail", "count": 12}, {"label": "yt-summary", "count": 3},
        ]))
        assert [v.type for v in views] == ["counts"]
        view = views[0]
        assert view.data["items"][0] == {"label": "gmail", "value": 12}
        assert view.reason
        assert "gmail: 12" in view.fallback_text

    def test_counts_folds_long_tail(self):
        from mycelos.knowledge.briefing_views import build_views
        arrivals = [{"label": f"s{i}", "count": 1} for i in range(12)]
        view = build_views(_facts(arrivals_by_source=arrivals))[0]
        assert len(view.data["items"]) == 9
        assert view.data["items"][-1]["value"] == 4

    def test_evidence_view(self):
        from mycelos.knowledge.briefing_views import build_views
        views = build_views(_facts(contradictions=[{
            "from_path": "a.md", "from_title": "Mail", "to_path": "b.md",
            "to_title": "Contract", "reason": "14 vs 30 days", "changed_at": "2026-11-08T07:00",
        }]))
        assert [v.type for v in views] == ["evidence"]
        claim = views[0].data["claims"][0]
        assert claim["relation"] == "contradicts"
        assert claim["date"] == "2026-11-08"
        assert {r.path for r in views[0].refs} == {"a.md", "b.md"}

    def test_rediscovered_view(self):
        from mycelos.knowledge.briefing_views import build_views
        views = build_views(_facts(rediscovered={
            "path": "old.md", "title": "Old plan", "created_at": "2025-03-01",
            "because_path": "new.md", "because_title": "New notes",
        }))
        assert [v.type for v in views] == ["graph_slice"]
        assert "2025" in views[0].reason
        assert len(views[0].data["nodes"]) == 2

    def test_order(self):
        from mycelos.knowledge.briefing_views import build_views
        views = build_views(_facts(
            arrivals_by_source=[{"label": "gmail", "count": 1}],
            contradictions=[{"from_path": "a", "from_title": "A", "to_path": "b",
                             "to_title": "B", "reason": "r"}],
            rediscovered={"path": "o", "title": "O", "because_path": "n",
                          "because_title": "N"},
        ))
        assert [v.type for v in views] == ["counts", "evidence", "graph_slice"]

    def test_long_titles_still_validate(self):
        from mycelos.knowledge.briefing_views import build_views
        long = "x" * 5000
        views = build_views(_facts(
            arrivals_by_source=[{"label": long, "count": 1}],
            contradictions=[{"from_path": "a", "from_title": long, "to_path": "b",
                             "to_title": long, "reason": long}],
        ))
        assert [v.type for v in views] == ["counts", "evidence"]

    def test_broken_group_is_dropped_not_fatal(self):
        from mycelos.knowledge.briefing_views import build_views
        views = build_views(_facts(
            arrivals_by_source=[{"label": "gmail", "count": 2}],
            contradictions=[{"from_title": "missing paths"}],
        ))
        assert [v.type for v in views] == ["counts"]

    def test_views_render_as_text(self):
        """Success criterion 6: every briefing view renders in Telegram and the CLI."""
        from mycelos.knowledge.briefing_views import build_views
        from mycelos.widgets.telegram_renderer import TelegramRenderer
        views = build_views(_facts(
            arrivals_by_source=[{"label": "gmail", "count": 2}],
            contradictions=[{"from_path": "a", "from_title": "A", "to_path": "b",
                             "to_title": "B", "reason": "r"}],
            rediscovered={"path": "o", "title": "O", "because_path": "n",
                          "because_title": "N"},
        ))
        renderer = TelegramRenderer()
        for view in views:
            assert renderer.render_knowledge_view(view).strip()


# ---- briefing payload ----------------------------------------------------

class TestBriefingPayload:
    def test_build_carries_views_and_lead(self, app):
        from mycelos.knowledge.briefing import build_briefing
        app.knowledge_base.write(title="Mail", content="x", type="note", created_by="import",
                                 source={"kind": "connector", "connector": "gmail"})
        briefing = build_briefing(app)
        assert briefing["lead"]
        assert [v["type"] for v in briefing["views"]] == ["counts"]
        assert briefing["counts"]["arrivals"] == 1
        json.dumps(briefing)  # cacheable in memory

    def test_markdown_has_new_sections(self, app):
        from mycelos.i18n import t
        from mycelos.knowledge.briefing import build_briefing
        app.knowledge_base.write(title="Mail", content="x", type="note", created_by="import",
                                 source={"kind": "connector", "connector": "gmail"})
        md = build_briefing(app)["markdown"]
        assert t("briefing.section.arrivals") in md
        assert "gmail: 1" in md

    def test_needs_you_is_live_not_cached(self, app):
        """The cached briefing must not freeze the inbox number for the day."""
        from mycelos.knowledge.briefing import get_or_build_briefing
        first = get_or_build_briefing(app)
        before = first["needs_you"]["count"]
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        app.knowledge_base.write(title="Pay invoice", content="x", type="task",
                                 status="open", due=yesterday)
        second = get_or_build_briefing(app)
        assert second["needs_you"]["count"] == before + 1
        assert "needs_you" not in app.memory.get("default", "system", "briefing_cache")


# ---- API -----------------------------------------------------------------

class TestFirstMessageAPI:
    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        from mycelos.gateway.server import create_app
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-briefing-views-api"
            from mycelos.app import App
            from mycelos.setup import web_init
            a = App(data_dir)
            a.initialize()
            web_init(a, api_key="sk-ant-api03-FAKETESTKEYBRIEFINGVIEWS")
            fastapi_app = create_app(data_dir, no_scheduler=True,
                                     host="0.0.0.0", allow_insecure_bind=True)
            yield TestClient(fastapi_app)

    def _no_llm(self, client):
        client.app.state.mycelos._llm = None

    def test_inbox_number_matches_badge(self, client):
        """Success criterion 1: the briefing's inbox number equals /api/inbox/count."""
        self._no_llm(client)
        kb = client.app.state.mycelos.knowledge_base
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        kb.write(title="Pay invoice", content="x", type="task", status="open", due=yesterday)
        kb.write(title="Call Alice", content="x", type="task", status="open", due=yesterday)

        body = client.get("/api/briefing/first-message").json()
        badge = client.get("/api/inbox/count").json()["count"]
        assert body["briefing"]["needs_you"]["count"] == badge
        assert badge >= 2
        listed = client.get("/api/inbox").json()["entries"]
        assert [e["id"] for e in body["briefing"]["needs_you"]["entries"]] == \
            [e["id"] for e in listed][: len(body["briefing"]["needs_you"]["entries"])]

    def test_shown_once_per_day(self, client):
        self._no_llm(client)
        first = client.get("/api/briefing/first-message").json()
        assert first["show"] is True
        # A reload before the client confirms still shows it.
        assert client.get("/api/briefing/first-message").json()["show"] is True

        resp = client.post("/api/briefing/first-message/shown")
        assert resp.status_code == 200
        assert resp.json()["shown"] == date.today().isoformat()
        assert client.get("/api/briefing/first-message").json()["show"] is False

    def test_shown_is_audited(self, client):
        self._no_llm(client)
        client.post("/api/briefing/first-message/shown")
        row = client.app.state.mycelos.storage.fetchone(
            "SELECT details FROM audit_events WHERE event_type='briefing.shown'"
        )
        assert row is not None

    def test_shown_yesterday_shows_again(self, client):
        self._no_llm(client)
        mycelos = client.app.state.mycelos
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        mycelos.memory.set("default", "system", "briefing_web_shown", yesterday,
                           created_by="test")
        assert client.get("/api/briefing/first-message").json()["show"] is True

    def test_today_endpoint_has_views(self, client):
        self._no_llm(client)
        body = client.get("/api/briefing/today").json()
        assert "views" in body and "needs_you" in body and "lead" in body


# ---- web chat wiring -------------------------------------------------------

FRONTEND = Path(__file__).resolve().parents[1] / "src/mycelos/frontend"


def _chat_briefing_methods() -> str:
    html = (FRONTEND / "pages/chat.html").read_text(encoding="utf-8")
    start = html.index("async showBriefingIfDue() {")
    end = html.index("async newSession() {")
    return html[start:end]


def _node(script: str, *args: str) -> None:
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("node not installed")
    result = subprocess.run(["node", "-e", script, *args],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


_HARNESS = """
const assert = require('node:assert/strict');
const methods = process.argv[1];
global.t = (k) => k;
global.window = { location: { href: '' } };
function chat(api) {
  global.MycelosAPI = api;
  return eval('({ messages: [], activeAgent: "Mycelos", $nextTick(f) { f(); }, scrollToBottom() {}, '
              + methods + '})');
}
"""


def test_chat_shows_briefing_in_order_and_confirms():
    script = _HARNESS + """
(async () => {
  const posts = [];
  const briefing = {
    lead: 'Good morning', needs_you: {count: 3, entries: [{id: 'e1', title: 'Pay', actions: []}], available: true},
    views: [{type: 'counts'}, {type: 'evidence'}, {type: 'graph_slice'}],
  };
  const c = chat({
    get: async (url) => { assert.equal(url, '/api/briefing/first-message'); return {show: true, briefing}; },
    post: async (url) => { posts.push(url); },
  });
  await c.showBriefingIfDue();
  assert.deepEqual(c.messages.map(m => m.role === 'view' ? m.view.type : m.role),
                   ['assistant', 'counts', 'briefing-cards', 'evidence', 'graph_slice']);
  assert.equal(c.messages[2].count, 3);
  assert.deepEqual(posts, ['/api/briefing/first-message/shown']);
})().catch(e => { console.error(e); process.exit(1); });
"""
    _node(script, _chat_briefing_methods())


def test_chat_skips_briefing_when_already_shown_or_unreachable():
    script = _HARNESS + """
(async () => {
  const posts = [];
  const shown = chat({get: async () => ({show: false, briefing: {lead: 'x'}}), post: async (u) => posts.push(u)});
  await shown.showBriefingIfDue();
  assert.equal(shown.messages.length, 0);
  const down = chat({get: async () => { throw new Error('offline'); }, post: async (u) => posts.push(u)});
  await down.showBriefingIfDue();
  assert.equal(down.messages.length, 0);
  assert.deepEqual(posts, []);
})().catch(e => { console.error(e); process.exit(1); });
"""
    _node(script, _chat_briefing_methods())


def test_chat_briefing_card_action_resolves_and_refreshes():
    script = _HARNESS + """
(async () => {
  const resolved = [];
  window.MycelosInbox = { resolve: async (entry, action) => resolved.push([entry.id, action]) };
  const c = chat({ get: async (url) => { assert.equal(url, '/api/inbox'); return {entries: [{id: 'e2'}]}; } });
  const msg = {count: 2, entries: [{id: 'e1'}, {id: 'e2'}], busyId: null, error: ''};
  await c.briefingAction(msg, {id: 'e1'}, 'done');
  assert.deepEqual(resolved, [['e1', 'done']]);
  assert.equal(msg.count, 1);
  assert.deepEqual(msg.entries.map(e => e.id), ['e2']);
  await c.briefingAction(msg, {id: 'e2', source: {path: 'notes/a b'}}, 'open');
  assert.equal(window.location.href, '/pages/knowledge.html?note=notes%2Fa%20b');
  assert.equal(resolved.length, 1);
})().catch(e => { console.error(e); process.exit(1); });
"""
    _node(script, _chat_briefing_methods())


def test_inbox_resolve_routes_are_shared():
    """The briefing cards and the inbox page resolve through the same routes."""
    script = """
const assert = require('node:assert/strict');
global.window = {};
const posts = [];
global.MycelosAPI = { post: async (url, body) => posts.push([url, body]) };
require(process.argv[1]);
(async () => {
  const r = window.MycelosInbox.resolve;
  await r({id: 'suggestion:7'}, 'accept');
  await r({source: {path: 'tasks/pay'}}, 'done');
  await r({kind: 'failed_run', source: {routine_key: 'gmail'}}, 'retry');
  await r({kind: 'unclassifiable', source: {path: 'n/x'}}, 'retry');
  assert.deepEqual(posts.map(p => p[0]), [
    '/api/organizer/suggestions/7/accept', '/api/knowledge/notes/tasks%2Fpay/done',
    '/api/inbox/runs/gmail/retry', '/api/inbox/notes/n%2Fx/retry']);
  assert.equal(typeof window.inboxApp, 'function');
})().catch(e => { console.error(e); process.exit(1); });
"""
    _node(script, str(FRONTEND / "shared/inbox.js"))


def test_chat_briefing_strings_exist_in_both_languages():
    from mycelos.i18n import get_web_translations

    html = (FRONTEND / "pages/chat.html").read_text(encoding="utf-8")
    assert '<script src="/shared/inbox.js"></script>' in html
    for lang in ("en", "de"):
        web = get_web_translations(lang)
        assert set(web["briefing"]) == {"needs_you", "all_clear", "open_inbox"}
        assert "{count}" in web["briefing"]["needs_you"]
