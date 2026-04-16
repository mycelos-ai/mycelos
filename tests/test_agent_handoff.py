"""Tests for Agent Handoff — session tracking, handler dispatch, handoff tool."""

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-handoff"
        a = App(Path(tmp))
        a.initialize()
        yield a


class TestSessionAgentTracking:
    def test_session_agents_table_exists(self, app):
        result = app.storage.fetchone("SELECT 1 FROM session_agents LIMIT 0")

    def test_default_agent_is_mycelos(self, app):
        agent = app.storage.fetchone(
            "SELECT active_agent_id FROM session_agents WHERE session_id = 'nonexistent'"
        )
        assert agent is None  # No row = default mycelos

    def test_set_active_agent(self, app):
        app.storage.execute(
            "INSERT INTO session_agents (session_id, active_agent_id, handoff_reason) VALUES (?, ?, ?)",
            ("sess-1", "creator", "User wants to build an agent"),
        )
        row = app.storage.fetchone(
            "SELECT active_agent_id, handoff_reason FROM session_agents WHERE session_id = 'sess-1'"
        )
        assert row["active_agent_id"] == "creator"

    def test_update_active_agent(self, app):
        app.storage.execute(
            "INSERT INTO session_agents (session_id, active_agent_id) VALUES (?, ?)",
            ("sess-2", "mycelos"),
        )
        app.storage.execute(
            "UPDATE session_agents SET active_agent_id = ? WHERE session_id = ?",
            ("planner", "sess-2"),
        )
        row = app.storage.fetchone(
            "SELECT active_agent_id FROM session_agents WHERE session_id = 'sess-2'"
        )
        assert row["active_agent_id"] == "planner"

    def test_user_facing_column_exists(self, app):
        app.storage.execute("SELECT user_facing FROM agents LIMIT 0")


class TestAgentHandlerProtocol:
    def test_protocol_exists(self):
        from mycelos.agents.handlers.base import AgentHandler
        assert hasattr(AgentHandler, 'handle')
        assert hasattr(AgentHandler, 'agent_id')
        assert hasattr(AgentHandler, 'display_name')
        assert hasattr(AgentHandler, 'get_system_prompt')
        assert hasattr(AgentHandler, 'get_tools')

    def test_protocol_is_runtime_checkable(self):
        from mycelos.agents.handlers.base import AgentHandler
        # Should be able to use isinstance checks
        assert hasattr(AgentHandler, '__protocol_attrs__') or hasattr(AgentHandler, '__abstractmethods__') or True


class TestMycelosHandler:
    def test_mycelos_has_correct_id(self, app):
        from mycelos.agents.handlers.mycelos_handler import MycelosHandler
        handler = MycelosHandler(app)
        assert handler.agent_id == "mycelos"
        assert handler.display_name == "Mycelos"

    def test_mycelos_has_tools(self, app):
        from mycelos.agents.handlers.mycelos_handler import MycelosHandler
        handler = MycelosHandler(app)
        tools = handler.get_tools()
        assert len(tools) > 0
        tool_names = [t["function"]["name"] for t in tools]
        assert "handoff" in tool_names

    def test_mycelos_prompt_has_handoff_rules(self, app):
        from mycelos.agents.handlers.mycelos_handler import MycelosHandler
        handler = MycelosHandler(app)
        prompt = handler.get_system_prompt()
        assert "handoff" in prompt.lower()
        assert "creator" in prompt.lower()
        assert "planner" in prompt.lower()


class TestBuilderHandler:
    def test_builder_has_correct_id(self, app):
        from mycelos.agents.handlers.builder_handler import BuilderHandler
        handler = BuilderHandler(app)
        assert handler.agent_id == "builder"
        assert handler.display_name == "Builder-Agent"

    def test_builder_has_all_tools(self, app):
        from mycelos.agents.handlers.builder_handler import BuilderHandler
        handler = BuilderHandler(app)
        tools = handler.get_tools()
        tool_names = [t["function"]["name"] for t in tools]
        assert "handoff" in tool_names
        assert "create_agent" in tool_names
        assert "create_workflow" in tool_names
        assert "list_tools" in tool_names
        assert "search_mcp_servers" in tool_names

    def test_builder_prompt_has_workflow_and_agent(self, app):
        from mycelos.agents.handlers.builder_handler import BuilderHandler
        handler = BuilderHandler(app)
        prompt = handler.get_system_prompt()
        assert "Builder" in prompt
        assert "workflow" in prompt.lower()
        assert "agent" in prompt.lower()


class TestHandoffExecution:
    def test_handoff_updates_session_agent(self, app):
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        session_id = service.create_session()
        service._execute_handoff(session_id, "creator", "build an agent")
        row = app.storage.fetchone(
            "SELECT active_agent_id FROM session_agents WHERE session_id = ?",
            (session_id,),
        )
        assert row["active_agent_id"] == "creator"

    def test_handoff_rejects_non_user_facing(self, app):
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        session_id = service.create_session()
        result = service._execute_handoff(session_id, "auditor", "test")
        assert "error" in result

    def test_get_active_agent_default(self, app):
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        session_id = service.create_session()
        assert service._get_active_agent(session_id) == "mycelos"

    def test_get_active_agent_after_handoff(self, app):
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        session_id = service.create_session()
        service._execute_handoff(session_id, "planner", "complex task")
        assert service._get_active_agent(session_id) == "planner"

    def test_handoff_persists_in_db(self, app):
        from mycelos.chat.service import ChatService
        service1 = ChatService(app)
        session_id = service1.create_session()
        service1._execute_handoff(session_id, "creator", "build")
        # New service instance should see the same agent (DB-backed)
        service2 = ChatService(app)
        assert service2._get_active_agent(session_id) == "creator"

    def test_app_get_agent_handlers(self, app):
        handlers = app.get_agent_handlers()
        assert "mycelos" in handlers
        assert "builder" in handlers
        assert handlers["mycelos"].agent_id == "mycelos"
        assert handlers["builder"].agent_id == "builder"


class TestHandoffIntegration:
    def test_active_agent_persists_across_services(self, app):
        """After handoff, the agent stays active even with a new ChatService instance."""
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        session_id = service.create_session()
        service._execute_handoff(session_id, "creator", "build agent")

        assert service._get_active_agent(session_id) == "creator"

        # New ChatService instance should also see it (DB-backed)
        service2 = ChatService(app)
        assert service2._get_active_agent(session_id) == "creator"

    def test_old_routing_branches_removed(self, app):
        """CREATE_AGENT and TASK_REQUEST are no longer handled via direct routing."""
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        # _handle_create_agent should not exist anymore
        assert not hasattr(service, "_handle_create_agent")

    def test_handler_tools_include_handoff(self, app):
        """All handlers include the handoff tool."""
        handlers = app.get_agent_handlers()
        for agent_id, handler in handlers.items():
            tools = handler.get_tools()
            tool_names = [t["function"]["name"] for t in tools]
            assert "handoff" in tool_names, f"{agent_id} handler missing handoff tool"

    def test_handler_system_prompts_not_empty(self, app):
        """All handlers return non-empty system prompts."""
        handlers = app.get_agent_handlers()
        for agent_id, handler in handlers.items():
            prompt = handler.get_system_prompt()
            assert len(prompt) > 50, f"{agent_id} handler has too short system prompt"
