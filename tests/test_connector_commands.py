"""Tests for /connector slash commands — add, add-custom, MCP detection."""

import os
import tempfile
from pathlib import Path

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


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-connector-cmds"
        a = App(Path(tmp))
        a.initialize()
        yield a


class TestConnectorAddMCP:
    """Test that /connector add detects MCP commands vs credentials."""

    def test_add_npx_command_registers_custom_connector(self, app):
        """/connector add playwright npx -y @playwright/mcp → custom MCP."""
        result = handle_slash_command(app, "/connector add playwright npx -y @playwright/mcp")
        assert "Custom connector" in _result_text(result)
        assert "playwright" in _result_text(result)
        assert "registered" in _result_text(result)

        # Verify in DB
        connector = app.connector_registry.get("playwright")
        assert connector is not None

    def test_add_docker_command_registers_custom_connector(self, app):
        """/connector add mydb docker run -i mcp/postgres → custom MCP."""
        result = handle_slash_command(app, "/connector add mydb docker run -i mcp/postgres")
        assert "Custom connector" in _result_text(result)
        assert "mydb" in _result_text(result)

    def test_add_python_command_registers_custom_connector(self, app):
        """/connector add myscript python3 -m my_mcp_server → custom MCP."""
        result = handle_slash_command(app, "/connector add myscript python3 -m my_mcp_server")
        assert "Custom connector" in _result_text(result)

    def test_add_uvx_command_registers_custom_connector(self, app):
        """/connector add tool uvx some-mcp-tool → custom MCP."""
        result = handle_slash_command(app, "/connector add tool uvx some-mcp-tool")
        assert "Custom connector" in _result_text(result)

    def test_add_token_treated_as_credential(self, app):
        """/connector add telegram 12345:ABCdef → treated as credential, not MCP."""
        result = handle_slash_command(app, "/connector add telegram 12345:ABCdefGHI")
        # This should NOT register as custom MCP
        assert "Custom connector" not in _result_text(result)

    def test_add_blocked_command_not_treated_as_mcp(self, app):
        """bash is not in the launcher allowlist — treated as credential."""
        result = handle_slash_command(app, "/connector add evil bash -c bad")
        assert "Custom connector" not in _result_text(result)

    def test_add_bash_not_treated_as_mcp(self, app):
        """/connector add evil bash ... → not detected as MCP (bash not in launcher list)."""
        result = handle_slash_command(app, "/connector add evil bash -c bad")
        # bash is not in the MCP launcher allowlist, so treated as credential
        assert "Custom connector" not in _result_text(result)

    def test_add_mcp_with_secret(self, app):
        """/connector add context7 npx -y @upstash/context7-mcp --secret MY_KEY."""
        result = handle_slash_command(
            app, "/connector add context7 npx -y @upstash/context7-mcp --secret test-api-key-123"
        )
        assert "Custom connector" in _result_text(result)
        assert "context7" in _result_text(result)
        assert "Secret stored" in _result_text(result)

        # Verify connector registered
        connector = app.connector_registry.get("context7")
        assert connector is not None
        assert "npx" in connector.get("description", "")

        # Verify secret stored — since b365963, MCP creds live under the
        # bare connector id (same namespace as channels + builtins).
        cred = app.credentials.get_credential("context7")
        assert cred is not None
        assert cred["api_key"] == "test-api-key-123"

    def test_add_builtin_with_secret_flag(self, app):
        """/connector add telegram --secret <token> → built-in with token."""
        result = handle_slash_command(app, "/connector add telegram --secret 12345:TestToken")
        # Should be treated as credential for built-in connector
        assert "Custom connector" not in _result_text(result)

    def test_add_secret_at_end_of_command(self, app):
        """--secret can appear after the command args."""
        result = handle_slash_command(
            app, "/connector add myserver npx -y @some/server --secret abc123"
        )
        assert "Custom connector" in _result_text(result)
        assert "Secret stored" in _result_text(result)

    def test_add_shell_injection_rejected(self, app):
        """Commands with shell metacharacters are rejected."""
        result = handle_slash_command(app, "/connector add evil npx foo; rm -rf /")
        assert "forbidden" in _result_text(result).lower() or "Invalid" in _result_text(result)


class TestConnectorList:
    def test_list_shows_connectors(self, app):
        result = handle_slash_command(app, "/connector list")
        assert "Connectors" in _result_text(result) or "connector" in _result_text(result).lower()


class TestConnectorSearch:
    def test_search_returns_results(self, app):
        """Note: This makes a real HTTP call to the MCP registry."""
        result = handle_slash_command(app, "/connector search playwright")
        # May or may not find results depending on network
        assert isinstance(result, str)
        assert len(result) > 10
