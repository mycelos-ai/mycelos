"""Tests for automatic agent routing.

The mycelos agent must recognize when a request belongs to a registered
custom/persona agent and route there seamlessly:

1. Agent roster in the system prompt (generated from the registry,
   omitted when no custom agents exist, with fail-soft guardrails).
2. Seamless auto-handoff that sticks across messages (session_agents),
   with a friendly i18n transition line.
3. An explicit return path (return_to_mycelos tool) — LLM judgment,
   never keyword matching.
4. Capability safety: routed persona agents keep their registered tool
   scoping (Constitution Rule 5), enforced fail-closed (Rule 3).

All LLM behavior is scripted via a fake broker — no keyword-based
classification anywhere; the model decides via tools.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mycelos.app import App
from mycelos.chat.service import ChatService


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-auto-routing"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def service(app: App) -> ChatService:
    return ChatService(app)


# --- Helpers -----------------------------------------------------------


def make_persona(
    app: App,
    agent_id: str = "research-buddy",
    name: str = "Research Buddy",
    prompt: str = (
        "You are Research Buddy, a specialist for deep literature research "
        "and source verification."
    ),
    allowed_tools: list[str] | None = None,
    user_facing: bool = True,
) -> str:
    """Register an active user-facing persona agent in the registry."""
    app.agent_registry.register(agent_id, name, "persona", [], "user")
    app.agent_registry.set_persona(
        agent_id,
        system_prompt=prompt,
        allowed_tools=allowed_tools,
        user_facing=user_facing,
        display_name=name,
    )
    app.agent_registry.set_status(agent_id, "active")
    return agent_id


class FakeResponse:
    """Minimal LLM response object matching what ChatService reads."""

    def __init__(self, content: str = "", tool_calls: list | None = None,
                 model: str = "fake-model"):
        self.content = content
        self.tool_calls = tool_calls
        self.total_tokens = 10
        self.prompt_tokens = 5
        self.completion_tokens = 5
        self.model = model
        self.cost = 0.0
        self.stop_reason = "tool_use" if tool_calls else "end_turn"


def tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def scripted_llm(responses: list[FakeResponse]):
    """Return (complete_fn, calls) — a scripted fake broker.

    Each call to complete() pops the next response and records the
    messages/tools/model it was called with for behavioral assertions.
    """
    calls: list[dict] = []
    remaining = list(responses)

    def _complete(messages, tools=None, model=None, **kwargs):
        calls.append({
            "messages": [dict(m) for m in messages],
            "tools": tools,
            "model": model,
        })
        if not remaining:
            return FakeResponse(content="(out of script)")
        return remaining.pop(0)

    return _complete, calls


def _system_prompt_of(call: dict) -> str:
    for m in call["messages"]:
        if m.get("role") == "system":
            return str(m.get("content", ""))
    return ""


def _tool_names(tools: list[dict]) -> set[str]:
    return {t.get("function", {}).get("name", "") for t in tools}


# --- 1. Agent roster in the system prompt ------------------------------


class TestAgentRoster:
    def test_roster_omitted_when_no_custom_agents(self, app: App):
        from mycelos.prompts import build_prompt_variables

        variables = build_prompt_variables(app)
        assert variables["available_agents"] == ""

    def test_prompt_has_no_custom_agents_section_when_none_exist(self, app: App):
        from mycelos.agents.handlers.mycelos_handler import MycelosHandler

        prompt = MycelosHandler(app).get_system_prompt()
        assert "## Custom Agents" not in prompt

    def test_roster_lists_user_facing_persona(self, app: App):
        from mycelos.agents.handlers.mycelos_handler import MycelosHandler

        make_persona(app)
        prompt = MycelosHandler(app).get_system_prompt()
        assert "Research Buddy" in prompt
        assert "research-buddy" in prompt
        # Description / when-to-use derived from the registry entry
        assert "literature research" in prompt

    def test_roster_contains_when_to_use_guidance(self, app: App):
        from mycelos.prompts import build_prompt_variables

        make_persona(app)
        roster = build_prompt_variables(app)["available_agents"]
        assert "When to use" in roster

    def test_roster_has_fail_soft_guardrails(self, app: App):
        """The model must be told to route only on clear matches and to
        stay with mycelos when unsure (fail-soft to the generalist)."""
        from mycelos.prompts import build_prompt_variables

        make_persona(app)
        roster = build_prompt_variables(app)["available_agents"]
        assert "clearly matches" in roster
        assert "When unsure" in roster

    def test_system_agents_not_in_roster(self, app: App):
        from mycelos.setup import register_system_agents
        from mycelos.prompts import build_prompt_variables

        register_system_agents(app)
        make_persona(app)
        roster = build_prompt_variables(app)["available_agents"]
        for system_id in ("workflow-agent", "evaluator-agent", "auditor-agent"):
            assert system_id not in roster

    def test_roster_generated_from_registry_not_hardcoded(self, app: App):
        from mycelos.agents.handlers.mycelos_handler import MycelosHandler

        make_persona(app, agent_id="garden-coach", name="Garden Coach",
                     prompt="You are Garden Coach, a specialist for plant care.")
        prompt = MycelosHandler(app).get_system_prompt()
        assert "garden-coach" in prompt

    def test_persona_in_handoff_tool_enum(self, app: App):
        from mycelos.agents.handlers.mycelos_handler import MycelosHandler

        make_persona(app)
        tools = MycelosHandler(app).get_tools()
        handoff = next(t for t in tools if t["function"]["name"] == "handoff")
        enum = handoff["function"]["parameters"]["properties"]["target_agent"]["enum"]
        assert "research-buddy" in enum
        # Doctor/builder paths must keep working
        assert "builder" in enum
        assert "doctor" in enum


# --- 2. Seamless auto-handoff + stickiness ------------------------------


class TestAutoHandoff:
    def test_handoff_routes_to_persona_and_persists(self, app: App, service: ChatService):
        make_persona(app)
        session_id = service.create_session()

        responses = [
            FakeResponse(tool_calls=[tool_call("handoff", {
                "target_agent": "research-buddy",
                "reason": "User asked for deep literature research",
                "summary": "User wants sources on mycelium networks.",
            })]),
            FakeResponse(content="Hi, Research Buddy here — let's dig in."),
        ]
        complete, calls = scripted_llm(responses)
        with patch.object(app.llm, "complete", side_effect=complete):
            events = service.handle_message("(scripted)", session_id)

        row = app.storage.fetchone(
            "SELECT active_agent_id FROM session_agents WHERE session_id = ?",
            (session_id,),
        )
        assert row["active_agent_id"] == "research-buddy"

        agent_names = [e.data.get("agent") for e in events if e.type == "agent"]
        assert "Research Buddy" in agent_names

        texts = [e.data.get("content", "") for e in events if e.type == "text"]
        assert any("Research Buddy here" in t for t in texts)

    def test_handoff_emits_i18n_transition_line(self, app: App, service: ChatService):
        from mycelos.i18n import t

        make_persona(app)
        session_id = service.create_session()

        responses = [
            FakeResponse(tool_calls=[tool_call("handoff", {
                "target_agent": "research-buddy",
                "reason": "specialist request",
            })]),
            FakeResponse(content="Ready."),
        ]
        complete, _ = scripted_llm(responses)
        with patch.object(app.llm, "complete", side_effect=complete):
            events = service.handle_message("(scripted)", session_id)

        expected = t("chat.handoff_transition", agent="Research Buddy")
        system_lines = [e.data.get("content", "") for e in events
                        if e.type == "system-response"]
        assert expected in system_lines

    def test_routed_agent_sticks_for_next_message(self, app: App, service: ChatService):
        make_persona(app)
        session_id = service.create_session()
        service._execute_handoff(session_id, "research-buddy", "routed by test")

        responses = [FakeResponse(content="Persona reply.")]
        complete, calls = scripted_llm(responses)
        with patch.object(app.llm, "complete", side_effect=complete):
            service.handle_message("(scripted follow-up)", session_id)

        # The persona's own system prompt is in play, not mycelos's
        assert "Research Buddy, a specialist" in _system_prompt_of(calls[0])
        assert service._get_active_agent(session_id) == "research-buddy"


# --- 3. Explicit return path --------------------------------------------


class TestReturnToMycelos:
    def test_return_tool_offered_to_routed_persona(self, app: App, service: ChatService):
        make_persona(app)
        session_id = service.create_session()
        handler = app.get_agent_handlers()["research-buddy"]
        tools = service._get_session_tools(handler, session_id)
        assert "return_to_mycelos" in _tool_names(tools)

    def test_return_tool_not_offered_to_mycelos(self, app: App, service: ChatService):
        session_id = service.create_session()
        handler = app.get_agent_handlers()["mycelos"]
        tools = service._get_session_tools(handler, session_id)
        assert "return_to_mycelos" not in _tool_names(tools)

    def test_return_tool_switches_back_to_mycelos(self, app: App, service: ChatService):
        from mycelos.i18n import t

        make_persona(app)
        session_id = service.create_session()
        service._execute_handoff(session_id, "research-buddy", "routed by test")

        responses = [
            FakeResponse(tool_calls=[tool_call("return_to_mycelos", {
                "reason": "User wants the generalist back",
            })]),
            FakeResponse(content="Good to have you back!"),
        ]
        complete, _ = scripted_llm(responses)
        with patch.object(app.llm, "complete", side_effect=complete):
            events = service.handle_message("(scripted)", session_id)

        assert service._get_active_agent(session_id) == "mycelos"
        row = app.storage.fetchone(
            "SELECT active_agent_id FROM session_agents WHERE session_id = ?",
            (session_id,),
        )
        assert row["active_agent_id"] == "mycelos"

        expected = t("chat.handoff_return", agent="Mycelos")
        system_lines = [e.data.get("content", "") for e in events
                        if e.type == "system-response"]
        assert expected in system_lines

    def test_persona_prompt_mentions_return_tool(self, app: App):
        """The persona must be told about the return path so the LLM can
        decide to use it — judgment via prompt, not keyword matching."""
        make_persona(app)
        handler = app.get_agent_handlers()["research-buddy"]
        assert "return_to_mycelos" in handler.get_system_prompt()


# --- 4. Capability safety (Constitution Rule 5) -------------------------


class TestRoutedAgentToolScoping:
    def test_scoped_persona_only_sees_allowed_tools(self, app: App, service: ChatService):
        make_persona(app, agent_id="note-reader", name="Note Reader",
                     prompt="You are Note Reader, a specialist for reading notes.",
                     allowed_tools=["note_read", "note_list"])
        session_id = service.create_session()
        handler = app.get_agent_handlers()["note-reader"]
        names = _tool_names(service._get_session_tools(handler, session_id))

        assert "note_read" in names
        assert "note_list" in names
        # Tools outside the allowlist must not be visible
        assert "note_write" not in names
        assert "search_web" not in names
        assert "filesystem_read" not in names
        # No escape hatch via lazy discovery
        assert "discover_tools" not in names
        # But the return path stays available
        assert "return_to_mycelos" in names

    def test_unrestricted_persona_keeps_full_toolset(self, app: App, service: ChatService):
        make_persona(app, agent_id="open-buddy", name="Open Buddy",
                     prompt="You are Open Buddy, a generalist persona.",
                     allowed_tools=None)
        session_id = service.create_session()
        handler = app.get_agent_handlers()["open-buddy"]
        names = _tool_names(service._get_session_tools(handler, session_id))
        assert "search_web" in names
        assert "return_to_mycelos" in names

    def test_execution_blocked_outside_allowlist(self, app: App, service: ChatService):
        make_persona(app, agent_id="note-reader", name="Note Reader",
                     prompt="You are Note Reader, a specialist for reading notes.",
                     allowed_tools=["note_read", "note_list"])
        session_id = service.create_session()
        service._execute_handoff(session_id, "note-reader", "routed by test")

        result = service._execute_tool(
            "search_web", {"query": "x"},
            user_id="default", session_id=session_id, agent_id="note-reader",
        )
        assert isinstance(result, dict)
        assert "error" in result

        blocked = app.storage.fetchall(
            "SELECT details FROM audit_events WHERE event_type = 'tool.blocked'"
        )
        assert any("note-reader" in (b["details"] or "") for b in blocked)

    def test_execution_allowed_inside_allowlist(self, app: App, service: ChatService):
        make_persona(app, agent_id="note-reader", name="Note Reader",
                     prompt="You are Note Reader, a specialist for reading notes.",
                     allowed_tools=["note_read", "note_list"])
        session_id = service.create_session()
        service._execute_handoff(session_id, "note-reader", "routed by test")

        result = service._execute_tool(
            "note_list", {},
            user_id="default", session_id=session_id, agent_id="note-reader",
        )
        # Must not be the allowlist rejection (the tool itself may return
        # anything — behavioral assertion only on the security gate)
        if isinstance(result, dict) and "error" in result:
            assert "not available to agent" not in result["error"]

    def test_return_tool_exempt_from_allowlist(self, app: App, service: ChatService):
        make_persona(app, agent_id="note-reader", name="Note Reader",
                     prompt="You are Note Reader, a specialist for reading notes.",
                     allowed_tools=["note_read"])
        session_id = service.create_session()
        service._execute_handoff(session_id, "note-reader", "routed by test")

        result = service._execute_tool(
            "return_to_mycelos", {"reason": "done"},
            user_id="default", session_id=session_id, agent_id="note-reader",
        )
        assert isinstance(result, dict)
        assert result.get("status") == "handoff"
        assert result.get("target_agent") == "mycelos"

    def test_unknown_agent_fails_closed(self, app: App, service: ChatService):
        """Constitution Rule 3: unknown agents are denied, not allowed."""
        session_id = service.create_session()
        result = service._execute_tool(
            "search_web", {"query": "x"},
            user_id="default", session_id=session_id, agent_id="ghost-agent",
        )
        assert isinstance(result, dict)
        assert "not available to agent" in result.get("error", "")
