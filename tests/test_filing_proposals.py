"""Capture, attach and correct as approval cards (Package 8, slice 5).

Scenarios 3, 4 and 5 of docs/superpowers/specs/2026-W45-generative-ui-design.md.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycelos.knowledge.proposals import (
    FilingError,
    ProposalError,
    ProposalService,
    attach_cards,
    capture_card,
    correct_card,
    resolve_topic,
)
from mycelos.knowledge.source_attachment import (
    SourceAttachmentService,
    local_source_key,
    source_key,
)
from mycelos.widgets.views import view_from_payload


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-filing-proposals"
        a = App(Path(tmp))
        a.initialize()
        a._llm = None
        yield a


def _meta(app, path):
    return app.knowledge_base._indexer.get_note_meta(path)


def _events(app, event_type):
    return app.storage.fetchall(
        "SELECT details FROM audit_events WHERE event_type=?", (event_type,))


CHAT = {"kind": "chat"}


# ---- source keys ------------------------------------------------------------


class TestSourceKey:
    @pytest.mark.parametrize("raw,key", [
        ({"connector": "gmail", "tool": "x"}, "gmail"),
        (json.dumps({"kind": "obsidian", "source_id": "abc"}), "local:abc"),
        ({"kind": "chat", "conversation_id": "s1"}, "chat"),
        ({"kind": "manual"}, None),
        (None, None),
        ("{not json", None),
        ("[1, 2]", None),
    ])
    def test_keys(self, raw, key):
        assert source_key(raw) == key


# ---- scenario 3: capture --------------------------------------------------


class TestCapture:
    def test_note_write_in_chat_shows_where_it_went(self, app):
        from mycelos.tools.knowledge import execute_note_write
        topic = app.knowledge_base.create_topic("Kunden")
        result = execute_note_write(
            {"title": "Kunden-Onboarding Checkliste", "content": "1. Vertrag", "topic": topic},
            {"app": app, "chat_service": object(), "session_id": "s1", "user_id": "default"},
        )
        view = result["__view__"]
        assert view["type"] == "approval_card"
        data = view["data"]
        assert data["changes"][0]["after"] == "Kunden"
        assert data["changes"][0]["before"] is None
        assert [a["id"] for a in data["actions"]] == ["accept", "elsewhere", "make_task"]
        assert len(data["proposal"]) == 32
        assert view["refs"][0]["path"] == result["path"]
        assert "Kunden" in view["fallback_text"]

    def test_no_card_outside_the_chat(self, app):
        from mycelos.tools.knowledge import execute_note_write
        result = execute_note_write({"title": "Workflow note", "content": "x"}, {"app": app})
        assert "__view__" not in result
        assert app.storage.fetchone("SELECT count(*) AS n FROM view_proposals")["n"] == 0

    def test_no_card_for_connector_imports(self, app):
        from mycelos.tools.knowledge import execute_note_write
        result = execute_note_write(
            {"title": "Mail", "content": "x",
             "source": {"connector": "gmail", "tool": "search"}},
            {"app": app, "chat_service": object()},
        )
        assert "__view__" not in result

    def test_unfiled_capture_says_inbox(self, app):
        path = app.knowledge_base.write(title="Idee", content="x", source=CHAT)
        card = capture_card(app, path)
        assert card["data"]["changes"][0]["after"] == "Inbox (sorted later)"
        assert card["data"]["effects"]

    def test_task_has_no_make_task(self, app):
        path = app.knowledge_base.write(title="Anrufen", content="x", type="task")
        ids = [a["id"] for a in capture_card(app, path)["data"]["actions"]]
        assert "make_task" not in ids

    def test_accept_confirms_placement(self, app):
        topic = app.knowledge_base.create_topic("Kunden")
        path = app.knowledge_base.write(title="Notiz", content="x", topic=topic)
        app.storage.execute("UPDATE knowledge_notes SET placement_confidence=0.4 WHERE path=?", (path,))
        card = capture_card(app, path)
        assert card["data"]["changes"][0]["confidence"] == 0.4
        result = ProposalService(app).resolve(card["data"]["proposal"], "accept")
        assert result["status"] == "accepted"
        assert _meta(app, path)["placement_confidence"] is None
        assert _events(app, "proposal.resolved")

    def test_make_task(self, app):
        path = app.knowledge_base.write(title="Steuer", content="x")
        card = capture_card(app, path)
        ProposalService(app).resolve(card["data"]["proposal"], "make_task")
        meta = _meta(app, path)
        assert (meta["type"], meta["status"]) == ("task", "open")

    def test_elsewhere_changes_nothing(self, app):
        path = app.knowledge_base.write(title="Steuer", content="x")
        before = dict(_meta(app, path))
        card = capture_card(app, path)
        result = ProposalService(app).resolve(card["data"]["proposal"], "elsewhere")
        assert result == {**result, "status": "ask_where", "title": "Steuer"}
        after = _meta(app, path)
        assert (after["type"], after["parent_path"]) == (before["type"], before["parent_path"])


# ---- scenario 5: correct a misfiling --------------------------------------


@pytest.fixture
def misfiled(app):
    kb = app.knowledge_base
    projects = kb.create_topic("Projekte")
    client = kb.create_topic("Müller GmbH")
    main = kb.write(title="Müller Belege Q3", content="Belege", topic=projects, source=CHAT)
    similar = kb.write(title="Müller Belege Q2", content="Belege", topic=projects, source=CHAT)
    other_source = kb.write(title="Müller Belege Mail", content="Belege", topic=projects,
                            source={"connector": "gmail", "tool": "x"})
    unrelated = kb.write(title="Website Relaunch", content="x", topic=projects, source=CHAT)
    return {"projects": projects, "client": client, "main": main, "similar": similar,
            "other_source": other_source, "unrelated": unrelated}


class TestCorrect:
    def test_card_shows_before_after_and_rule_preview(self, app, misfiled):
        card = correct_card(app, misfiled["main"], "Müller GmbH")
        data = card["data"]
        assert data["changes"] == [{"title": "Müller Belege Q3", "before": "Projekte",
                                    "after": "Müller GmbH", "confidence": None}]
        assert [a["id"] for a in data["actions"]] == ["accept", "teach_rule", "cancel"]
        ref_paths = [r["path"] for r in card["refs"]]
        assert misfiled["similar"] in ref_paths
        # Same folder and same source only.
        assert misfiled["other_source"] not in ref_paths
        assert misfiled["unrelated"] not in ref_paths
        # Nothing moved yet.
        assert _meta(app, misfiled["main"])["parent_path"] == misfiled["projects"]

    def test_accept_moves_only_this_note(self, app, misfiled):
        card = correct_card(app, misfiled["main"], misfiled["client"])
        result = ProposalService(app).resolve(card["data"]["proposal"], "accept")
        assert result["moved"] == 1
        assert _meta(app, misfiled["main"])["parent_path"] == misfiled["client"]
        assert _meta(app, misfiled["similar"])["parent_path"] == misfiled["projects"]
        assert SourceAttachmentService(app.storage).get_rule("chat") == ""

    def test_teach_rule_moves_the_listed_notes_and_remembers(self, app, misfiled):
        card = correct_card(app, misfiled["main"], misfiled["client"])
        result = ProposalService(app).resolve(card["data"]["proposal"], "teach_rule")
        assert result["moved"] == 2 and result["rule"] is True
        assert _meta(app, misfiled["similar"])["parent_path"] == misfiled["client"]
        assert _meta(app, misfiled["other_source"])["parent_path"] == misfiled["projects"]
        rule = SourceAttachmentService(app.storage).get_rule("chat")
        assert "Müller GmbH" in rule and "Projekte" in rule
        # The rule change is declarative state: it lands in a config generation.
        gens = app.storage.fetchall("SELECT trigger FROM config_generations")
        assert any(g["trigger"] == "source_rule" for g in gens)

    def test_teach_rule_skips_notes_moved_meanwhile(self, app, misfiled):
        card = correct_card(app, misfiled["main"], misfiled["client"])
        elsewhere = app.knowledge_base.create_topic("Archiv")
        app.knowledge_base.move_to_topic(misfiled["similar"], elsewhere)
        result = ProposalService(app).resolve(card["data"]["proposal"], "teach_rule")
        assert result["moved"] == 1
        assert _meta(app, misfiled["similar"])["parent_path"] == elsewhere

    def test_rule_is_used_by_the_organizer(self, app, misfiled):
        """Notes captured in the chat are classified with the taught rule."""
        from mycelos.agents.handlers.knowledge_organizer_handler import KnowledgeOrganizerHandler
        card = correct_card(app, misfiled["main"], misfiled["client"])
        ProposalService(app).resolve(card["data"]["proposal"], "teach_rule")
        app.knowledge_base.write(title="Müller Belege Q4", content="Belege", source=CHAT)
        handler = KnowledgeOrganizerHandler(app)
        seen = {}

        def fake(notes, topics, rule=""):
            seen.setdefault("rules", []).append(rule)
            return {}
        handler._classify_batch = fake
        handler.run()
        assert any("Müller GmbH" in r for r in seen["rules"])

    def test_errors(self, app, misfiled):
        with pytest.raises(FilingError, match="topic_not_found"):
            correct_card(app, misfiled["main"], "Gibt es nicht")
        with pytest.raises(FilingError, match="note_not_found"):
            correct_card(app, "notes/missing", misfiled["client"])
        with pytest.raises(FilingError, match="already_there"):
            correct_card(app, misfiled["main"], misfiled["projects"])

    def test_cancel_changes_nothing(self, app, misfiled):
        card = correct_card(app, misfiled["main"], misfiled["client"])
        assert ProposalService(app).resolve(card["data"]["proposal"], "cancel")["status"] == "cancelled"
        assert _meta(app, misfiled["main"])["parent_path"] == misfiled["projects"]

    def test_tool_returns_card(self, app, misfiled):
        from mycelos.tools.filing import execute_filing_correct
        result = execute_filing_correct({"path": misfiled["main"], "topic": "Müller GmbH"},
                                        {"app": app, "session_id": "s1"})
        assert result["__view__"]["type"] == "approval_card"
        assert result["status"] == "card_shown"
        bad = execute_filing_correct({"path": misfiled["main"], "topic": "nope"}, {"app": app})
        assert bad["error"] == "topic_not_found"


# ---- scenario 4: attach a source ------------------------------------------


@pytest.fixture
def vault(app, tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    for name in ("Kunde Müller.md", "Meeting 03.11.md", "Ideen.md"):
        (root / name).write_text("# " + name + "\ntext", encoding="utf-8")
    from mycelos.knowledge.local_sources import LocalSourceService
    source = LocalSourceService(app).add("Mein Vault", "folder", str(root), mode="direct")
    topic = app.knowledge_base.create_topic("Arbeit")
    app.local_source_task = MagicMock()
    return {"source": source, "topic": topic, "root": root}


class TestAttach:
    def test_card_and_preview(self, app, vault):
        views = attach_cards(app, vault["source"]["id"], "Arbeit", "Meetings zu Kunden")
        card, counts = views
        assert card["type"] == "approval_card"
        assert [a["id"] for a in card["data"]["actions"]] == ["approve", "cancel"]
        assert any("Meetings zu Kunden" in e for e in card["data"]["effects"])
        titles = {c["title"] for c in card["data"]["changes"]}
        assert "Kunde Müller" in titles
        assert counts["type"] == "counts"
        assert {i["value"] for i in counts["data"]["items"]} >= {3}
        # Nothing attached, no import before approval.
        assert SourceAttachmentService(app.storage).list_attachments(
            local_source_key(vault["source"]["id"])) == []
        app.local_source_task.assert_not_called()

    def test_approve_attaches_sets_rule_and_imports(self, app, vault):
        sid = vault["source"]["id"]
        card = attach_cards(app, sid, vault["topic"], "Meetings zu Kunden")[0]
        result = ProposalService(app).resolve(card["data"]["proposal"], "approve")
        assert result["status"] == "attached" and result["import"] == "queued"
        svc = SourceAttachmentService(app.storage)
        assert svc.list_attachments(local_source_key(sid)) == [vault["topic"]]
        assert svc.get_rule(local_source_key(sid)) == "Meetings zu Kunden"
        app.local_source_task.assert_called_once()
        gens = app.storage.fetchall("SELECT trigger FROM config_generations")
        assert any(g["trigger"] == "source_attach" for g in gens)
        assert _events(app, "source.attached")

    def test_imported_notes_are_scoped_to_the_folder(self, app, vault):
        """The organizer files the source's notes inside the attached folder only."""
        from mycelos.agents.handlers.knowledge_organizer_handler import KnowledgeOrganizerHandler
        from mycelos.knowledge.organizer import Classification
        sid = vault["source"]["id"]
        other = app.knowledge_base.create_topic("Privat")
        card = attach_cards(app, sid, vault["topic"])[0]
        ProposalService(app).resolve(card["data"]["proposal"], "approve")
        note = app.knowledge_base.write(title="Kunde Müller", content="x", created_by="source-import",
                                        source={"kind": "folder", "source_id": sid, "external_id": "e1"})
        handler = KnowledgeOrganizerHandler(app)
        offered = {}

        def fake(notes, topics, rule=""):
            offered["topics"] = topics
            return {n["path"]: Classification(topic_path=other, confidence=0.99,
                                              related_note_paths=[], new_topic_name=None)
                    for n in notes}
        handler._classify_batch = fake
        handler.run()
        assert other not in offered["topics"]
        assert _meta(app, note)["parent_path"] != other

    def test_analyze_root_is_created_inside_the_folder(self, app, vault):
        from mycelos.knowledge import source_articles
        sid = vault["source"]["id"]
        card = attach_cards(app, sid, vault["topic"])[0]
        ProposalService(app).resolve(card["data"]["proposal"], "approve")
        root = source_articles.source_root(app, vault["source"])
        assert root["parent_path"] == vault["topic"]

    def test_cancel(self, app, vault):
        card = attach_cards(app, vault["source"]["id"], vault["topic"])[0]
        ProposalService(app).resolve(card["data"]["proposal"], "cancel")
        assert app.storage.fetchone("SELECT count(*) AS n FROM source_attachments")["n"] == 0
        app.local_source_task.assert_not_called()

    def test_errors(self, app, vault):
        with pytest.raises(FilingError, match="source_not_found"):
            attach_cards(app, "missing", vault["topic"])
        with pytest.raises(FilingError, match="topic_not_found"):
            attach_cards(app, vault["source"]["id"], "Nirgendwo")

    def test_tool_returns_card_and_preview(self, app, vault):
        from mycelos.tools.filing import execute_source_attach
        result = execute_source_attach({"source_id": vault["source"]["id"], "topic": "Arbeit"},
                                       {"app": app})
        assert [v["type"] for v in result["__view__"]] == ["approval_card", "counts"]


# ---- resolving ---------------------------------------------------------------


class TestResolve:
    def _card(self, app):
        path = app.knowledge_base.write(title="Notiz", content="x")
        return capture_card(app, path)["data"]["proposal"]

    def test_action_outside_the_kind_is_refused(self, app):
        pid = self._card(app)
        with pytest.raises(ProposalError, match="invalid_action"):
            ProposalService(app).resolve(pid, "approve")
        assert ProposalService(app).get(pid)["status"] == "pending"

    def test_twice_is_refused(self, app):
        pid = self._card(app)
        ProposalService(app).resolve(pid, "accept")
        with pytest.raises(ProposalError, match="already_resolved"):
            ProposalService(app).resolve(pid, "accept")

    def test_expired(self, app):
        pid = self._card(app)
        app.storage.execute("UPDATE view_proposals SET created_at='2020-01-01T00:00:00Z' WHERE id=?", (pid,))
        with pytest.raises(ProposalError, match="expired"):
            ProposalService(app).resolve(pid, "accept")

    def test_other_user_and_bad_ids(self, app):
        pid = self._card(app)
        app.storage.execute("INSERT OR IGNORE INTO users (id, name) VALUES ('mallory', 'M')")
        for user, ident in (("mallory", pid), ("default", "0" * 32), ("default", "../x")):
            with pytest.raises(ProposalError, match="not_found"):
                ProposalService(app, user).resolve(ident, "accept")

    def test_failure_leaves_the_card_pressable(self, app, misfiled):
        card = correct_card(app, misfiled["main"], misfiled["client"])
        pid = card["data"]["proposal"]
        app.knowledge_base.archive(misfiled["client"]) if hasattr(app.knowledge_base, "archive") else \
            app.storage.execute("UPDATE knowledge_notes SET status='archived' WHERE path=?", (misfiled["client"],))
        with pytest.raises(ProposalError, match="move_failed"):
            ProposalService(app).resolve(pid, "accept")
        assert ProposalService(app).get(pid)["status"] == "pending"
        assert _events(app, "proposal.failed")

    def test_resolve_topic_by_title_or_path(self, app):
        topic = app.knowledge_base.create_topic("Kunden")
        assert resolve_topic(app, "kunden") == topic
        assert resolve_topic(app, topic) == topic
        assert resolve_topic(app, "") is None


# ---- views --------------------------------------------------------------------


class TestCardView:
    def test_card_round_trips_and_fallback_lists_the_change(self, app, misfiled):
        card = correct_card(app, misfiled["main"], misfiled["client"])
        again = view_from_payload(card)
        assert again.type == "approval_card"
        assert again.data["proposal"] == card["data"]["proposal"]
        assert "Projekte" in card["fallback_text"] and "Müller GmbH" in card["fallback_text"]

    def test_bad_proposal_id_is_rejected(self):
        payload = {"type": "approval_card", "title": "x", "reason": "y",
                   "data": {"summary": "s", "actions": [{"id": "accept", "label": "ok"}],
                            "proposal": "not-an-id"}}
        assert view_from_payload(payload).__class__.__name__ == "TextBlock"


# ---- API -----------------------------------------------------------------------


class TestAPI:
    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        from mycelos.gateway.server import create_app
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-filing-proposals-api"
            from mycelos.app import App
            from mycelos.setup import web_init
            a = App(data_dir)
            a.initialize()
            web_init(a, api_key="sk-ant-api03-FAKETESTKEYFILINGPROPOSALS")
            fastapi_app = create_app(data_dir, no_scheduler=True,
                                     host="0.0.0.0", allow_insecure_bind=True)
            yield TestClient(fastapi_app)

    def test_resolve_and_refusals(self, client):
        app = client.app.state.mycelos
        kb = app.knowledge_base
        a = kb.create_topic("A")
        b = kb.create_topic("B")
        path = kb.write(title="Notiz", content="x", topic=a)
        pid = correct_card(app, path, b)["data"]["proposal"]

        # The body cannot name a target: extra fields are ignored.
        res = client.post(f"/api/proposals/{pid}/resolve",
                          json={"action": "accept", "topic": "notes", "path": "other"})
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "moved"
        assert _meta(app, path)["parent_path"] == b

        assert client.post(f"/api/proposals/{pid}/resolve", json={"action": "accept"}).status_code == 409
        assert client.post(f"/api/proposals/{'f' * 32}/resolve", json={"action": "accept"}).status_code == 404
        assert client.post(f"/api/proposals/{pid}/resolve", json={}).status_code == 400
        assert client.post(f"/api/proposals/{pid}/resolve", content=b"nope").status_code == 400


def test_no_capture_card_on_telegram(app):
    from mycelos.tools.knowledge import execute_note_write
    result = execute_note_write({"title": "Per Telegram", "content": "x"},
                                {"app": app, "chat_service": object(), "channel": "telegram"})
    assert "__view__" not in result


def _llm(content=None, tool=None):
    mock = MagicMock()
    mock.content = content
    mock.total_tokens = 20
    mock.model = "test-model"
    mock.cost = 0.0
    mock.prompt_tokens = 15
    mock.completion_tokens = 5
    mock.stop_reason = "tool_use" if tool else "end_turn"
    mock.tool_calls = [{"id": "tc-cap-1", "function": {
        "name": tool[0], "arguments": json.dumps(tool[1])}}] if tool else None
    return mock


@pytest.mark.parametrize("channel,cards", [("api", 1), ("telegram", 0)])
def test_chat_capture_emits_the_card(app, channel, cards):
    from unittest.mock import patch
    from mycelos.chat.service import ChatService
    app._llm = MagicMock()
    service = ChatService(app)
    session_id = service.create_session()
    calls: list = []

    def complete(*args, **kwargs):
        calls.append(kwargs.get("messages") or (args[0] if args else None))
        if len(calls) == 1:
            return _llm(tool=("note_write", {"title": "Onboarding Checkliste",
                                             "content": "1. Vertrag"}))
        return _llm(content="Gespeichert.")

    with patch.object(app.llm, "complete", side_effect=complete):
        events = service.handle_message("Merk dir: Onboarding als Checkliste", session_id,
                                        channel=channel)
    widgets = [e.data["widget"] for e in events if e.type == "widget"]
    assert len([w for w in widgets if w.get("type") == "approval_card"]) == cards
    tool_messages = [m for m in calls[-1] if m.get("role") == "tool"]
    assert tool_messages and "__view__" not in tool_messages[-1]["content"]
