"""Tests for Permission UI — 5-option prompt, agent-scoped grants."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App
from mycelos.security.permissions import PermissionRequired, grant_permission


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-perm"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def permission():
    return PermissionRequired(
        tool="filesystem_read",
        action="mount add /tmp/test --rw",
        reason="Need read access to check files",
        target="/tmp/test",
        action_type="mount",
    )


class TestGrantPermission:
    def test_allow_session(self, app, permission):
        grants = set()
        result = grant_permission(app, permission, "allow_session",
                                   agent_id="creator", session_grants=grants)
        assert "creator:filesystem_read:/tmp/test" in grants

    def test_always_allow_creates_policy(self, app, permission):
        grant_permission(app, permission, "always_allow", agent_id="creator")
        decision = app.policy_engine.evaluate("default", "creator", "filesystem_read")
        assert decision == "always"

    def test_allow_all_always_creates_global_policy(self, app, permission):
        grant_permission(app, permission, "allow_all_always", agent_id="creator")
        # agent_id=None → applies to any agent
        decision = app.policy_engine.evaluate("default", "other-agent", "filesystem_read")
        assert decision == "always"

    def test_always_allow_triggers_config_generation(self, app, permission):
        gen_before = app.config.get_active_generation_id()
        grant_permission(app, permission, "always_allow", agent_id="creator")
        gen_after = app.config.get_active_generation_id()
        assert gen_after > gen_before

    def test_allow_all_triggers_config_generation(self, app, permission):
        gen_before = app.config.get_active_generation_id()
        grant_permission(app, permission, "allow_all_always", agent_id="creator")
        gen_after = app.config.get_active_generation_id()
        assert gen_after > gen_before

    def test_never_allow_triggers_config_generation(self, app, permission):
        gen_before = app.config.get_active_generation_id()
        grant_permission(app, permission, "never_allow", agent_id="creator")
        gen_after = app.config.get_active_generation_id()
        assert gen_after > gen_before

    def test_deny_does_not_create_policy(self, app, permission):
        gen_before = app.config.get_active_generation_id()
        grant_permission(app, permission, "deny", agent_id="creator")
        gen_after = app.config.get_active_generation_id()
        assert gen_after == gen_before


class TestHandlePermissionResponse:
    """Test _handle_permission_response accepts 1-5 and legacy shortcuts."""

    def _make_service(self, app):
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        service._pending_permission = {
            "tool_name": "filesystem_read",
            "tool_call_id": "tc-1",
            "args": {"path": "/tmp/test"},
            "permission": PermissionRequired(
                tool="filesystem_read",
                action="mount add /tmp/test --rw",
                reason="Need read access",
                target="/tmp/test",
            ),
            "session_id": "test-sess",
            "user_id": "default",
            "agent_id": "creator",
            "conversation": [],
        }
        return service

    def test_input_1_maps_to_allow_session(self, app):
        service = self._make_service(app)
        with patch.object(service, '_execute_tool', return_value={"ok": True}):
            events = service._handle_permission_response("1", service._pending_permission)
        # Should not raise, should process

    def test_input_2_maps_to_always_allow(self, app):
        service = self._make_service(app)
        with patch.object(service, '_execute_tool', return_value={"ok": True}):
            events = service._handle_permission_response("2", service._pending_permission)
        decision = app.policy_engine.evaluate("default", "creator", "filesystem_read")
        assert decision == "always"

    def test_input_3_maps_to_allow_all(self, app):
        service = self._make_service(app)
        with patch.object(service, '_execute_tool', return_value={"ok": True}):
            events = service._handle_permission_response("3", service._pending_permission)
        decision = app.policy_engine.evaluate("default", "any-agent", "filesystem_read")
        assert decision == "always"

    def test_input_4_maps_to_deny(self, app):
        service = self._make_service(app)
        events = service._handle_permission_response("4", service._pending_permission)
        assert any("denied" in str(e.data).lower() for e in events)

    def test_input_5_maps_to_never(self, app):
        service = self._make_service(app)
        events = service._handle_permission_response("5", service._pending_permission)
        decision = app.policy_engine.evaluate("default", "creator", "filesystem_read")
        assert decision == "never"

    def test_legacy_y_still_works(self, app):
        service = self._make_service(app)
        with patch.object(service, '_execute_tool', return_value={"ok": True}):
            events = service._handle_permission_response("y", service._pending_permission)
        # Should not raise

    def test_legacy_a_still_works(self, app):
        service = self._make_service(app)
        with patch.object(service, '_execute_tool', return_value={"ok": True}):
            events = service._handle_permission_response("a", service._pending_permission)

    def test_perm_prefix_from_web(self, app):
        """Web sends PERM:{id}:{value} format."""
        service = self._make_service(app)
        with patch.object(service, '_execute_tool', return_value={"ok": True}):
            events = service._handle_permission_response(
                "PERM:abc123:1", service._pending_permission)


class TestTelegramPermissionButtons:
    def test_build_permission_keyboard(self):
        from mycelos.channels.telegram import _build_permission_keyboard
        keyboard = _build_permission_keyboard("perm123", "Creator")
        assert len(keyboard.inline_keyboard) == 3
        assert len(keyboard.inline_keyboard[0]) == 2
        first_btn = keyboard.inline_keyboard[0][0]
        assert first_btn.callback_data == "perm:allow_session:perm123"
        assert "Creator" in first_btn.text

    def test_build_keyboard_second_row(self):
        from mycelos.channels.telegram import _build_permission_keyboard
        keyboard = _build_permission_keyboard("xyz", "Agent")
        second_row = keyboard.inline_keyboard[1]
        assert len(second_row) == 2
        assert second_row[0].callback_data == "perm:allow_all:xyz"
        assert second_row[1].callback_data == "perm:deny:xyz"

    def test_parse_permission_callback(self):
        from mycelos.channels.telegram import _parse_permission_callback
        decision, perm_id = _parse_permission_callback("perm:allow_always:abc123")
        assert decision == "allow_always"
        assert perm_id == "abc123"

    def test_parse_invalid_callback(self):
        from mycelos.channels.telegram import _parse_permission_callback
        assert _parse_permission_callback("invalid:data") is None
        assert _parse_permission_callback("perm:only_two") is None
        assert _parse_permission_callback("wrong:a:b:c") is None

    def test_pending_permissions_dict_exists(self):
        from mycelos.channels import telegram
        assert hasattr(telegram, '_pending_permissions')
        assert isinstance(telegram._pending_permissions, dict)
