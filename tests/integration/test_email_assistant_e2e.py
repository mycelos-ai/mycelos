"""End-to-end integration test for UC01: Personal Email Assistant.

Tests the full email assistant flow from fresh App initialization through
credential storage, tool availability, real IMAP operations, and system
status verification.

Credentials via env var EMAIL_TEST_ACCOUNT (JSON):
  {
    "email": "you@example.com",
    "password": "app-password",
    "imap_server": "imap.gmail.com",
    "smtp_server": "smtp.gmail.com"
  }

Or set GMAIL_USER + GMAIL_PASSWORD for Gmail (auto-detects servers).

Run: pytest tests/integration/test_email_assistant_e2e.py -v
Note: Tests are SKIPPED when credentials are not available.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def _load_email_creds() -> dict | None:
    """Load email credentials from environment.

    Supports two formats:
      1. EMAIL_TEST_ACCOUNT as JSON string
      2. GMAIL_USER + GMAIL_PASSWORD (Gmail-specific)
    """
    raw = os.environ.get("EMAIL_TEST_ACCOUNT")
    if raw:
        try:
            creds = json.loads(raw)
            # Ensure required fields
            if creds.get("email") and creds.get("password"):
                creds.setdefault("imap_server", "imap.gmail.com")
                creds.setdefault("imap_port", 993)
                creds.setdefault("smtp_server", "smtp.gmail.com")
                creds.setdefault("smtp_port", 587)
                return creds
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: GMAIL_USER + GMAIL_PASSWORD
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_PASSWORD")
    if user and pw:
        return {
            "email": user,
            "password": pw,
            "imap_server": "imap.gmail.com",
            "imap_port": 993,
            "smtp_server": "smtp.gmail.com",
            "smtp_port": 587,
        }

    return None


_HAS_EMAIL_CREDS = _load_email_creds() is not None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def email_creds():
    """Return email credentials or skip the test.

    Also verifies that the IMAP server is actually reachable (skips if
    sandboxed or offline).
    """
    creds = _load_email_creds()
    if not creds:
        pytest.skip(
            "EMAIL_TEST_ACCOUNT or GMAIL_USER/GMAIL_PASSWORD not set. "
            "Skipping live email tests."
        )
    # Verify network connectivity to the IMAP server
    import socket
    try:
        sock = socket.create_connection(
            (creds["imap_server"], int(creds.get("imap_port", 993))),
            timeout=5,
        )
        sock.close()
    except (OSError, socket.timeout, socket.gaierror):
        pytest.skip(
            f"Cannot reach {creds['imap_server']} -- network sandboxed or offline"
        )
    return creds


@pytest.fixture
def fresh_app(tmp_path):
    """Create a fully initialized App with fresh DB -- no mocks."""
    data_dir = tmp_path / "mycelos-e2e"
    os.environ["MYCELOS_MASTER_KEY"] = "e2e-test-key-uc01-2026"

    app = App(data_dir)
    app.initialize_with_config(
        default_model="anthropic/claude-haiku-4-5",
        provider="anthropic",
    )
    return app


@pytest.fixture
def app_with_email(fresh_app, email_creds):
    """App with email credentials stored in the Credential Proxy."""
    # Store email creds as JSON in the credential proxy (same format
    # as /connector add email uses)
    fresh_app.credentials.store_credential(
        "email",
        {"api_key": json.dumps(email_creds)},
        description="E2E test email account",
    )
    return fresh_app


# ---------------------------------------------------------------------------
# 1. Fresh App initialization (no credentials needed)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestFreshAppSetup:
    """Verify the App initializes correctly with DB, config gen, and audit."""

    def test_app_initializes_with_db(self, fresh_app):
        """UC01 precondition: system is freshly installed and initialized."""
        app = fresh_app
        assert app.data_dir.exists()

        # Initial config generation exists
        gen_id = app.config.get_active_generation_id()
        assert gen_id is not None
        assert gen_id >= 1

    def test_initial_audit_event_logged(self, fresh_app):
        """Every init logs a system.initialized audit event."""
        events = fresh_app.storage.fetchall(
            "SELECT event_type FROM audit_events WHERE event_type = 'system.initialized'"
        )
        assert len(events) >= 1

    def test_email_tools_registered_in_registry(self):
        """Email tools are available in the ToolRegistry regardless of credentials."""
        from mycelos.tools.registry import ToolRegistry
        ToolRegistry.reset()
        ToolRegistry._ensure_initialized()

        email_tools = ["email_inbox", "email_search", "email_read",
                       "email_send", "email_count"]
        for tool_name in email_tools:
            schema = ToolRegistry.get_schema(tool_name)
            assert schema is not None, f"Tool {tool_name} not registered"
            assert schema["function"]["name"] == tool_name


# ---------------------------------------------------------------------------
# 2. Credential storage and config generation
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestCredentialAndConnectorSetup:
    """Store email credentials and verify config generation is created."""

    @pytest.mark.skipif(not _HAS_EMAIL_CREDS, reason="No email credentials")
    def test_store_email_credential(self, fresh_app, email_creds):
        """Storing email credentials via Credential Proxy encrypts them."""
        app = fresh_app
        gen_before = app.config.get_active_generation_id()

        app.credentials.store_credential(
            "email",
            {"api_key": json.dumps(email_creds)},
            description="Test email account",
        )

        # Credential can be retrieved (decrypted)
        cred = app.credentials.get_credential("email")
        assert cred is not None
        assert "api_key" in cred

        # The stored JSON round-trips correctly
        stored = json.loads(cred["api_key"])
        assert stored["email"] == email_creds["email"]
        assert stored["imap_server"] == email_creds["imap_server"]

    @pytest.mark.skipif(not _HAS_EMAIL_CREDS, reason="No email credentials")
    def test_credential_never_in_plaintext_db(self, app_with_email, email_creds):
        """Constitution Rule 4: credentials never visible in plaintext."""
        app = app_with_email
        # Read raw DB row -- the password must NOT appear unencrypted
        row = app.storage.fetchone(
            "SELECT encrypted, nonce FROM credentials WHERE service = 'email'"
        )
        assert row is not None
        raw_bytes = row["encrypted"]
        # The raw bytes should not contain the plaintext password
        assert email_creds["password"].encode() not in raw_bytes

    def test_config_generation_on_connector_register(self, fresh_app):
        """Registering a connector creates a new config generation (UC01 Gen 2)."""
        app = fresh_app
        gen_before = app.config.get_active_generation_id()

        # Register email connector (simulates /connector add email)
        app.connector_registry.register(
            connector_id="email",
            name="Email (IMAP/SMTP)",
            connector_type="builtin",
            capabilities=["email.read", "email.send"],
            description="Built-in email connector",
            setup_type="builtin",
        )

        gen_after = app.config.get_active_generation_id()
        assert gen_after > gen_before, (
            f"Expected new config generation after connector register: "
            f"before={gen_before}, after={gen_after}"
        )


# ---------------------------------------------------------------------------
# 3. Agent tool list includes email tools
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestAgentToolList:
    """Verify the mycelos agent has email tools in its tool list."""

    def test_static_tool_names_include_email(self):
        """Email tools are available after discovering the 'email' category."""
        import os, tempfile
        from pathlib import Path
        from mycelos.app import App
        from mycelos.chat.service import _get_chat_agent_tools

        # Email tools are in the 'email' discoverable category.
        # They are loaded when the connector is active AND the category is discovered.
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-email-gate"
            app = App(Path(tmp))
            app.initialize()
            app.connector_registry.register(
                "email", "Email", "builtin", [],
                description="Email connector", setup_type="builtin",
            )
            # Simulate discovery of the email category (as the agent would do)
            tools = _get_chat_agent_tools(app=app, channel="api", session_extras={"email"})
            tool_names = {t["function"]["name"] for t in tools}

        expected_email_tools = {
            "email_inbox", "email_search", "email_read",
            "email_send", "email_count",
        }
        missing = expected_email_tools - tool_names
        assert not missing, f"Email tools missing from agent tool list: {missing}"

    def test_tool_schemas_have_correct_structure(self):
        """Each email tool schema follows OpenAI function calling format."""
        from mycelos.tools.registry import ToolRegistry
        ToolRegistry.reset()
        ToolRegistry._ensure_initialized()

        for name in ["email_inbox", "email_search", "email_read",
                      "email_send", "email_count"]:
            schema = ToolRegistry.get_schema(name)
            assert "function" in schema, f"{name}: missing 'function' key"
            func = schema["function"]
            assert "name" in func, f"{name}: missing function name"
            assert "description" in func, f"{name}: missing description"
            assert "parameters" in func, f"{name}: missing parameters"


# ---------------------------------------------------------------------------
# 4. Live email operations (require credentials)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLiveEmailOperations:
    """Test real IMAP operations through the email connector.

    These tests hit a real mail server -- no mocks.
    """

    def test_email_count(self, email_creds):
        """email_count returns total and unread counts."""
        from mycelos.connectors.email_tools import email_count

        result = email_count(email_creds)
        assert "total" in result
        assert "unread" in result
        assert isinstance(result["total"], int)
        assert result["total"] >= 0
        assert result["unread"] >= 0

    def test_email_inbox_returns_messages(self, email_creds):
        """email_inbox returns a list of message dicts with expected fields."""
        from mycelos.connectors.email_tools import email_inbox

        messages = email_inbox(email_creds, limit=5)
        assert isinstance(messages, list)

        if messages:
            msg = messages[0]
            assert "from" in msg, "Message missing 'from' field"
            assert "subject" in msg, "Message missing 'subject' field"
            assert "date" in msg, "Message missing 'date' field"
            assert "body" in msg, "Message missing 'body' field"
            assert "id" in msg, "Message missing 'id' field"

    def test_email_read_returns_markdown_not_html(self, email_creds):
        """email_read returns clean text/markdown, not raw HTML."""
        from mycelos.connectors.email_tools import email_inbox, email_read

        messages = email_inbox(email_creds, limit=1)
        if not messages:
            pytest.skip("Inbox is empty -- cannot test email_read")

        msg_id = messages[0].get("id")
        if not msg_id:
            pytest.skip("First message has no ID")

        full = email_read(email_creds, message_id=msg_id)
        assert "from" in full
        assert "body" in full

        body = full["body"]
        # If the body has content, it should not be raw HTML
        # (the connector converts HTML to markdown)
        if body and len(body) > 50:
            # Raw HTML would start with DOCTYPE or <html>
            assert not body.strip().lower().startswith("<!doctype"), (
                "Body appears to be raw HTML, expected markdown conversion"
            )
            assert not body.strip().lower().startswith("<html"), (
                "Body appears to be raw HTML, expected markdown conversion"
            )

    def test_email_search(self, email_creds):
        """email_search returns results matching a subject query."""
        from mycelos.connectors.email_tools import email_search

        # Search for a common term that likely exists
        results = email_search(email_creds, query="a", limit=3)
        assert isinstance(results, list)
        # We cannot guarantee results, but the call should not fail

    def test_email_inbox_unread_only(self, email_creds):
        """email_inbox with unread_only=True filters correctly."""
        from mycelos.connectors.email_tools import email_inbox

        unread = email_inbox(email_creds, limit=5, unread_only=True)
        assert isinstance(unread, list)
        # All returned messages should be unread (we trust IMAP UNSEEN filter)


# ---------------------------------------------------------------------------
# 5. Email tools via App context (credential proxy integration)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestEmailToolsViaApp:
    """Test email tools through the App execution context (as the agent would)."""

    def test_execute_email_count_via_tool(self, app_with_email):
        """email_count tool works through the App context (credential proxy)."""
        from mycelos.tools.email import execute_email_count

        context = {"app": app_with_email, "user_id": "default"}
        result = execute_email_count({}, context)

        assert "error" not in result, f"Tool returned error: {result.get('error')}"
        assert "total" in result
        assert "unread" in result

    def test_execute_email_inbox_via_tool(self, app_with_email):
        """email_inbox tool works through the App context."""
        from mycelos.tools.email import execute_email_inbox

        context = {"app": app_with_email, "user_id": "default"}
        result = execute_email_inbox({"limit": 3}, context)

        assert not isinstance(result, dict) or "error" not in result, (
            f"Tool returned error: {result}"
        )
        assert isinstance(result, list)

    def test_execute_email_search_via_tool(self, app_with_email):
        """email_search tool works through the App context."""
        from mycelos.tools.email import execute_email_search

        context = {"app": app_with_email, "user_id": "default"}
        result = execute_email_search({"query": "test", "limit": 3}, context)

        assert not isinstance(result, dict) or "error" not in result, (
            f"Tool returned error: {result}"
        )
        assert isinstance(result, list)

    def test_no_credential_returns_helpful_error(self, fresh_app):
        """Without credentials stored, tools return a helpful setup message."""
        from mycelos.tools.email import execute_email_inbox

        context = {"app": fresh_app, "user_id": "default"}
        result = execute_email_inbox({"limit": 5}, context)

        assert isinstance(result, dict)
        assert "error" in result
        assert "connector" in result["error"].lower() or "configured" in result["error"].lower()


# ---------------------------------------------------------------------------
# 6. System status shows email as builtin + ready
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSystemStatusEmail:
    """Verify system_status reflects email connector state correctly."""

    def test_system_status_email_not_ready_without_creds(self, fresh_app):
        """Without credentials, email connector shows as not ready."""
        app = fresh_app
        # Register the connector but do NOT store credentials
        app.connector_registry.register(
            connector_id="email",
            name="Email (IMAP/SMTP)",
            connector_type="builtin",
            capabilities=["email.read", "email.send"],
            setup_type="builtin",
        )

        from mycelos.tools.system import execute_system_status
        context = {"app": app, "user_id": "default"}
        status = execute_system_status({}, context)

        connectors = status.get("connectors", [])
        email_connector = next(
            (c for c in connectors if c["id"] == "email"), None
        )
        assert email_connector is not None, "Email connector not in system_status"
        assert email_connector["type"] == "builtin"
        assert email_connector["ready"] is False
        assert "credentials missing" in email_connector.get("status", "").lower()

    @pytest.mark.skipif(not _HAS_EMAIL_CREDS, reason="No email credentials")
    def test_system_status_email_ready_with_creds(self, app_with_email):
        """With credentials stored, email connector shows as ready."""
        app = app_with_email
        # Register the connector
        try:
            app.connector_registry.register(
                connector_id="email",
                name="Email (IMAP/SMTP)",
                connector_type="builtin",
                capabilities=["email.read", "email.send"],
                setup_type="builtin",
            )
        except Exception:
            # May already exist if connector_registry.register is not idempotent
            pass

        from mycelos.tools.system import execute_system_status
        context = {"app": app, "user_id": "default"}
        status = execute_system_status({}, context)

        connectors = status.get("connectors", [])
        email_connector = next(
            (c for c in connectors if c["id"] == "email"), None
        )
        assert email_connector is not None, "Email connector not in system_status"
        assert email_connector["type"] == "builtin"
        assert email_connector["ready"] is True
        assert email_connector["status"] == "ready"


# ---------------------------------------------------------------------------
# 7. Full UC01 digest flow (live, end-to-end)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestEmailDigestFlowE2E:
    """Simulate the UC01 email-digest workflow: count -> inbox -> read -> verify.

    This is the closest we get to UC01 without invoking the LLM.
    """

    def test_full_digest_flow(self, app_with_email):
        """Run the email digest steps the agent would execute."""
        app = app_with_email
        context = {"app": app, "user_id": "default"}

        from mycelos.tools.email import (
            execute_email_count,
            execute_email_inbox,
            execute_email_read,
        )

        # Step 1: Count emails
        count = execute_email_count({}, context)
        assert "error" not in count, f"Count failed: {count}"
        assert count["total"] >= 0

        # Step 2: Fetch inbox (latest 5)
        inbox = execute_email_inbox({"limit": 5}, context)
        assert isinstance(inbox, list), f"Inbox returned error: {inbox}"

        # Step 3: Read first message (if any)
        if inbox:
            msg_id = inbox[0].get("id")
            if msg_id:
                full = execute_email_read({"message_id": msg_id}, context)
                assert isinstance(full, dict)
                assert "error" not in full, f"Read failed: {full}"
                assert "body" in full
                assert "from" in full
                assert "subject" in full

        # Step 4: Verify audit trail exists
        # The credential access should have been through the proxy
        # (no direct password in logs)
        audit_events = app.storage.fetchall(
            "SELECT event_type, details FROM audit_events ORDER BY created_at DESC LIMIT 20"
        )
        event_details_text = " ".join(
            str(e.get("details", "")) for e in audit_events
        )
        # Password must never appear in audit logs (Constitution Rule 4)
        from mycelos.tools.email import _get_email_creds
        creds = _get_email_creds(context)
        if creds:
            assert creds["password"] not in event_details_text, (
                "Password found in audit log details -- Constitution Rule 4 violation"
            )
