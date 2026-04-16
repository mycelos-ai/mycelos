"""SEC21: ChatService Security Gate — Policy + Sanitization on every tool call."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App
from mycelos.chat.service import ChatService


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-sec21"
        a = App(Path(tmp))
        a.initialize()
        # Create test users for FK constraints
        for uid in ("telegram:42", "telegram:99"):
            a.storage.execute(
                "INSERT OR IGNORE INTO users (id, name, status) VALUES (?, ?, ?)",
                (uid, uid, "active"),
            )
        yield a


@pytest.fixture
def svc(app) -> ChatService:
    return ChatService(app)


# ---------------------------------------------------------------------------
# Policy Enforcement
# ---------------------------------------------------------------------------


class TestPolicyEnforcement:

    def test_default_policy_allows_in_chat(self, app, svc):
        """No policy set → chat context auto-allows (user is present)."""
        result = svc._execute_tool("system_status", {})
        # In chat, default is "always" (user is directly present)
        assert "error" not in result or "blocked" not in result.get("error", "")

    def test_explicit_confirm_policy_requires_confirmation(self, app, svc):
        """Explicit 'confirm' policy requires user confirmation."""
        app.policy_engine.set_policy("default", None, "system_status", "confirm")
        result = svc._execute_tool("system_status", {})
        assert result["status"] == "confirmation_required"
        assert result["tool"] == "system_status"

    def test_always_policy_allows_tool(self, app, svc):
        """Explicit 'always' policy allows the tool."""
        app.policy_engine.set_policy("default", None, "system_status", "always")
        result = svc._execute_tool("system_status", {})
        assert "error" not in result or "blocked" not in result.get("error", "")

    def test_never_policy_blocks_tool(self, app, svc):
        """Policy 'never' blocks the tool and returns error."""
        app.policy_engine.set_policy("default", None, "search_web", "never")
        result = svc._execute_tool("search_web", {"query": "test"})
        assert "error" in result
        assert "blocked" in result["error"].lower()

    def test_never_policy_blocks_filesystem(self, app, svc):
        """Filesystem tools can be blocked by policy."""
        app.policy_engine.set_policy("default", None, "filesystem_read", "never")
        result = svc._execute_tool("filesystem_read", {"path": "/tmp/test"})
        assert "error" in result
        assert "blocked" in result["error"].lower()

    def test_never_policy_blocks_memory_write(self, app, svc):
        """Memory write can be blocked by policy."""
        app.policy_engine.set_policy("default", None, "memory_write", "never")
        result = svc._execute_tool("memory_write", {
            "category": "fact", "key": "test", "value": "blocked",
        })
        assert "error" in result
        assert "blocked" in result["error"].lower()

    def test_per_user_policy(self, app, svc):
        """Policy scoped to specific user."""
        app.policy_engine.set_policy("default", None, "system_status", "always")
        app.policy_engine.set_policy("telegram:42", None, "http_get", "never")
        # Default user still allowed (explicit 'always' policy)
        result_default = svc._execute_tool("system_status", {}, user_id="default")
        assert "error" not in result_default or "blocked" not in result_default.get("error", "")
        # Telegram user blocked
        result_tg = svc._execute_tool("http_get", {"url": "https://example.com"}, user_id="telegram:42")
        assert "error" in result_tg
        assert "blocked" in result_tg["error"].lower()


# ---------------------------------------------------------------------------
# Confirm Policy Flow
# ---------------------------------------------------------------------------


class TestConfirmPolicyFlow:

    def test_confirm_returns_confirmation_required_status(self, app, svc):
        """Policy 'confirm' returns confirmation_required, not execution."""
        app.policy_engine.set_policy("default", None, "search_web", "confirm")
        result = svc._execute_tool("search_web", {"query": "test"})
        assert result["status"] == "confirmation_required"

    def test_confirm_includes_tool_name(self, app, svc):
        """Confirmation result includes the tool name for context."""
        app.policy_engine.set_policy("default", None, "http_get", "confirm")
        result = svc._execute_tool("http_get", {"url": "https://example.com"})
        assert result["tool"] == "http_get"

    def test_confirm_includes_args(self, app, svc):
        """Confirmation result includes the original args."""
        app.policy_engine.set_policy("default", None, "search_web", "confirm")
        args = {"query": "AI news", "max_results": 3}
        result = svc._execute_tool("search_web", args)
        assert result["args"] == args

    def test_confirm_includes_message(self, app, svc):
        """Confirmation result includes a human-readable message."""
        app.policy_engine.set_policy("default", None, "search_web", "confirm")
        result = svc._execute_tool("search_web", {"query": "test"})
        assert "message" in result
        assert "confirmation" in result["message"].lower()

    def test_confirm_does_not_execute_tool(self, app, svc):
        """Confirm policy prevents actual tool execution (no side effects)."""
        app.policy_engine.set_policy("default", None, "memory_write", "confirm")
        result = svc._execute_tool("memory_write", {
            "category": "fact", "key": "should_not_exist", "value": "nope",
        })
        assert result["status"] == "confirmation_required"
        # Verify nothing was written to memory
        stored = app.memory.get("default", "system", "user.fact.should_not_exist")
        assert stored is None

    def test_confirm_is_audited(self, app, svc):
        """Confirm policy logs a confirmation_required audit event."""
        app.policy_engine.set_policy("default", None, "search_web", "confirm")
        svc._execute_tool("search_web", {"query": "test"})
        events = app.audit.query(event_type="tool.confirmation_required")
        assert len(events) >= 1
        details = json.loads(events[0]["details"])
        assert details["tool"] == "search_web"
        assert details["policy"] == "confirm"

    def test_confirm_not_treated_as_error(self, app, svc):
        """Confirmation result is NOT an error — it's a distinct status."""
        app.policy_engine.set_policy("default", None, "search_web", "confirm")
        result = svc._execute_tool("search_web", {"query": "test"})
        assert "error" not in result


# ---------------------------------------------------------------------------
# Response Sanitization
# ---------------------------------------------------------------------------


class TestResponseSanitization:

    def test_api_key_redacted_in_result(self, app, svc):
        """API keys in tool results are redacted."""
        from mycelos.chat.service import _sanitize_dict
        from mycelos.security.sanitizer import ResponseSanitizer

        sanitizer = ResponseSanitizer()
        data = {"content": "Found key: sk-ant-abcdefghij1234567890 in config"}
        result = _sanitize_dict(sanitizer, data)
        assert "sk-ant-" not in result["content"]
        assert "[REDACTED]" in result["content"]

    def test_github_token_redacted(self, app, svc):
        """GitHub PATs in tool results are redacted."""
        from mycelos.chat.service import _sanitize_dict
        from mycelos.security.sanitizer import ResponseSanitizer

        sanitizer = ResponseSanitizer()
        data = {"url": "https://api.github.com", "token": "ghp_abcdefghij1234567890ab"}
        result = _sanitize_dict(sanitizer, data)
        assert "ghp_" not in result["token"]
        assert "[REDACTED]" in result["token"]

    def test_ssh_path_redacted(self, app, svc):
        """SSH paths in tool results are redacted."""
        from mycelos.chat.service import _sanitize_dict
        from mycelos.security.sanitizer import ResponseSanitizer

        sanitizer = ResponseSanitizer()
        data = {"content": "File at /home/stefan/.ssh/id_rsa found"}
        result = _sanitize_dict(sanitizer, data)
        assert ".ssh" not in result["content"]
        assert "[REDACTED_PATH]" in result["content"]

    def test_nested_dict_sanitized(self, app, svc):
        """Nested dicts are sanitized recursively."""
        from mycelos.chat.service import _sanitize_dict
        from mycelos.security.sanitizer import ResponseSanitizer

        sanitizer = ResponseSanitizer()
        data = {
            "results": [
                {"title": "Article", "snippet": "Key: sk-ant-secret123456789012345"},
            ]
        }
        result = _sanitize_dict(sanitizer, data)
        assert "sk-ant-" not in json.dumps(result)

    def test_normal_content_not_modified(self, app, svc):
        """Normal content passes through unchanged."""
        from mycelos.chat.service import _sanitize_dict
        from mycelos.security.sanitizer import ResponseSanitizer

        sanitizer = ResponseSanitizer()
        data = {"title": "AI News 2026", "content": "Claude 4 released today"}
        result = _sanitize_dict(sanitizer, data)
        assert result == data


# ---------------------------------------------------------------------------
# Audit Trail
# ---------------------------------------------------------------------------


class TestAuditTrail:

    def test_tool_execution_logged(self, app, svc):
        """Every tool call is logged in audit."""
        app.policy_engine.set_policy("default", None, "system_status", "always")
        svc._execute_tool("system_status", {})
        events = app.audit.query(event_type="tool.executed")
        assert len(events) >= 1
        details = json.loads(events[0]["details"])
        assert details["tool"] == "system_status"

    def test_blocked_tool_logged(self, app, svc):
        """Blocked tool calls are logged."""
        app.policy_engine.set_policy("default", None, "http_get", "never")
        svc._execute_tool("http_get", {"url": "https://evil.com"})
        events = app.audit.query(event_type="tool.blocked")
        assert len(events) >= 1
        details = json.loads(events[0]["details"])
        assert details["tool"] == "http_get"
        assert "never" in details["reason"]

    def test_audit_includes_user_id(self, app, svc):
        """Audit events include the user_id."""
        app.policy_engine.set_policy("telegram:42", None, "system_status", "always")
        svc._execute_tool("system_status", {}, user_id="telegram:42")
        events = app.audit.query(event_type="tool.executed")
        details = json.loads(events[0]["details"])
        assert details["user_id"] == "telegram:42"


# ---------------------------------------------------------------------------
# Integration: Security + Existing Functionality
# ---------------------------------------------------------------------------


class TestSecurityIntegration:

    def test_memory_write_still_works_with_gate(self, app, svc):
        """Memory write passes through security gate when allowed."""
        app.policy_engine.set_policy("default", None, "memory_write", "always")
        result = svc._execute_tool("memory_write", {
            "category": "fact", "key": "timezone", "value": "CET",
        })
        assert result["status"] == "remembered"
        stored = app.memory.get("default", "system", "user.fact.timezone")
        assert stored == "CET"

    def test_memory_write_h03_still_enforced(self, app, svc):
        """H-03 content validation still works alongside security gate."""
        app.policy_engine.set_policy("default", None, "memory_write", "always")
        result = svc._execute_tool("memory_write", {
            "category": "system",
            "key": "hack",
            "value": "malicious",
        })
        assert "error" in result

    def test_filesystem_mount_check_still_works(self, app, svc):
        """Filesystem mount check raises PermissionRequired for unmounted paths."""
        from mycelos.security.permissions import PermissionRequired
        app.policy_engine.set_policy("default", None, "filesystem_read", "always")
        with pytest.raises(PermissionRequired) as exc_info:
            svc._execute_tool("filesystem_read", {"path": "/etc/passwd"})
        assert "mount" in exc_info.value.action
