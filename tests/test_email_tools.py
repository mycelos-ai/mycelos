"""Tests for email connector — IMAP/SMTP tools."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from mycelos.app import App


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-email"
        a = App(Path(tmp))
        a.initialize()
        yield a


# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------

class TestProviderPresets:
    def test_gmail_preset(self):
        from mycelos.connectors.email_tools import PROVIDER_PRESETS
        gmail = PROVIDER_PRESETS["gmail"]
        assert gmail["imap_server"] == "imap.gmail.com"
        assert gmail["smtp_server"] == "smtp.gmail.com"

    def test_outlook_preset(self):
        from mycelos.connectors.email_tools import PROVIDER_PRESETS
        outlook = PROVIDER_PRESETS["outlook"]
        assert "office365" in outlook["imap_server"]

    def test_icloud_preset(self):
        from mycelos.connectors.email_tools import PROVIDER_PRESETS
        icloud = PROVIDER_PRESETS["icloud"]
        assert "mail.me.com" in icloud["imap_server"]


# ---------------------------------------------------------------------------
# IMAP tools
# ---------------------------------------------------------------------------

class TestEmailInbox:
    def test_inbox_returns_messages(self):
        from mycelos.connectors.email_tools import email_inbox

        mock_imap = MagicMock()
        mock_imap.select.return_value = ("OK", [b"5"])
        mock_imap.search.return_value = ("OK", [b"1 2 3"])
        # Mock fetch for 3 messages
        mock_imap.fetch.side_effect = [
            ("OK", [(b"1 (RFC822)", b"From: alice@test.com\r\nSubject: Hello\r\nDate: Mon, 30 Mar 2026 10:00:00 +0000\r\n\r\nHi there")]),
            ("OK", [(b"2 (RFC822)", b"From: bob@test.com\r\nSubject: Meeting\r\nDate: Mon, 30 Mar 2026 11:00:00 +0000\r\n\r\nSee you")]),
            ("OK", [(b"3 (RFC822)", b"From: carol@test.com\r\nSubject: Report\r\nDate: Mon, 30 Mar 2026 12:00:00 +0000\r\n\r\nAttached")]),
        ]

        with patch("mycelos.connectors.email_tools._get_imap_connection", return_value=mock_imap):
            result = email_inbox(creds={"imap_server": "test", "imap_port": 993, "email": "me@test.com", "password": "pw"}, limit=3)

        assert len(result) == 3
        assert result[0]["from"] == "alice@test.com"
        assert result[0]["subject"] == "Hello"

    def test_inbox_unread_only(self):
        from mycelos.connectors.email_tools import email_inbox

        mock_imap = MagicMock()
        mock_imap.select.return_value = ("OK", [b"10"])
        mock_imap.search.return_value = ("OK", [b"8 9 10"])
        mock_imap.fetch.side_effect = [
            ("OK", [(b"8 (RFC822)", b"From: a@t.com\r\nSubject: Unread1\r\nDate: Mon, 30 Mar 2026 10:00:00 +0000\r\n\r\nBody")]),
            ("OK", [(b"9 (RFC822)", b"From: b@t.com\r\nSubject: Unread2\r\nDate: Mon, 30 Mar 2026 11:00:00 +0000\r\n\r\nBody")]),
            ("OK", [(b"10 (RFC822)", b"From: c@t.com\r\nSubject: Unread3\r\nDate: Mon, 30 Mar 2026 12:00:00 +0000\r\n\r\nBody")]),
        ]

        with patch("mycelos.connectors.email_tools._get_imap_connection", return_value=mock_imap):
            result = email_inbox(creds={"imap_server": "test", "imap_port": 993, "email": "me@test.com", "password": "pw"}, unread_only=True)

        # Should have used UNSEEN search
        mock_imap.search.assert_called_with(None, "UNSEEN")


class TestEmailSearch:
    def test_search_by_subject(self):
        from mycelos.connectors.email_tools import email_search

        mock_imap = MagicMock()
        mock_imap.select.return_value = ("OK", [b"5"])
        mock_imap.search.return_value = ("OK", [b"3"])
        mock_imap.fetch.return_value = ("OK", [(b"3 (RFC822)", b"From: boss@co.com\r\nSubject: Q4 Report\r\nDate: Mon, 30 Mar 2026 10:00:00 +0000\r\n\r\nPlease review")])

        with patch("mycelos.connectors.email_tools._get_imap_connection", return_value=mock_imap):
            result = email_search(creds={"imap_server": "t", "imap_port": 993, "email": "me@t.com", "password": "pw"}, query="Q4 Report")

        assert len(result) >= 1
        assert "Q4 Report" in result[0]["subject"]


class TestEmailSend:
    def test_send_email(self):
        from mycelos.connectors.email_tools import email_send

        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)

        with patch("mycelos.connectors.email_tools._get_smtp_connection", return_value=mock_smtp):
            result = email_send(
                creds={"smtp_server": "t", "smtp_port": 587, "email": "me@t.com", "password": "pw"},
                to="friend@t.com", subject="Hi", body="Hello!"
            )

        assert result["status"] == "sent"
        mock_smtp.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

class TestToolRegistration:
    def test_email_tools_registered(self):
        from mycelos.tools.registry import ToolRegistry
        ToolRegistry.reset()
        ToolRegistry._ensure_initialized()
        assert ToolRegistry.get_schema("email_inbox") is not None
        assert ToolRegistry.get_schema("email_search") is not None
        assert ToolRegistry.get_schema("email_read") is not None
        assert ToolRegistry.get_schema("email_send") is not None


# ---------------------------------------------------------------------------
# Connector recipe
# ---------------------------------------------------------------------------

class TestConnectorRecipe:
    def test_email_recipe_exists(self):
        from mycelos.connectors.mcp_recipes import get_recipe
        recipe = get_recipe("email")
        assert recipe is not None
        assert "email" in recipe.id
        assert "IMAP" in recipe.description or "email" in recipe.description.lower()

    def test_gmail_recipe_exists(self):
        from mycelos.connectors.mcp_recipes import get_recipe
        recipe = get_recipe("gmail")
        assert recipe is not None


# ---------------------------------------------------------------------------
# Suggested workflows
# ---------------------------------------------------------------------------

class TestSuggestedWorkflows:
    def test_email_digest_workflow_yaml(self):
        yaml_path = Path(__file__).parent.parent / "artifacts" / "workflows" / "email-digest.yaml"
        assert yaml_path.exists()

        import yaml
        data = yaml.safe_load(yaml_path.read_text())
        assert data["name"] == "email-digest"
        # Should only have read permissions
        assert "email_inbox" in data.get("allowed_tools", []) or "email.read" in str(data.get("scope", []))
        assert "email_send" not in data.get("allowed_tools", [])


class TestPasswordSanitization:
    """Gmail App-Passwords are shown as `xxxx xxxx xxxx xxxx` and users often
    paste them with quotes. Google only accepts the bare 16-char code."""

    def test_strips_double_quotes(self):
        from mycelos.connectors.email_tools import _sanitize_password
        assert _sanitize_password('"abcd efgh ijkl mnop"') == "abcdefghijklmnop"

    def test_strips_single_quotes(self):
        from mycelos.connectors.email_tools import _sanitize_password
        assert _sanitize_password("'abcd efgh ijkl mnop'") == "abcdefghijklmnop"

    def test_strips_internal_spaces(self):
        from mycelos.connectors.email_tools import _sanitize_password
        assert _sanitize_password("abcd efgh ijkl mnop") == "abcdefghijklmnop"

    def test_strips_leading_trailing_whitespace(self):
        from mycelos.connectors.email_tools import _sanitize_password
        assert _sanitize_password("  lgqq  ") == "lgqq"

    def test_already_clean_passthrough(self):
        from mycelos.connectors.email_tools import _sanitize_password
        assert _sanitize_password("lgqqqjqerghxrpzc") == "lgqqqjqerghxrpzc"

    def test_empty_passthrough(self):
        from mycelos.connectors.email_tools import _sanitize_password
        assert _sanitize_password("") == ""
        assert _sanitize_password(None) is None
