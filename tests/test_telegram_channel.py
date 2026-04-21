"""Tests for Telegram Channel adapter."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.channels.telegram import (
    _render_events,
    _split_message,
)
from mycelos.chat.events import (
    agent_event,
    done_event,
    error_event,
    step_progress_event,
    system_response_event,
    text_event,
)


# --- Event rendering ---

def test_render_text_event():
    events = [agent_event("Mycelos"), text_event("Hello from Telegram!")]
    result = _render_events(events)
    assert "Hello from Telegram!" in result


def test_render_error_event():
    events = [error_event("Something broke")]
    result = _render_events(events)
    assert "Something broke" in result


def test_render_step_progress():
    """Step progress is now silent — collected in footer as tool count."""
    events = [step_progress_event("search_web", "done")]
    result = _render_events(events)
    # Step progress no longer renders as separate lines — just collected for footer
    assert result == "" or "search_web" not in result


def test_render_done_with_tokens():
    events = [text_event("Response"), done_event(tokens=150, model="claude-sonnet-4-6")]
    result = _render_events(events)
    assert "150" in result
    assert "claude-sonnet" in result


def test_render_system_response():
    events = [system_response_event("Config info")]
    result = _render_events(events)
    assert "Config info" in result


def test_render_empty():
    assert _render_events([]) == ""


# --- Message splitting ---

def test_split_short_message():
    chunks = _split_message("Hello", 4000)
    assert chunks == ["Hello"]


def test_split_long_message():
    long_text = "A" * 5000
    chunks = _split_message(long_text, 4000)
    assert len(chunks) >= 2
    assert all(len(c) <= 4000 for c in chunks)


def test_split_at_newline():
    text = "Line 1\n" * 600  # ~4200 chars
    chunks = _split_message(text, 4000)
    assert len(chunks) >= 2
    # Should split at a newline, not mid-line
    assert chunks[0].endswith("\n") or chunks[0].endswith("Line 1")


def test_split_preserves_content():
    text = "Word " * 1000  # ~5000 chars
    chunks = _split_message(text, 4000)
    combined = "".join(chunks)
    # Should have all the content (minus possible stripped whitespace)
    assert combined.count("Word") >= 900


# --- Telegram recipe ---

def test_telegram_recipe_exists():
    from mycelos.connectors.mcp_recipes import get_recipe
    r = get_recipe("telegram")
    assert r is not None
    assert r.name == "Telegram Bot"
    assert r.transport == "channel"
    assert r.requires_node is False
    assert len(r.credentials) == 1
    assert "BOT_TOKEN" in r.credentials[0]["env_var"]


# --- Setup function ---

def test_setup_telegram():
    """setup_telegram should create a bot instance."""
    from mycelos.channels.telegram import setup_telegram

    mock_service = MagicMock()
    bot = setup_telegram("123456:ABC-DEF", mock_service)
    assert bot is not None


def test_get_bot_after_setup():
    from mycelos.channels.telegram import setup_telegram, get_bot

    mock_service = MagicMock()
    setup_telegram("123456:TEST-TOKEN", mock_service)
    bot = get_bot()
    assert bot is not None


def test_setup_with_allowlist():
    """setup_telegram with allowed_users restricts access."""
    from mycelos.channels.telegram import setup_telegram, is_user_allowed, get_allowed_users

    mock_service = MagicMock()
    setup_telegram("123456:ALLOW-TEST", mock_service, allowed_users=[42, 100])
    assert is_user_allowed(42)
    assert is_user_allowed(100)
    assert not is_user_allowed(999)
    assert get_allowed_users() == {42, 100}


def test_setup_empty_allowlist_blocks_all():
    """Empty allowlist means NO users are allowed (fail-closed)."""
    from mycelos.channels.telegram import setup_telegram, is_user_allowed

    mock_service = MagicMock()
    setup_telegram("123456:OPEN-TEST", mock_service, allowed_users=[])
    assert not is_user_allowed(42)
    assert not is_user_allowed(999)


def test_setup_with_webhook_secret():
    """setup_telegram with webhook_secret enables verification."""
    from mycelos.channels.telegram import setup_telegram, verify_webhook_secret

    mock_service = MagicMock()
    setup_telegram("123456:SECRET-TEST", mock_service, webhook_secret="my-secret")
    assert verify_webhook_secret("my-secret")
    assert not verify_webhook_secret("wrong-secret")


# --- Connector CLI entry ---

def test_telegram_in_connector_dict():
    """Telegram should be in the CONNECTORS dict."""
    from mycelos.cli.connector_cmd import CONNECTORS
    assert "telegram" in CONNECTORS
    assert CONNECTORS["telegram"]["category"] == "channel"
    assert CONNECTORS["telegram"]["requires_key"] is True
    assert CONNECTORS["telegram"]["setup_type"] == "telegram"


# --- Webhook registration ---

def test_register_telegram_webhook():
    """_register_telegram_webhook calls Telegram API via the single-
    container urllib path (no proxy configured in this test)."""
    from mycelos.gateway.server import _register_telegram_webhook
    import json as _json

    mock_resp = MagicMock()
    mock_resp.read.return_value = _json.dumps({"ok": True, "description": "Webhook set"}).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = _register_telegram_webhook(
            bot_token="123456:ABC",
            webhook_url="https://example.com/telegram/webhook",
            webhook_secret="test-secret",
        )

    assert result is True
    mock_urlopen.assert_called_once()
    req = mock_urlopen.call_args[0][0]
    # Token substituted into the URL
    assert "123456:ABC" in req.full_url
    body = _json.loads(req.data)
    assert body["url"] == "https://example.com/telegram/webhook"
    assert body["secret_token"] == "test-secret"


def test_register_telegram_webhook_failure():
    """Webhook registration handles API errors gracefully."""
    from mycelos.gateway.server import _register_telegram_webhook

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"ok": False, "description": "Bad token"}

    with patch("httpx.post", return_value=mock_resp):
        result = _register_telegram_webhook(
            bot_token="invalid",
            webhook_url="https://example.com/telegram/webhook",
        )

    assert result is False


def test_register_telegram_webhook_network_error():
    """Webhook registration handles network errors."""
    from mycelos.gateway.server import _register_telegram_webhook

    with patch("httpx.post", side_effect=Exception("Connection refused")):
        result = _register_telegram_webhook(
            bot_token="123456:ABC",
            webhook_url="https://example.com/telegram/webhook",
        )

    assert result is False


# --- Channel config in DB (NixOS State) ---

@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-telegram"
        a = App(Path(tmp))
        a.initialize()
        yield a


def test_channel_config_write_and_read(app):
    """Channel config written to channels table can be read back."""
    from mycelos.channels.telegram import load_channel_config

    app.storage.execute(
        """INSERT INTO channels (id, channel_type, mode, status, config, allowed_users)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("telegram", "telegram", "polling", "active", "{}", "[42, 100]"),
    )

    cfg = load_channel_config(app.storage)
    assert cfg is not None
    assert cfg["mode"] == "polling"
    assert cfg["allowed_users"] == [42, 100]


def test_channel_config_not_found(app):
    """No channel config returns None."""
    from mycelos.channels.telegram import load_channel_config
    assert load_channel_config(app.storage) is None


def test_channel_config_webhook_mode(app):
    """Webhook mode stores URL and secret in config."""
    from mycelos.channels.telegram import load_channel_config

    config = json.dumps({"webhook_url": "https://example.com", "webhook_secret": "s3cret"})
    app.storage.execute(
        """INSERT INTO channels (id, channel_type, mode, status, config, allowed_users)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("telegram", "telegram", "webhook", "active", config, "[]"),
    )

    cfg = load_channel_config(app.storage)
    assert cfg["mode"] == "webhook"
    assert cfg["config"]["webhook_url"] == "https://example.com"
    assert cfg["config"]["webhook_secret"] == "s3cret"


def test_channel_in_nixos_snapshot(app):
    """Channel config is part of NixOS-style state snapshots."""
    app.storage.execute(
        """INSERT INTO channels (id, channel_type, mode, status, config, allowed_users)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("telegram", "telegram", "polling", "active", "{}", "[42]"),
    )

    snapshot = app.state_manager.snapshot()
    assert "channels" in snapshot
    assert "telegram" in snapshot["channels"]
    assert snapshot["channels"]["telegram"]["mode"] == "polling"
    assert snapshot["channels"]["telegram"]["allowed_users"] == [42]


def test_channel_survives_snapshot_restore(app):
    """Channel config survives snapshot → restore cycle."""
    app.storage.execute(
        """INSERT INTO channels (id, channel_type, mode, status, config, allowed_users)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("telegram", "telegram", "polling", "active",
         json.dumps({"webhook_secret": "abc"}), json.dumps([42, 100])),
    )

    snapshot = app.state_manager.snapshot()

    # Wipe and restore
    app.storage.execute("DELETE FROM channels")
    from mycelos.channels.telegram import load_channel_config
    assert load_channel_config(app.storage) is None

    app.state_manager.restore(snapshot)

    cfg = load_channel_config(app.storage)
    assert cfg is not None
    assert cfg["mode"] == "polling"
    assert cfg["allowed_users"] == [42, 100]
    assert cfg["config"]["webhook_secret"] == "abc"


def test_channel_rollback(app):
    """Channel config is rolled back with config generation."""
    # Generation 1: no telegram
    gen1 = app.config.apply_from_state(app.state_manager, "before telegram", "test")

    # Add telegram channel
    app.storage.execute(
        """INSERT INTO channels (id, channel_type, mode, status, config, allowed_users)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("telegram", "telegram", "polling", "active", "{}", "[42]"),
    )
    app.config.apply_from_state(app.state_manager, "with telegram", "test")

    from mycelos.channels.telegram import load_channel_config
    assert load_channel_config(app.storage) is not None

    # Rollback to gen1
    app.config.rollback(to_generation=gen1, state_manager=app.state_manager)
    assert load_channel_config(app.storage) is None


def test_start_polling_without_bot():
    """start_polling returns None if bot not initialized."""
    from mycelos.channels.telegram import start_polling
    # Reset state
    import mycelos.channels.telegram as tg
    old_bot = tg._bot
    tg._bot = None
    try:
        result = start_polling()
        assert result is None
    finally:
        tg._bot = old_bot
