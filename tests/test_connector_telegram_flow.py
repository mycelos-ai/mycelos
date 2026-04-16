"""Integration test: Telegram connector setup flow end-to-end.

Tests the full user journey:
1. /connector add telegram (no token) → setup instructions
2. /connector add telegram <token> → configured + next steps
3. /connector test telegram → live API check
4. /connector add telegram <token> (already active) → already active message
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from mycelos.app import App
from mycelos.chat.slash_commands import handle_slash_command


def _result_text(result) -> str:
    """Extract text from slash command result (str or ChatEvent list)."""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = []
        for event in result:
            data = event.data if hasattr(event, "data") else {}
            if "content" in data:
                parts.append(data["content"])
        return "\n".join(parts)
    return str(result)


def _has_action(result, label_contains: str) -> bool:
    """Check if result contains a suggested action with matching label."""
    if not isinstance(result, list):
        return False
    for event in result:
        if hasattr(event, "type") and event.type == "suggested-actions":
            for action in event.data.get("actions", []):
                if label_contains.lower() in action.get("label", "").lower():
                    return True
    return False


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-telegram-flow"
        a = App(Path(tmp))
        a.initialize()
        yield a


class TestTelegramSetupFlow:
    """Full Telegram connector setup journey."""

    def test_step1_no_token_shows_instructions(self, app):
        """User types /connector add telegram without token → instructions."""
        result = handle_slash_command(app, "/connector add telegram")
        text = _result_text(result)
        assert "Telegram Bot" in text
        assert "token" in text.lower()
        assert "@BotFather" in text or "BotFather" in text

    def test_step1_has_prefill_button(self, app):
        """Instructions include a prefill button for token pasting."""
        result = handle_slash_command(app, "/connector add telegram")
        assert _has_action(result, "token")

    def test_step2_with_token_configures(self, app):
        """User pastes token → configured + mentions allowlist/restart."""
        result = handle_slash_command(app, "/connector add telegram 123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
        text = _result_text(result)
        assert "configured" in text.lower() or "Telegram Bot" in text
        assert "allowlist" in text.lower() or "fail-closed" in text.lower()

    def test_step2_token_stored_encrypted(self, app):
        """Token is actually stored in credentials."""
        handle_slash_command(app, "/connector add telegram 123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
        cred = app.credentials.get_credential("telegram")
        assert cred is not None
        assert cred.get("api_key") == "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"

    def test_step2_channel_config_created(self, app):
        """Telegram channel entry created in channels table."""
        handle_slash_command(app, "/connector add telegram 123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
        row = app.storage.fetchone("SELECT * FROM channels WHERE id = 'telegram'")
        assert row is not None
        assert row["channel_type"] == "telegram"
        assert row["status"] == "active"

    def test_step3_test_no_token_shows_setup(self, app):
        """Test without token → tells user to add one."""
        result = handle_slash_command(app, "/connector test telegram")
        text = _result_text(result)
        assert "no token" in text.lower() or "not found" in text.lower() or "token" in text.lower()

    def test_step3_test_with_invalid_token(self, app):
        """Test with invalid token → clear error."""
        handle_slash_command(app, "/connector add telegram invalid-token")
        # Mock the urllib call to simulate 401
        with patch("urllib.request.urlopen") as mock_url:
            mock_url.side_effect = Exception("HTTP Error 401: Unauthorized")
            result = handle_slash_command(app, "/connector test telegram")
            text = _result_text(result)
            assert "invalid" in text.lower() or "expired" in text.lower()

    def test_step3_test_with_valid_token(self, app):
        """Test with valid token → shows bot name."""
        handle_slash_command(app, "/connector add telegram 123456789:ABCdefGHIjklMNOpqrsTUVwxyz")

        import json
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "ok": True,
            "result": {"first_name": "TestBot", "username": "test_mycelos_bot"}
        }).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = handle_slash_command(app, "/connector test telegram")
            text = _result_text(result)
            assert "working" in text.lower()
            assert "TestBot" in text
            assert "test_mycelos_bot" in text
            assert "mycelos serve" in text

    def test_step4_already_active(self, app):
        """Adding telegram again when already active → already active message."""
        handle_slash_command(app, "/connector add telegram 123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
        result = handle_slash_command(app, "/connector add telegram")
        text = _result_text(result)
        assert "already active" in text.lower()

    def test_restart_suggested_for_telegram(self, app):
        """Telegram setup suggests /restart to activate the bot."""
        result = handle_slash_command(app, "/connector add telegram 123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
        text = _result_text(result)
        assert "restart" in text.lower()
