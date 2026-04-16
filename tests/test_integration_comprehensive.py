"""Integration tests — slash commands, onboarding, streaming, system status.

Tests for /memory, /mount, /connector, /cost are in their dedicated test files:
  - test_memory_system.py — /memory slash commands
  - test_filesystem_mounts.py — /mount slash commands + mount operations
  - test_connector_commands.py — /connector slash commands
  - test_cost_tracking.py — /cost slash commands + LLM usage tracking
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App
from mycelos.chat.confirmable import extract_suggested_commands, format_confirmable
from mycelos.chat.service import ChatService
from mycelos.chat.slash_commands import handle_slash_command
from mycelos.llm.broker import LiteLLMBroker
from mycelos.security.mounts import MountRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    """Fresh Mycelos App with temp directory."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-integration"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def test_dir(tmp_path: Path) -> Path:
    """Directory with sample files for mount tests."""
    d = tmp_path / "testfiles"
    d.mkdir()
    (d / "report.txt").write_text("Revenue Q1: 500k EUR")
    (d / "notes.md").write_text("# Meeting Notes\n\n- Item 1\n- Item 2")
    (d / "data.csv").write_text("name,value\nalpha,1\nbeta,2")
    (d / "subdir").mkdir()
    (d / "subdir" / "deep.txt").write_text("deep content")
    return d


@pytest.fixture
def chat_service(app) -> ChatService:
    return ChatService(app)


# ===========================================================================
# 1. SLASH COMMANDS (unique to this file — no dedicated test file)
# ===========================================================================

class TestSlashHelp:

    def test_help_returns_useful_content(self, app):
        result = handle_slash_command(app, "/help")
        assert "/memory" in result
        assert "/cost" in result
        assert "/mount" in result
        assert "/config" in result
        assert "/agent" in result
        assert "/connector" in result
        assert "/schedule" in result
        assert "/workflow" in result
        assert "/model" in result
        assert "/sessions" in result

    def test_help_shows_subcommands(self, app):
        result = handle_slash_command(app, "/help")
        assert "memory list" in result
        assert "cost" in result
        assert "mount add" in result


class TestSlashAgent:

    def test_agent_list_empty(self, app):
        result = handle_slash_command(app, "/agent list")
        assert "No agents" in result or "no agents" in result.lower()

    def test_agent_list_with_agents(self, app):
        app.agent_registry.register("news-agent", "News Agent", "llm", ["search.web"], "system")
        result = handle_slash_command(app, "/agent list")
        assert "news-agent" in result
        assert "search.web" in result

    def test_agent_info(self, app):
        app.agent_registry.register("news-agent", "News Agent", "llm", ["search.web"], "system")
        result = handle_slash_command(app, "/agent news-agent info")
        assert "News Agent" in result
        assert "search.web" in result
        assert "Reputation" in result or "reputation" in result.lower()

    def test_agent_grant(self, app):
        app.agent_registry.register("test-agent", "Test", "deterministic", [], "system")
        result = handle_slash_command(app, "/agent test-agent grant filesystem.read")
        assert "Granted" in result or "granted" in result.lower()
        agent = app.agent_registry.get("test-agent")
        assert "filesystem.read" in agent["capabilities"]

    def test_agent_grant_creates_config_generation(self, app):
        app.agent_registry.register("test-agent", "Test", "deterministic", [], "system")
        gen_before = app.config.get_active_generation_id()
        handle_slash_command(app, "/agent test-agent grant filesystem.read")
        gen_after = app.config.get_active_generation_id()
        assert gen_after != gen_before

    def test_agent_revoke(self, app):
        app.agent_registry.register("test-agent", "Test", "deterministic", ["search.web"], "system")
        result = handle_slash_command(app, "/agent test-agent revoke search.web")
        assert "Revoked" in result or "revoked" in result.lower()
        agent = app.agent_registry.get("test-agent")
        assert "search.web" not in agent["capabilities"]

    def test_agent_revoke_nonexistent_cap(self, app):
        app.agent_registry.register("test-agent", "Test", "deterministic", [], "system")
        result = handle_slash_command(app, "/agent test-agent revoke nonexistent")
        assert "doesn't have" in result

    def test_agent_not_found(self, app):
        result = handle_slash_command(app, "/agent ghost info")
        assert "not found" in result.lower()

    def test_agent_already_has_cap(self, app):
        app.agent_registry.register("test-agent", "Test", "deterministic", ["search.web"], "system")
        result = handle_slash_command(app, "/agent test-agent grant search.web")
        assert "already has" in result


class TestSlashConfig:

    def test_config_delegates(self, app):
        result = handle_slash_command(app, "/config")
        assert "Config" in result or "config" in result.lower() or "Generation" in result


class TestSlashSchedule:

    def test_schedule_list_empty(self, app):
        result = handle_slash_command(app, "/schedule list")
        assert "No scheduled" in result

    def test_schedule_list_with_tasks(self, app):
        app.workflow_registry.register(
            "news-summary", "News Summary",
            steps=[{"agent": "news-agent", "action": "search"}],
        )
        app.schedule_manager.add("news-summary", "0 8 * * *")
        result = handle_slash_command(app, "/schedule list")
        assert "news-summary" in result


class TestSlashWorkflow:

    def test_workflow_list_has_builtins(self, app):
        result = handle_slash_command(app, "/workflow list")
        assert "brainstorming-interview" in result

    def test_workflow_list_with_workflows(self, app):
        app.workflow_registry.register(
            "daily-news", "Daily News",
            steps=[{"agent": "a", "action": "b"}],
            description="Fetch daily news",
        )
        result = handle_slash_command(app, "/workflow list")
        assert "daily-news" in result

    def test_workflow_runs_empty(self, app):
        result = handle_slash_command(app, "/workflow runs")
        assert "No workflow runs" in result


class TestSlashModel:

    def test_model_list(self, app):
        result = handle_slash_command(app, "/model list")
        assert isinstance(result, str)
        assert len(result) > 0


class TestSlashSessions:

    def test_sessions_empty(self, app):
        result = handle_slash_command(app, "/sessions")
        assert "No sessions" in result


class TestSlashUnknown:

    def test_unknown_command(self, app):
        result = handle_slash_command(app, "/foobar")
        assert "Unknown command" in result
        assert "/help" in result

    def test_empty_command(self, app):
        result = handle_slash_command(app, "/")
        assert "Unknown" in result or "help" in result.lower()


# ===========================================================================
# 2. SYSTEM STATUS TOOL
# ===========================================================================

class TestSystemStatus:

    def test_empty_system_returns_empty_lists(self, chat_service):
        status = chat_service._get_system_status()
        assert "connectors" in status
        assert "mounts" in status
        assert "agents" in status
        assert "scheduled_tasks" in status
        assert "workflows" in status
        assert status["connectors"] == []
        assert status["mounts"] == []

    def test_status_includes_connectors(self, app, chat_service):
        app.connector_registry.register("github", "GitHub", "mcp", ["code.read"])
        status = chat_service._get_system_status()
        assert len(status["connectors"]) == 1
        assert status["connectors"][0]["id"] == "github"
        assert "code.read" in status["connectors"][0]["capabilities"]

    def test_status_includes_mounts(self, app, chat_service, test_dir):
        mounts = MountRegistry(app.storage)
        mounts.add(str(test_dir), "read")
        status = chat_service._get_system_status()
        assert len(status["mounts"]) == 1
        assert status["mounts"][0]["access"] == "read"

    def test_status_includes_agents(self, app, chat_service):
        app.agent_registry.register("news-agent", "News", "llm", ["search.web"], "system")
        app.storage.execute("UPDATE agents SET status = 'active' WHERE id = ?", ("news-agent",))
        status = chat_service._get_system_status()
        assert len(status["agents"]) >= 1
        agent_ids = [a["id"] for a in status["agents"]]
        assert "news-agent" in agent_ids

    def test_status_after_adding_connector(self, app, chat_service):
        status_before = chat_service._get_system_status()
        connector_count_before = len(status_before["connectors"])
        app.connector_registry.register("brave", "Brave Search", "mcp", ["search.web"])
        status_after = chat_service._get_system_status()
        assert len(status_after["connectors"]) == connector_count_before + 1


# ===========================================================================
# 3. CONFIRMABLE COMMANDS
# ===========================================================================

class TestConfirmableCommands:

    def test_extract_mount_add(self):
        text = "You can grant access with: `/mount add ~/Downloads --rw`"
        cmds = extract_suggested_commands(text)
        assert cmds == ["/mount add ~/Downloads --rw"]

    def test_extract_agent_grant(self):
        text = "Give permission: `/agent news-agent grant search.web`"
        cmds = extract_suggested_commands(text)
        assert cmds == ["/agent news-agent grant search.web"]

    def test_extract_connector_add(self):
        text = "Set it up with: `/connector add github`"
        cmds = extract_suggested_commands(text)
        assert cmds == ["/connector add github"]

    def test_extract_schedule_add(self):
        text = 'Schedule it: `/schedule add news-summary --cron "0 8 * * *"`'
        cmds = extract_suggested_commands(text)
        assert len(cmds) == 1
        assert "/schedule add" in cmds[0]

    def test_ignores_non_slash_backtick_content(self):
        text = "Install with `pip install mycelos` and run `python main.py`"
        cmds = extract_suggested_commands(text)
        assert cmds == []

    def test_multiple_commands(self):
        text = (
            "Run these:\n"
            "1. `/mount add ~/Documents --read`\n"
            "2. `/connector add github`\n"
            "3. `/agent news-agent grant search.web`\n"
        )
        cmds = extract_suggested_commands(text)
        assert len(cmds) == 3

    def test_ignores_plain_text_slashes(self):
        text = "The /home/user path is not a command."
        cmds = extract_suggested_commands(text)
        assert cmds == []

    def test_format_single_command(self):
        result = format_confirmable(["/mount add ~/docs --read"])
        assert "Suggested command" in result
        assert "/mount add" in result

    def test_format_multiple_commands(self):
        result = format_confirmable(["/mount add ~/a --read", "/connector add github"])
        assert "(1)" in result
        assert "(2)" in result

    def test_format_empty(self):
        assert format_confirmable([]) == ""

    def test_extract_memory_command(self):
        text = "Delete it: `/memory delete user.fact.temp`"
        cmds = extract_suggested_commands(text)
        assert cmds == ["/memory delete user.fact.temp"]

    def test_extract_workflow_command(self):
        text = "Check runs: `/workflow runs`"
        cmds = extract_suggested_commands(text)
        assert len(cmds) == 1
        assert "/workflow runs" in cmds[0]

    def test_extract_config_command(self):
        text = "Rollback: `/config rollback 3`"
        cmds = extract_suggested_commands(text)
        assert len(cmds) == 1

    def test_extract_model_command(self):
        text = "See models: `/model list`"
        cmds = extract_suggested_commands(text)
        assert len(cmds) == 1


# ===========================================================================
# 4. ONBOARDING DETECTION
# ===========================================================================

class TestOnboardingDetection:

    def test_new_user_triggers_onboarding(self, app, chat_service):
        prompt = chat_service.get_system_prompt(user_name=None)
        assert "NEW user" in prompt
        assert "Onboarding" in prompt or "name" in prompt.lower()

    def test_returning_user_normal_mode(self, app, chat_service):
        app.memory.set("default", "system", "user.name", "Stefan")
        prompt = chat_service.get_system_prompt(user_name="Stefan")
        assert "NEW user" not in prompt
        assert "Stefan" in prompt

    def test_name_saved_from_simple_input(self, app, chat_service):
        chat_service._try_save_name("Stefan")
        stored = app.memory.get("default", "system", "user.name")
        assert stored == "Stefan"

    def test_multi_word_name_saved(self, app, chat_service):
        chat_service._try_save_name("Stefan Mueller")
        stored = app.memory.get("default", "system", "user.name")
        assert stored == "Stefan Mueller"

    def test_non_name_input_not_saved(self, app, chat_service):
        chat_service._try_save_name("ich brauche hilfe mit code")
        stored = app.memory.get("default", "system", "user.name")
        assert stored is None


class TestFollowUpSuggestions:

    def test_no_telegram_suggests_telegram(self, app, chat_service):
        app.memory.set("default", "system", "user.name", "Stefan", created_by="test")
        suggestions = chat_service._get_follow_up_suggestions()
        assert "Telegram" in suggestions or "telegram" in suggestions.lower()

    def test_no_mounts_suggests_mount(self, app, chat_service):
        app.memory.set("default", "system", "user.name", "Stefan", created_by="test")
        suggestions = chat_service._get_follow_up_suggestions()
        assert "/mount add" in suggestions or "mount" in suggestions.lower()

    def test_no_schedules_suggests_schedule(self, app, chat_service):
        app.memory.set("default", "system", "user.name", "Stefan", created_by="test")
        suggestions = chat_service._get_follow_up_suggestions()
        assert "schedule" in suggestions.lower()

    def test_all_configured_no_suggestions(self, app, chat_service, test_dir):
        app.memory.set("default", "system", "user.name", "Stefan", created_by="test")
        mounts = MountRegistry(app.storage)
        mounts.add(str(test_dir), "read")
        app.workflow_registry.register(
            "test-wf", "Test", steps=[{"agent": "a", "action": "b"}]
        )
        app.schedule_manager.add("test-wf", "0 8 * * *")
        with patch.object(app.credentials, "get_credential", return_value={"api_key": "fake"}):
            suggestions = chat_service._get_follow_up_suggestions()
        assert suggestions == "" or suggestions is None or len(suggestions) == 0


# ===========================================================================
# 5. TOKEN STREAMING
# ===========================================================================

class TestTokenStreaming:

    def test_complete_stream_yields_chunks(self, app):
        broker = LiteLLMBroker(default_model="test-model", storage=app.storage)

        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta = MagicMock()
        chunk1.choices[0].delta.content = "Hello "
        chunk1.choices[0].delta.tool_calls = None
        chunk1.usage = None

        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta = MagicMock()
        chunk2.choices[0].delta.content = "world!"
        chunk2.choices[0].delta.tool_calls = None
        chunk2.usage = None

        chunk3 = MagicMock()
        chunk3.choices = [MagicMock()]
        chunk3.choices[0].delta = MagicMock()
        chunk3.choices[0].delta.content = None
        chunk3.choices[0].delta.tool_calls = None
        chunk3.usage = MagicMock()
        chunk3.usage.total_tokens = 50

        with patch("litellm.completion", return_value=iter([chunk1, chunk2, chunk3])):
            with patch("litellm.model_cost", {}):
                chunks = list(broker.complete_stream(
                    [{"role": "user", "content": "Hi"}]
                ))

        assert chunks == ["Hello ", "world!"]

    def test_streaming_tracks_tokens(self, app):
        broker = LiteLLMBroker(default_model="test-model", storage=app.storage)

        final_chunk = MagicMock()
        final_chunk.choices = [MagicMock()]
        final_chunk.choices[0].delta = MagicMock()
        final_chunk.choices[0].delta.content = "Done"
        final_chunk.choices[0].delta.tool_calls = None
        final_chunk.usage = MagicMock()
        final_chunk.usage.total_tokens = 75

        with patch("litellm.completion", return_value=iter([final_chunk])):
            with patch("litellm.model_cost", {"test-model": {
                "input_cost_per_token": 0.000001,
                "output_cost_per_token": 0.000001,
            }}):
                list(broker.complete_stream(
                    [{"role": "user", "content": "Hi"}]
                ))

        assert broker._last_stream_tokens == 75
        assert broker._last_stream_model == "test-model"

    def test_streaming_detects_tool_calls(self, app):
        broker = LiteLLMBroker(default_model="test-model", storage=app.storage)

        tool_chunk = MagicMock()
        tool_chunk.choices = [MagicMock()]
        tool_chunk.choices[0].delta = MagicMock()
        tool_chunk.choices[0].delta.content = None

        tc = MagicMock()
        tc.id = "call_123"
        tc.function = MagicMock()
        tc.function.name = "search_web"
        tc.function.arguments = '{"query": "test"}'
        tool_chunk.choices[0].delta.tool_calls = [tc]
        tool_chunk.usage = None

        end_chunk = MagicMock()
        end_chunk.choices = []
        end_chunk.usage = None

        with patch("litellm.completion", return_value=iter([tool_chunk, end_chunk])):
            with patch("litellm.model_cost", {}):
                list(broker.complete_stream(
                    [{"role": "user", "content": "search for AI"}]
                ))

        assert broker._last_stream_tool_calls is not None
        assert len(broker._last_stream_tool_calls) == 1
        assert broker._last_stream_tool_calls[0]["function"]["name"] == "search_web"

    def test_streaming_persists_cost(self, app):
        broker = LiteLLMBroker(default_model="test-model", storage=app.storage)

        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta = MagicMock()
        chunk.choices[0].delta.content = "Hi"
        chunk.choices[0].delta.tool_calls = None
        chunk.usage = MagicMock()
        chunk.usage.total_tokens = 100

        with patch("litellm.completion", return_value=iter([chunk])):
            with patch("litellm.model_cost", {"test-model": {
                "input_cost_per_token": 0.000003,
                "output_cost_per_token": 0.000015,
            }}):
                list(broker.complete_stream(
                    [{"role": "user", "content": "Hi"}]
                ))

        rows = app.storage.fetchall("SELECT * FROM llm_usage")
        assert len(rows) >= 1
        assert rows[-1]["total_tokens"] == 100


# ===========================================================================
# 6. CROSS-CUTTING INTEGRATION
# ===========================================================================

class TestCrossCuttingIntegration:

    def test_full_onboarding_flow_prompt_structure(self, app, chat_service):
        prompt_new = chat_service.get_system_prompt(user_name=None)
        assert "NEW user" in prompt_new
        app.memory.set("default", "system", "user.name", "Maria", created_by="test")
        prompt_returning = chat_service.get_system_prompt(user_name="Maria")
        assert "Maria" in prompt_returning
        assert "NEW user" not in prompt_returning

    def test_slash_command_does_not_leak_to_llm(self, app):
        commands = [
            "/help",
            "/memory list",
            "/mount list",
            "/cost",
            "/agent list",
            "/connector list",
            "/schedule list",
            "/workflow list",
            "/sessions",
        ]
        for cmd in commands:
            result = handle_slash_command(app, cmd)
            assert isinstance(result, str), f"Command {cmd} did not return string"
            assert len(result) > 0, f"Command {cmd} returned empty string"
