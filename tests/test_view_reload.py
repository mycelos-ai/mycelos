"""Views and approval cards survive a reload; Telegram answers cards with buttons.

Follow-ups of Package 8 slices 4 and 5
(docs/superpowers/specs/2026-W45-generative-ui-design.md).
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mycelos.chat.events import ChatEvent
from mycelos.knowledge.proposals import ProposalService, correct_card
from mycelos.sessions.store import SessionStore


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-view-reload"
        a = App(Path(tmp))
        a.initialize()
        a._llm = None
        yield a


def _meta(app, path):
    return app.knowledge_base._indexer.get_note_meta(path)


def _llm(content=None, tool=None):
    mock = MagicMock()
    mock.content = content
    mock.total_tokens = 20
    mock.model = "test-model"
    mock.cost = 0.0
    mock.prompt_tokens = 15
    mock.completion_tokens = 5
    mock.stop_reason = "tool_use" if tool else "end_turn"
    mock.tool_calls = [{"id": "tc-reload-1", "function": {
        "name": tool[0], "arguments": json.dumps(tool[1])}}] if tool else None
    return mock


def _misfiled(app, user_id="default"):
    kb = app.knowledge_base
    a = kb.create_topic("Projekte")
    b = kb.create_topic("Müller")
    path = kb.write(title="Müller Angebot", content="x", topic=a)
    return path, b, correct_card(app, path, b, user_id=user_id)


# ---- session store ---------------------------------------------------------------


class TestSessionStore:
    def test_views_are_kept_in_order_but_not_resumed(self, tmp_path):
        store = SessionStore(tmp_path)
        sid = store.create_session()
        store.append_message(sid, "user", "Was ist offen?")
        store.append_view(sid, {"type": "timeline", "title": "Offen", "data": {}})
        store.append_message(sid, "assistant", "Siehe Zeitleiste.")

        transcript = store.load_transcript(sid)
        assert [r["type"] for r in transcript] == ["message", "view", "message"]
        assert transcript[1]["view"]["type"] == "timeline"
        # Resume for the model sees only messages.
        assert [m["role"] for m in store.load_messages(sid)] == ["user", "assistant"]
        # A view is not counted as a message in the sidebar.
        assert store.list_sessions()[0]["message_count"] == 2

    def test_missing_session_has_no_transcript(self, tmp_path):
        assert SessionStore(tmp_path).load_transcript("nope") == []


def test_chat_turn_records_the_card_between_question_and_answer(app):
    from mycelos.chat.service import ChatService
    app._llm = MagicMock()
    service = ChatService(app)
    session_id = service.create_session()
    calls: list = []

    def complete(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return _llm(tool=("note_write", {"title": "Onboarding Checkliste",
                                             "content": "1. Vertrag"}))
        return _llm(content="Gespeichert.")

    with patch.object(app.llm, "complete", side_effect=complete):
        service.handle_message("Merk dir: Onboarding als Checkliste", session_id)

    transcript = app.session_store.load_transcript(session_id)
    kinds = [r.get("role") if r["type"] == "message" else r["view"]["type"] for r in transcript]
    assert kinds.index("user") < kinds.index("approval_card") < kinds.index("assistant")
    assert all(m["type"] == "message" for m in app.session_store.load_messages(session_id))


# ---- proposal state ----------------------------------------------------------------


class TestProposalState:
    def test_pending_resolved_expired_unknown(self, app):
        _path, _b, card = _misfiled(app)
        pid = card["data"]["proposal"]
        service = ProposalService(app)
        assert service.state(pid) == {"status": "pending"}

        service.resolve(pid, "accept")
        state = service.state(pid)
        assert state["status"] == "resolved" and state["action"] == "accept"
        assert state["result"]["status"] == "moved"
        assert "payload" not in state

        assert service.state("f" * 32) == {"status": "unknown"}
        assert service.state("not-an-id") == {"status": "unknown"}

    def test_expired(self, app):
        _path, _b, card = _misfiled(app)
        pid = card["data"]["proposal"]
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        app.storage.execute("UPDATE view_proposals SET created_at=? WHERE id=?", (old, pid))
        assert ProposalService(app).state(pid) == {"status": "expired"}

    def test_other_users_card_is_unknown(self, app):
        _path, _b, card = _misfiled(app, user_id="telegram:42")
        assert ProposalService(app, "default").state(card["data"]["proposal"]) == {"status": "unknown"}


# ---- reload API --------------------------------------------------------------------


class TestReloadAPI:
    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        from mycelos.gateway.server import create_app
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-view-reload-api"
            from mycelos.app import App
            from mycelos.setup import web_init
            a = App(data_dir)
            a.initialize()
            web_init(a, api_key="sk-ant-api03-FAKETESTKEYVIEWRELOAD")
            fastapi_app = create_app(data_dir, no_scheduler=True,
                                     host="0.0.0.0", allow_insecure_bind=True)
            yield TestClient(fastapi_app)

    def _session_with(self, client, *views):
        app = client.app.state.mycelos
        store = app.session_store
        sid = store.create_session()
        store.append_message(sid, "user", "Frage")
        for view in views:
            store.append_view(sid, view)
        store.append_message(sid, "assistant", "Antwort")
        return sid

    def test_views_come_back_in_place(self, client):
        sid = self._session_with(client, {"type": "timeline", "title": "T", "data": {}})
        res = client.get(f"/api/sessions/{sid}/messages")
        assert res.status_code == 200
        msgs = res.json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "view", "assistant"]
        assert msgs[1]["view"]["type"] == "timeline"
        assert "state" not in msgs[1]

    def test_approval_card_carries_its_current_state(self, client):
        app = client.app.state.mycelos
        _path, _b, card = _misfiled(app)
        pid = card["data"]["proposal"]
        sid = self._session_with(client, card)

        state = client.get(f"/api/sessions/{sid}/messages").json()["messages"][1]["state"]
        assert state == {"status": "pending"}

        assert client.post(f"/api/proposals/{pid}/resolve",
                           json={"action": "accept"}).status_code == 200
        state = client.get(f"/api/sessions/{sid}/messages").json()["messages"][1]["state"]
        assert state["status"] == "resolved" and state["action"] == "accept"

    def test_another_users_card_reloads_locked(self, client):
        app = client.app.state.mycelos
        _path, _b, card = _misfiled(app, user_id="telegram:42")
        sid = self._session_with(client, card)
        state = client.get(f"/api/sessions/{sid}/messages").json()["messages"][1]["state"]
        assert state == {"status": "unknown"}


# ---- Telegram buttons --------------------------------------------------------------


def _card_event(card):
    return ChatEvent(type="widget", data={"widget": card})


class TestTelegramCards:
    def test_split_and_keyboard(self, app):
        from mycelos.channels import telegram
        _path, _b, card = _misfiled(app)
        text = ChatEvent(type="text", data={"content": "Hier die Karte."})
        rest, cards = telegram._split_approval_cards([_card_event(card), text])
        assert rest == [text] and cards == [card]

        keyboard = telegram._build_proposal_keyboard(card)
        buttons = [row[0] for row in keyboard.inline_keyboard]
        assert [b.text for b in buttons] == [a["label"] for a in card["data"]["actions"]]
        for button in buttons:
            # Telegram limits callback data to 64 bytes.
            assert len(button.callback_data.encode()) <= 64
            action, pid = telegram._parse_proposal_callback(button.callback_data)
            assert pid == card["data"]["proposal"]

        body = telegram._approval_card_text(card)
        assert card["title"] in body
        # The numbered action list is replaced by the buttons.
        assert card["data"]["actions"][-1]["label"] not in body

    @pytest.mark.parametrize("data", [None, "", "prop", "prop::x", "prop:a:", "perm:a:b", "prop:a:b:c"])
    def test_bad_callback_data(self, data):
        from mycelos.channels import telegram
        assert telegram._parse_proposal_callback(data) is None

    def test_send_events_puts_buttons_on_the_card(self, app):
        from mycelos.channels import telegram
        _path, _b, card = _misfiled(app)
        message = SimpleNamespace(answer=AsyncMock())
        asyncio.run(telegram._send_events(message, [
            _card_event(card), ChatEvent(type="text", data={"content": "Bitte bestätigen."})]))
        first, second = message.answer.await_args_list
        assert first.kwargs["reply_markup"] is not None
        assert "Bitte bestätigen." in second.args[0]

    def test_result_text_matches_web_wording(self):
        from mycelos.channels import telegram
        from mycelos.i18n import t
        assert telegram._proposal_result_text({"status": "moved", "moved": 3}) == \
            t("web.views.result.moved", count=3)
        assert telegram._proposal_result_text({"status": "attached", "import": "not_started"}) == \
            t("web.views.result.attached_not_started")
        assert telegram._proposal_error_text("weird") == t("web.views.error.failed")


def _callback(user_id, data, text="Karte"):
    message = SimpleNamespace(text=text, edit_text=AsyncMock(), edit_reply_markup=AsyncMock())
    return SimpleNamespace(from_user=SimpleNamespace(id=user_id), data=data,
                           message=message, answer=AsyncMock())


@pytest.fixture
def tg(app, monkeypatch):
    from mycelos.channels import telegram
    monkeypatch.setattr(telegram, "_app", app)
    monkeypatch.setattr(telegram, "_allowed_users", {42})
    return telegram


def test_button_resolves_the_card(app, tg):
    path, target, card = _misfiled(app, user_id="telegram:42")
    cb = _callback(42, f"prop:accept:{card['data']['proposal']}")
    asyncio.run(tg.handle_proposal_callback(cb))
    assert _meta(app, path)["parent_path"] == target
    cb.message.edit_text.assert_awaited_once()
    assert cb.message.edit_text.await_args.kwargs["reply_markup"] is None

    # A second press is refused and drops the buttons.
    again = _callback(42, f"prop:accept:{card['data']['proposal']}")
    asyncio.run(tg.handle_proposal_callback(again))
    again.message.edit_reply_markup.assert_awaited_once()
    events = app.storage.fetchall(
        "SELECT event_type FROM audit_events WHERE event_type='proposal.resolved'")
    assert len(events) == 1
