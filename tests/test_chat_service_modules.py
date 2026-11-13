"""Structure tests for the split ChatService (chat/service.py → mixins).

The refactor must not change behavior or break callers: the old import
paths keep working, every method is still reachable on ChatService, and
the extracted helpers behave like the inline code they replace.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import mycelos.chat.service as service_mod
from mycelos.chat import guards, system_info, tool_selection
from mycelos.chat.events import ChatEvent
from mycelos.chat.service import ChatService

# Every method ChatService had before the split.
_EXPECTED_METHODS = [
    "create_session", "resume_session", "get_system_prompt", "handle_message",
    "mark_force_include", "_build_attachment_blocks",
    "_get_session_tools", "_append_return_tool", "_get_agent_allowed_tools",
    "_get_active_agent", "_execute_handoff", "_get_model_for_agent",
    "_complete_for_agent", "_augment_tools_with_connectors",
    "_execute_batch_parallel", "_execute_tool", "_execute_tool_inner",
    "_execute_custom_agent", "_handle_discover_tools",
    "_handle_request_action", "_handle_permission_response", "_execute_pending_action",
    "_handle_run_command", "_resume_workflow", "_get_pending_runs_context",
    "_handle_interview_message", "_run_creator_pipeline", "_suggest_agent_name",
    "_handle_system_command", "_get_system_status", "_filesystem_read",
    "_filesystem_write", "_filesystem_list", "_normalize_path",
    "_get_follow_up_suggestions", "_try_save_name",
]


@pytest.mark.parametrize("name", _EXPECTED_METHODS)
def test_chat_service_keeps_every_method(name: str) -> None:
    assert callable(getattr(ChatService, name, None)), name


def test_old_import_paths_still_work() -> None:
    assert service_mod.CHAT_AGENT_TOOLS is tool_selection.CHAT_AGENT_TOOLS
    assert service_mod._get_chat_agent_tools is tool_selection._get_chat_agent_tools
    assert service_mod._partition_tool_calls is tool_selection._partition_tool_calls
    assert service_mod._ROUTING_CONTROL_TOOLS is tool_selection._ROUTING_CONTROL_TOOLS
    assert service_mod._SYSTEM_AGENT_IDS is tool_selection._SYSTEM_AGENT_IDS
    assert service_mod.MAX_TOOL_ROUNDS == tool_selection.MAX_TOOL_ROUNDS
    assert service_mod._sanitize_dict is guards._sanitize_dict
    assert service_mod._validate_memory_write is guards._validate_memory_write
    assert service_mod._build_system_info is system_info._build_system_info


def test_service_module_stays_small() -> None:
    """Guard against chat/service.py growing back into a mega-module."""
    lines = Path(inspect.getfile(service_mod)).read_text().count("\n")
    assert lines < 800, f"chat/service.py has {lines} lines — move code into a mixin"


def test_handle_message_is_still_serialized_per_session() -> None:
    # _serialized_session wraps handle_message (functools.wraps sets __wrapped__)
    assert hasattr(ChatService.handle_message, "__wrapped__")


class _StubStore:
    def __init__(self) -> None:
        self.views: list[dict] = []

    def append_view(self, session_id: str, view: dict) -> None:
        self.views.append(view)


class _StubApp:
    def __init__(self) -> None:
        self.session_store = _StubStore()


def _bare_service() -> ChatService:
    svc = ChatService.__new__(ChatService)
    svc._app = _StubApp()
    return svc


def test_emit_tool_result_events_strips_markers_and_emits_events() -> None:
    svc = _bare_service()
    events: list[ChatEvent] = []
    result = {
        "__widget__": "form",
        "fields": [],
        "__view__": [{"type": "timeline"}, {"type": "card"}],
        "__suggested_actions__": [{"label": "Open", "command": "/open"}],
        "summary": "ok",
    }
    svc._emit_tool_result_events("some_tool", result, "s1", events)

    assert result == {"fields": [], "summary": "ok"}  # markers removed, the LLM sees the rest
    types = [e.type for e in events]
    assert types == ["widget", "widget", "widget", "suggested-actions"]
    assert events[0].data["widget"]["type"] == "form"
    assert events[0].data["widget"]["fields"] == []
    assert svc._app.session_store.views == [{"type": "timeline"}, {"type": "card"}]


def test_emit_tool_result_events_session_meta_only_for_session_set() -> None:
    svc = _bare_service()
    events: list[ChatEvent] = []
    svc._emit_tool_result_events("note_write", {"title": "x"}, "s1", events)
    assert events == []
    svc._emit_tool_result_events(
        "session_set", {"session_id": "s1", "title": "T", "topic": "t"}, "s1", events,
    )
    assert [e.type for e in events] == ["session-meta"]
    assert events[0].data == {"session_id": "s1", "title": "T", "topic": "t"}
