"""Security boundary tests for sandbox isolation (SEC05 + SEC06).

NOTE: LocalSandbox is NOT a security boundary. Some of these tests
document known limitations rather than enforcing guarantees.
DockerSandbox (Phase 2.4) will enforce these properly.
"""

import os

import pytest

from mycelos.execution.sandbox import LocalSandbox, SandboxConfig


@pytest.fixture
def sandbox() -> LocalSandbox:
    return LocalSandbox()


# ── SEC05: Environment Stripping ──


def test_sec05_env_has_no_master_key(sandbox: LocalSandbox) -> None:
    """SEC05: MYCELOS_MASTER_KEY must NOT be in agent environment."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-that-must-not-leak"
    try:
        config = SandboxConfig(agent_id="agent", session_token="tok")
        sandbox_id = sandbox.create(config)
        result = sandbox.execute(sandbox_id, ["env"], config)
        assert "MYCELOS_MASTER_KEY" not in result.stdout
        assert "test-key-that-must-not-leak" not in result.stdout
        sandbox.cleanup(sandbox_id)
    finally:
        del os.environ["MYCELOS_MASTER_KEY"]


def test_sec05_env_has_no_api_keys(sandbox: LocalSandbox) -> None:
    """SEC05: No API key env vars in agent environment."""
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-secret"
    os.environ["OPENAI_API_KEY"] = "sk-openai-test-secret"
    try:
        config = SandboxConfig(agent_id="agent", session_token="tok")
        sandbox_id = sandbox.create(config)
        result = sandbox.execute(sandbox_id, ["env"], config)
        assert "ANTHROPIC_API_KEY" not in result.stdout
        assert "OPENAI_API_KEY" not in result.stdout
        assert "sk-ant-test-secret" not in result.stdout
        assert "sk-openai-test-secret" not in result.stdout
        sandbox.cleanup(sandbox_id)
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)


def test_sec05_session_token_is_only_credential(sandbox: LocalSandbox) -> None:
    """SEC05: MYCELOS_SESSION_TOKEN is the only credential-like var in agent env."""
    config = SandboxConfig(agent_id="agent", session_token="unique-session-abc")
    sandbox_id = sandbox.create(config)
    result = sandbox.execute(sandbox_id, ["env"], config)
    assert "MYCELOS_SESSION_TOKEN=unique-session-abc" in result.stdout
    # Count MYCELOS_ vars — should only be SESSION_TOKEN, INPUT, WORKSPACE, OUTPUT
    mycelos_vars = [l for l in result.stdout.split("\n") if l.startswith("MYCELOS_")]
    expected_vars = {"MYCELOS_SESSION_TOKEN", "MYCELOS_INPUT", "MYCELOS_WORKSPACE", "MYCELOS_OUTPUT"}
    actual_vars = {l.split("=")[0] for l in mycelos_vars if "=" in l}
    assert actual_vars == expected_vars
    sandbox.cleanup(sandbox_id)


def test_sec05_home_is_sandboxed(sandbox: LocalSandbox) -> None:
    """SEC05: HOME points to sandbox workspace, not user's home."""
    config = SandboxConfig(agent_id="agent", session_token="tok")
    sandbox_id = sandbox.create(config)
    result = sandbox.execute(sandbox_id, ["sh", "-c", "echo $HOME"], config)
    home_dir = result.stdout.strip()
    assert "workspace" in home_dir
    assert home_dir != os.path.expanduser("~")
    sandbox.cleanup(sandbox_id)


def test_sec05_path_is_restricted(sandbox: LocalSandbox) -> None:
    """SEC05: PATH is restricted to standard system paths."""
    config = SandboxConfig(agent_id="agent", session_token="tok")
    sandbox_id = sandbox.create(config)
    result = sandbox.execute(sandbox_id, ["sh", "-c", "echo $PATH"], config)
    path = result.stdout.strip()
    assert "/usr/bin" in path
    # Should NOT contain user-specific paths
    assert ".local/bin" not in path
    assert "pyenv" not in path
    sandbox.cleanup(sandbox_id)


# ── SEC06: Network Isolation (LocalSandbox limitation) ──


def test_sec06_local_sandbox_network_limitation() -> None:
    """SEC06: LocalSandbox does NOT isolate network (documented limitation).

    This test documents that LocalSandbox can make network calls.
    DockerSandbox (Phase 2.4) will enforce network isolation via Network Namespaces.
    """
    # This is a documentation test — it passes to remind us of the limitation
    sandbox = LocalSandbox()
    config = SandboxConfig(agent_id="agent", session_token="tok", timeout_seconds=3)
    sandbox_id = sandbox.create(config)
    # Try a network call — this WILL succeed in LocalSandbox (known limitation)
    # We test that at least the env is stripped (no auth for external services)
    result = sandbox.execute(sandbox_id, ["env"], config)
    # No proxy env vars that could be used for network auth
    assert "HTTP_PROXY" not in result.stdout
    assert "HTTPS_PROXY" not in result.stdout
    sandbox.cleanup(sandbox_id)
