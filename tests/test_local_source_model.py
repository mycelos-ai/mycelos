"""Local sources in the shared source model (attachments, rules, generations).

Local sources (folders, Obsidian vaults, repositories) use the same
attachment and rule tables as connectors under the key ``local:<id>``.
Their mutations are audited and land in config generations, and the
attachments and rules are part of the rollback-able state.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mycelos.app import App
from mycelos.knowledge.local_readers import SourceUnit
from mycelos.knowledge.local_sources import LocalSourceService
from mycelos.knowledge.source_attachment import SourceAttachmentService, local_source_key


@pytest.fixture
def env(tmp_path):
    app = App(tmp_path / "data")
    app.initialize()
    root = tmp_path / "vault"
    root.mkdir()
    (root / "README.md").write_text("Client onboarding checklist.")
    from mycelos.gateway.routers.local_sources import router as local_router
    from mycelos.gateway.routers.sources import router as sources_router
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(local_router)
    api.include_router(sources_router)
    return app, root, TestClient(api)


def _generations(app) -> int:
    return app.storage.fetchone("SELECT count(*) AS n FROM config_generations")["n"]


def _events(app, prefix: str) -> list[str]:
    rows = app.storage.fetchall(
        "SELECT event_type FROM audit_events WHERE event_type LIKE ? ORDER BY rowid",
        (prefix + "%",),
    )
    return [r["event_type"] for r in rows]


def test_add_update_pause_are_audited_and_create_generations(env):
    app, root, _ = env
    svc = LocalSourceService(app)
    before = _generations(app)
    source = svc.add("Vault", "folder", str(root), mode="direct")
    svc.update(source["id"], name="Vault 2", purpose="", mode="direct")
    svc.pause(source["id"], True)
    svc.pause(source["id"], False)
    assert _events(app, "local_source.") == [
        "local_source.added", "local_source.updated",
        "local_source.paused", "local_source.resumed",
    ]
    assert _generations(app) >= before + 1


def test_audit_payload_never_carries_the_root_path(env):
    app, root, _ = env
    LocalSourceService(app).add("Vault", "folder", str(root), mode="direct")
    row = app.storage.fetchone(
        "SELECT details FROM audit_events WHERE event_type='local_source.added'")
    assert str(root) not in (row["details"] or "")


def test_filing_reads_the_shared_attachment_and_rule_tables(env):
    app, root, client = env
    source = LocalSourceService(app).add("Vault", "folder", str(root), mode="direct")
    topic = app.knowledge_base.create_topic("Clients")
    shared = SourceAttachmentService(app.storage)
    shared.attach(local_source_key(source["id"]), topic)
    shared.set_rule(local_source_key(source["id"]), "Onboarding goes to Clients.")
    response = client.get(f"/api/local-sources/{source['id']}/filing")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "source_key": "local:" + source["id"],
        "attachments": [topic],
        "rule_text": "Onboarding goes to Clients.",
    }


def test_filing_of_unknown_source_is_refused(env):
    _, _, client = env
    assert client.get("/api/local-sources/nope/filing").status_code == 422


def test_sources_api_accepts_local_source_keys(env):
    app, root, client = env
    source = LocalSourceService(app).add("Vault", "folder", str(root), mode="direct")
    topic = app.knowledge_base.create_topic("Clients")
    key = "local:" + source["id"]
    assert client.post(f"/api/sources/{key}/attachments",
                       json={"topic_path": topic}).status_code == 200
    assert client.put(f"/api/sources/{key}/rule",
                      json={"rule_text": "One rule."}).status_code == 200
    data = client.get(f"/api/sources/{key}").json()
    assert data["attachments"][0]["topic_path"] == topic
    assert data["rule_text"] == "One rule."


def test_sources_api_accepts_chat_key(env):
    _, _, client = env
    assert client.put("/api/sources/chat/rule",
                      json={"rule_text": "Tasks go to Inbox."}).status_code == 200


@pytest.mark.parametrize("key", ["local:", "local:does-not-exist", "localx"])
def test_sources_api_refuses_unknown_local_keys(env, key):
    _, _, client = env
    response = client.put(f"/api/sources/{key}/rule", json={"rule_text": "x"})
    assert response.status_code == 422


def test_sources_api_refuses_another_users_local_source(env):
    app, root, client = env
    other = LocalSourceService(app, "other")
    app.storage.execute("INSERT OR IGNORE INTO users(id, name) VALUES ('other', 'Other')")
    source = other.add("Theirs", "folder", str(root), mode="direct")
    response = client.put(f"/api/sources/local:{source['id']}/rule", json={"rule_text": "x"})
    assert response.status_code == 422
    assert SourceAttachmentService(app.storage).get_rule("local:" + source["id"], "default") == ""


def test_snapshot_carries_attachments_and_rules(env):
    app, root, _ = env
    source = LocalSourceService(app).add("Vault", "folder", str(root), mode="direct")
    topic = app.knowledge_base.create_topic("Clients")
    shared = SourceAttachmentService(app.storage)
    shared.attach(local_source_key(source["id"]), topic)
    shared.set_rule(local_source_key(source["id"]), "Rule A")
    snapshot = app.state_manager.snapshot()
    assert snapshot["sources"]["attachments"] == [
        {"source_id": "local:" + source["id"], "user_id": "default", "topic_path": topic}]
    assert snapshot["sources"]["rules"][0]["rule_text"] == "Rule A"


def test_rollback_restores_attachments_and_rules(env):
    app, root, _ = env
    key = "local:abc"
    topic = app.knowledge_base.create_topic("Clients")
    shared = SourceAttachmentService(app.storage)
    shared.attach(key, topic)
    shared.set_rule(key, "Rule A")
    snapshot = app.state_manager.snapshot()
    shared.detach(key, topic)
    shared.set_rule(key, "Rule B")
    app.state_manager.restore(snapshot)
    assert shared.list_attachments(key) == [topic]
    assert shared.get_rule(key) == "Rule A"


def test_old_snapshot_without_sources_leaves_attachments_alone(env):
    app, _, _ = env
    shared = SourceAttachmentService(app.storage)
    topic = app.knowledge_base.create_topic("Clients")
    shared.attach("gmail", topic)
    snapshot = app.state_manager.snapshot()
    snapshot.pop("sources")
    app.state_manager.restore(snapshot)
    assert shared.list_attachments("gmail") == [topic]


@pytest.mark.parametrize("lang,expected", [("de", "German"), ("en", "English")])
def test_article_language_follows_user_preference(monkeypatch, lang, expected):
    from mycelos.knowledge import source_analysis
    monkeypatch.setattr("mycelos.i18n.get_language", lambda: lang)
    seen = []

    class Broker:
        def complete(self, messages, model, purpose=None):
            seen.append(messages[0]["content"])
            return SimpleNamespace(content='{"articles":[]}', finish_reason="stop")

    unit = SourceUnit("s", "Title", "Some text.", ())
    with pytest.raises(ValueError):
        source_analysis.extract_articles(SimpleNamespace(llm=Broker()), unit,
                                         {"model": "m", "purpose": ""})
    assert f"in {expected}" in seen[0]
