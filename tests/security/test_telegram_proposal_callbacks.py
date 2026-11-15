"""Telegram approval-card buttons are fail-closed (Package 8, slice 5 follow-up).

A button press names only an action and a proposal id. It must never apply a
change for a user outside the allowlist, for another user's card, or with an
action the card does not offer.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mycelos.knowledge.proposals import correct_card


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-telegram-proposals"
        a = App(Path(tmp))
        a.initialize()
        a._llm = None
        yield a


@pytest.fixture
def tg(app, monkeypatch):
    from mycelos.channels import telegram
    monkeypatch.setattr(telegram, "_app", app)
    monkeypatch.setattr(telegram, "_allowed_users", {42, 43})
    return telegram


def _card(app, owner):
    kb = app.knowledge_base
    a = kb.create_topic("A")
    b = kb.create_topic("B")
    path = kb.write(title="Notiz", content="x", topic=a)
    return path, a, correct_card(app, path, b, user_id=owner)["data"]["proposal"]


def _press(tg, user_id, data):
    cb = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id), data=data, answer=AsyncMock(),
        message=SimpleNamespace(text="Karte", edit_text=AsyncMock(), edit_reply_markup=AsyncMock()))
    asyncio.run(tg.handle_proposal_callback(cb))
    return cb


def _parent(app, path):
    return app.knowledge_base._indexer.get_note_meta(path)["parent_path"]


def test_user_outside_allowlist_changes_nothing(app, tg):
    path, before, pid = _card(app, "telegram:99")
    _press(tg, 99, f"prop:accept:{pid}")
    assert _parent(app, path) == before
    assert app.storage.fetchone("SELECT status FROM view_proposals WHERE id=?", (pid,))["status"] == "pending"


def test_empty_allowlist_denies(app, tg, monkeypatch):
    monkeypatch.setattr(tg, "_allowed_users", set())
    path, before, pid = _card(app, "telegram:42")
    _press(tg, 42, f"prop:accept:{pid}")
    assert _parent(app, path) == before


def test_another_users_card_is_refused(app, tg):
    path, before, pid = _card(app, "telegram:42")
    _press(tg, 43, f"prop:accept:{pid}")
    assert _parent(app, path) == before


def test_web_card_cannot_be_pressed_from_telegram(app, tg):
    path, before, pid = _card(app, "default")
    _press(tg, 42, f"prop:accept:{pid}")
    assert _parent(app, path) == before


def test_action_outside_the_card_is_refused(app, tg):
    path, before, pid = _card(app, "telegram:42")
    _press(tg, 42, f"prop:approve:{pid}")
    assert _parent(app, path) == before
    row = app.storage.fetchone("SELECT status FROM view_proposals WHERE id=?", (pid,))
    assert row["status"] == "pending"
