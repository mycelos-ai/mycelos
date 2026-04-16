"""Live integration test: Email connector with real Gmail account.

Requires .env.test with:
  GMAIL_USER=your@gmail.com
  GMAIL_PASSWORD=app-password-here

Run with: pytest -m integration tests/integration/test_email_live.py -v
"""

from __future__ import annotations

import os
import pytest

from mycelos.connectors.email_tools import (
    email_inbox,
    email_search,
    email_read,
    email_send,
    email_count,
    test_connection,
    detect_provider,
)


def _get_creds() -> dict | None:
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_PASSWORD")
    if not user or not pw:
        return None
    return {
        "imap_server": "imap.gmail.com",
        "imap_port": 993,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "email": user,
        "password": pw,
    }


@pytest.fixture
def gmail_creds():
    creds = _get_creds()
    if not creds:
        pytest.skip("GMAIL_USER/GMAIL_PASSWORD not set in .env.test")
    # Gate behind an explicit env var — live Gmail hits rate limits in
    # full-suite runs (SMTP banner can take 30+s once throttled).
    # Run explicitly with: MYCELOS_RUN_GMAIL_LIVE=1
    if os.environ.get("MYCELOS_RUN_GMAIL_LIVE") != "1":
        pytest.skip("Gmail live tests gated — set MYCELOS_RUN_GMAIL_LIVE=1 to run")
    return creds


@pytest.mark.integration
class TestGmailConnection:
    """Test real IMAP/SMTP connection to Gmail."""

    def test_connection_ok(self, gmail_creds):
        result = test_connection(gmail_creds)
        assert result["imap"] is True, f"IMAP failed: {result.get('imap_error')}"
        assert result["smtp"] is True, f"SMTP failed: {result.get('smtp_error')}"
        assert result["inbox_count"] >= 0

    def test_provider_detection(self, gmail_creds):
        provider = detect_provider(gmail_creds["email"])
        assert provider == "gmail"


@pytest.mark.integration
class TestGmailRead:
    """Test reading emails from real Gmail inbox."""

    def test_inbox_returns_messages(self, gmail_creds):
        messages = email_inbox(gmail_creds, limit=5)
        assert isinstance(messages, list)
        # Should have at least some messages
        if messages:
            msg = messages[0]
            assert "from" in msg
            assert "subject" in msg
            assert "date" in msg
            assert "body" in msg

    def test_inbox_unread_only(self, gmail_creds):
        messages = email_inbox(gmail_creds, limit=5, unread_only=True)
        assert isinstance(messages, list)

    def test_email_count(self, gmail_creds):
        result = email_count(gmail_creds)
        assert "total" in result
        assert "unread" in result
        assert result["total"] >= 0
        assert result["unread"] >= 0

    def test_search_returns_results(self, gmail_creds):
        # Search for something that likely exists
        results = email_search(gmail_creds, query="Google", limit=3)
        assert isinstance(results, list)

    def test_read_first_message(self, gmail_creds):
        messages = email_inbox(gmail_creds, limit=1)
        if messages:
            msg_id = messages[0].get("id")
            if msg_id:
                full = email_read(gmail_creds, message_id=msg_id)
                assert "from" in full
                assert "body" in full


@pytest.mark.integration
class TestGmailSend:
    """Test sending emails via real Gmail SMTP."""

    def test_send_to_self(self, gmail_creds):
        """Send a test email to the same account."""
        result = email_send(
            gmail_creds,
            to=gmail_creds["email"],
            subject="Mycelos Integration Test",
            body="This is an automated test email from Mycelos. You can delete it.",
        )
        assert result["status"] == "sent"
        assert result["to"] == gmail_creds["email"]


@pytest.mark.integration
class TestEmailWorkflowE2E:
    """Test the email-digest workflow concept end-to-end."""

    def test_digest_flow(self, gmail_creds):
        """Simulate email-digest: count → inbox → summarize."""
        # Step 1: Count
        count = email_count(gmail_creds)
        assert count["total"] >= 0

        # Step 2: Get unread
        unread = email_inbox(gmail_creds, unread_only=True, limit=10)
        assert isinstance(unread, list)

        # Step 3: Read first unread (if any)
        if unread:
            msg_id = unread[0].get("id")
            if msg_id:
                full = email_read(gmail_creds, message_id=msg_id)
                assert full.get("body") is not None

        # Step 4: The WorkflowAgent would now summarize — we just verify data is there
        # This proves the email tools work end-to-end with real Gmail
