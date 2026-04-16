"""Live integration test for MCP connector lifecycle.

Tests the full flow:
1. Register a custom MCP connector via slash command
2. Gateway starts the MCP server
3. Tools are discovered
4. Tools can be called

Requires a running Gateway (mycelos serve) with the latest code.
Run: pytest -m integration tests/integration/test_mcp_connector_live.py -v -s
"""

import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def app(integration_app):
    """Use the isolated integration_app fixture (temp DB + creds from .env.test)."""
    return integration_app


@pytest.mark.integration
class TestConnectorRegistration:
    """Test connector registration via slash command."""

    def test_add_custom_mcp_connector(self, app):
        """Register a custom MCP connector via /connector add."""
        from mycelos.chat.slash_commands import handle_slash_command

        # Remove if exists from previous test
        try:
            app.connector_registry.remove("test-echo-mcp")
        except Exception:
            pass

        result = handle_slash_command(app, "/connector add test-echo-mcp npx -y @anthropic/echo-mcp-server")

        print(f"\nAdd result: {result}", file=sys.stderr)
        # Result may be a string or a ChatEvent list
        result_text = str(result)
        assert "registered" in result_text.lower() or "connector" in result_text.lower()

        # Verify in DB
        connector = app.connector_registry.get("test-echo-mcp")
        assert connector is not None
        assert connector["connector_type"] == "mcp"

        # Cleanup
        app.connector_registry.remove("test-echo-mcp")

    def test_playwright_registered_correctly(self, app):
        """Verify Playwright connector is registered with correct command."""
        connector = app.connector_registry.get("playwright")
        if connector is None:
            pytest.skip("Playwright connector not registered — run /connector add playwright npx -y @playwright/mcp first")

        assert connector["connector_type"] == "mcp"
        # Description is now user-friendly from curated recipes
        desc = connector.get("description", "").lower()
        assert "playwright" in desc or "browser" in desc
        print(f"\nPlaywright connector: {connector.get('description', '')}", file=sys.stderr)


@pytest.mark.integration
class TestMCPToolDiscovery:
    """Test that MCP tools are discovered and callable."""

    def test_connector_tools_discovers_playwright(self, app):
        """connector_tools("playwright") returns discovered tools.

        Note: This requires the Gateway to be running with Playwright started.
        If running standalone (no Gateway), the MCP manager won't be available.
        """
        from mycelos.chat.service import ChatService

        svc = ChatService(app)
        result = svc._execute_tool("connector_tools", {"connector_id": "playwright"})

        print(f"\nPlaywright tools: {result}", file=sys.stderr)

        if "error" in result and "not running" in str(result.get("error", "")):
            pytest.skip("MCP manager not running (need Gateway)")

        if "error" in result and "No MCP connectors" in str(result.get("error", "")):
            pytest.skip("No MCP connectors running (need Gateway)")

        assert "tools" in result, f"Expected tools, got: {result}"
        assert result.get("tool_count", 0) > 0, f"No tools discovered: {result}"

        print(f"Discovered {result['tool_count']} Playwright tools:", file=sys.stderr)
        for t in result["tools"][:5]:
            print(f"  - {t['name']}: {t.get('description', '')[:60]}", file=sys.stderr)


@pytest.mark.integration
class TestCredentialForConnector:
    """Test how credentials work with connectors."""

    def test_connector_with_token(self, app):
        """Connectors that need tokens store them via credential system.

        Flow:
        1. /connector add telegram <bot_token>
        2. Token stored in credentials table as 'connector:telegram'
        3. On MCP start, token injected as env var

        This test verifies the credential storage path.
        """
        # Check if telegram has a credential
        cred = app.credentials.get_credential("telegram")
        if cred is None:
            pytest.skip("No telegram credential stored")

        assert "api_key" in cred or "token" in cred or "bot_token" in cred
        print(f"\nTelegram credential keys: {list(cred.keys())}", file=sys.stderr)

    def test_connector_without_token(self, app):
        """Some connectors (filesystem, playwright) don't need credentials."""
        # Playwright is a no-auth MCP server
        connector = app.connector_registry.get("playwright")
        if connector is None:
            pytest.skip("Playwright not registered")

        # No credential should be needed
        cred = app.credentials.get_credential("connector:playwright")
        # It's OK if there's no credential — Playwright doesn't need one
        print(f"\nPlaywright credential: {cred}", file=sys.stderr)
        print("(None is expected — Playwright doesn't need auth)", file=sys.stderr)
