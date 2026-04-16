"""Live MCP integration tests — requires Node.js (npx).

These tests connect to REAL MCP servers via stdio.
Skip if npx is not available.
"""

from __future__ import annotations

import shutil

import pytest

from mycelos.connectors.mcp_manager import MCPConnectorManager
from mycelos.connectors.mcp_recipes import is_node_available

def _npx_can_reach_npm() -> bool:
    """Check if npx can download packages (network available)."""
    if not is_node_available():
        return False
    # Quick network check — try to resolve registry.npmjs.org
    import socket
    try:
        socket.setdefaulttimeout(2)
        socket.getaddrinfo("registry.npmjs.org", 443)
        return True
    except (socket.gaierror, socket.timeout, OSError):
        return False


# Skip if no Node.js or no network
pytestmark = pytest.mark.skipif(
    not _npx_can_reach_npm(),
    reason="Node.js (npx) not available or no network — MCP tests skipped",
)


@pytest.fixture
def mgr() -> MCPConnectorManager:
    m = MCPConnectorManager()
    yield m
    m.disconnect_all()


class TestMCPFilesystemServer:
    """Test against the real @modelcontextprotocol/server-filesystem."""

    def test_connect_and_discover_tools(self, mgr):
        tools = mgr.connect(
            "fs-test",
            "npx -y @modelcontextprotocol/server-filesystem /tmp",
        )
        assert len(tools) > 0
        tool_names = [t["name"] for t in tools]
        assert any("list_directory" in n for n in tool_names)
        assert any("read" in n for n in tool_names)

    def test_list_directory(self, mgr):
        mgr.connect("fs-test", "npx -y @modelcontextprotocol/server-filesystem /tmp")
        result = mgr.call_tool("fs-test.list_directory", {"path": "/tmp"})
        assert result is not None
        assert "error" not in str(result).lower() or "ENOENT" not in str(result)

    def test_tool_count(self, mgr):
        mgr.connect("fs-test", "npx -y @modelcontextprotocol/server-filesystem /tmp")
        assert mgr.tool_count >= 10

    def test_list_connected(self, mgr):
        mgr.connect("fs-test", "npx -y @modelcontextprotocol/server-filesystem /tmp")
        assert "fs-test" in mgr.list_connected()

    def test_disconnect(self, mgr):
        mgr.connect("fs-test", "npx -y @modelcontextprotocol/server-filesystem /tmp")
        mgr.disconnect_all()
        assert mgr.list_connected() == []
        assert mgr.tool_count == 0


class TestMCPManagerErrors:

    def test_unknown_tool(self, mgr):
        result = mgr.call_tool("nonexistent.tool", {})
        assert "error" in result

    def test_connect_bad_command(self, mgr):
        """Bad command should raise or return error."""
        with pytest.raises(Exception):
            mgr.connect("bad", "nonexistent-command-xyz")
