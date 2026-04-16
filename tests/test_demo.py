"""Tests for the mycelos demo command."""

from click.testing import CliRunner

from mycelos.cli.demo_cmd import demo_cmd


def test_demo_help():
    runner = CliRunner()
    result = runner.invoke(demo_cmd, ["--help"])
    assert result.exit_code == 0
    assert "demo" in result.output.lower()


def test_demo_fast_runs_without_error():
    """Demo with --fast --lang en should complete without errors."""
    runner = CliRunner()
    result = runner.invoke(demo_cmd, ["--fast", "--lang", "en"])
    assert result.exit_code == 0, f"Demo failed: {result.output}"


def test_demo_fast_shows_banner():
    runner = CliRunner()
    result = runner.invoke(demo_cmd, ["--fast", "--lang", "en"])
    assert "M Y C E L O S" in result.output


def test_demo_fast_shows_key_sections():
    runner = CliRunner()
    result = runner.invoke(demo_cmd, ["--fast", "--lang", "en"])
    output = result.output
    assert "Setup" in output
    assert "Security" in output
    assert "Knowledge Base" in output
    assert "Multi-Channel" in output
    assert "Workflows" in output


def test_demo_fast_shows_security():
    runner = CliRunner()
    result = runner.invoke(demo_cmd, ["--fast", "--lang", "en"])
    output = result.output
    assert "SecurityProxy" in output or "Security" in output
    assert "Credentials" in output or "credential" in output.lower()


def test_demo_fast_shows_getting_started():
    runner = CliRunner()
    result = runner.invoke(demo_cmd, ["--fast", "--lang", "en"])
    output = result.output
    assert "pip install mycelos" in output
    assert "mycelos init" in output
    assert "mycelos serve" in output


def test_demo_fast_shows_github():
    runner = CliRunner()
    result = runner.invoke(demo_cmd, ["--fast", "--lang", "en"])
    assert "github" in result.output.lower()


def test_demo_fast_german():
    """German demo should also work."""
    runner = CliRunner()
    result = runner.invoke(demo_cmd, ["--fast", "--lang", "de"])
    assert result.exit_code == 0
    assert "M Y C E L O S" in result.output


def test_demo_no_setup_required():
    """Demo should work without mycelos init — no database needed."""
    runner = CliRunner()
    result = runner.invoke(demo_cmd, ["--fast", "--lang", "en"])
    assert result.exit_code == 0
    assert "mycelos.db" not in result.output.lower()
