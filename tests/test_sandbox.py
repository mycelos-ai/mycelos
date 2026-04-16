"""Tests for LocalSandbox."""

import os
from pathlib import Path

import pytest

from mycelos.execution.sandbox import LocalSandbox, SandboxConfig


@pytest.fixture
def sandbox() -> LocalSandbox:
    return LocalSandbox()


@pytest.fixture
def config() -> SandboxConfig:
    return SandboxConfig(
        agent_id="test-agent",
        session_token="test-token-123",
        timeout_seconds=5,
    )


def test_create_sandbox(sandbox: LocalSandbox, config: SandboxConfig) -> None:
    sandbox_id = sandbox.create(config)
    assert sandbox_id is not None
    output_dir = sandbox.get_output_dir(sandbox_id)
    assert output_dir is not None
    assert output_dir.exists()
    sandbox.cleanup(sandbox_id)


def test_execute_simple_command(sandbox: LocalSandbox, config: SandboxConfig) -> None:
    sandbox_id = sandbox.create(config)
    result = sandbox.execute(sandbox_id, ["echo", "hello"], config)
    assert result.exit_code == 0
    assert "hello" in result.stdout
    sandbox.cleanup(sandbox_id)


def test_env_stripping(sandbox: LocalSandbox, config: SandboxConfig) -> None:
    """Agent process should only see MYCELOS_* env vars, not host secrets."""
    sandbox_id = sandbox.create(config)
    result = sandbox.execute(sandbox_id, ["env"], config)
    env_lines = result.stdout.strip().split("\n")
    env_keys = {line.split("=")[0] for line in env_lines if "=" in line}
    assert "MYCELOS_SESSION_TOKEN" in env_keys
    assert "MYCELOS_INPUT" in env_keys
    assert "MYCELOS_OUTPUT" in env_keys
    assert "ANTHROPIC_API_KEY" not in env_keys
    assert "MYCELOS_MASTER_KEY" not in env_keys
    sandbox.cleanup(sandbox_id)


def test_timeout_enforcement(sandbox: LocalSandbox) -> None:
    config = SandboxConfig(agent_id="slow", session_token="t", timeout_seconds=1)
    sandbox_id = sandbox.create(config)
    result = sandbox.execute(sandbox_id, ["sleep", "10"], config)
    assert result.timed_out is True
    assert result.exit_code == -1
    sandbox.cleanup(sandbox_id)


def test_cleanup_removes_dirs(sandbox: LocalSandbox, config: SandboxConfig) -> None:
    sandbox_id = sandbox.create(config)
    output_dir = sandbox.get_output_dir(sandbox_id)
    assert output_dir.exists()
    sandbox.cleanup(sandbox_id)
    assert not output_dir.exists()


def test_input_files_copied(sandbox: LocalSandbox, tmp_path: Path) -> None:
    input_file = tmp_path / "data.txt"
    input_file.write_text("input data")
    config = SandboxConfig(
        agent_id="test",
        session_token="t",
        input_files={"data.txt": str(input_file)},
    )
    sandbox_id = sandbox.create(config)
    result = sandbox.execute(sandbox_id, ["sh", "-c", "cat $MYCELOS_INPUT/data.txt"], config)
    assert "input data" in result.stdout
    sandbox.cleanup(sandbox_id)


def test_output_writable_via_env(sandbox: LocalSandbox, config: SandboxConfig) -> None:
    sandbox_id = sandbox.create(config)
    result = sandbox.execute(
        sandbox_id,
        ["sh", "-c", "echo 'result' > $MYCELOS_OUTPUT/result.txt && cat $MYCELOS_OUTPUT/result.txt"],
        config,
    )
    assert result.exit_code == 0
    assert "result" in result.stdout
    sandbox.cleanup(sandbox_id)
