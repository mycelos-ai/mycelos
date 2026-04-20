"""Tests for `mycelos serve --role {all,gateway,proxy}` dispatch."""

from __future__ import annotations

from click.testing import CliRunner

from mycelos.cli.serve_cmd import serve_cmd


def test_serve_accepts_role_all():
    """--role all is the default and does not change existing behavior."""
    runner = CliRunner()
    # --dry-run must be implemented; it validates config and exits 0
    result = runner.invoke(serve_cmd, ["--role", "all", "--dry-run"])
    assert result.exit_code == 0, result.output


def test_serve_accepts_role_proxy(tmp_path):
    """--role proxy requires .master_key in --data-dir and MYCELOS_PROXY_TOKEN."""
    (tmp_path / ".master_key").write_text("test-master-key-32-bytes-plus-extra")
    runner = CliRunner()
    result = runner.invoke(
        serve_cmd,
        [
            "--role", "proxy",
            "--data-dir", str(tmp_path),
            "--proxy-host", "127.0.0.1",
            "--proxy-port", "0",
            "--dry-run",
        ],
        env={"MYCELOS_PROXY_TOKEN": "t"},
    )
    assert result.exit_code == 0, result.output


def test_serve_role_proxy_fails_without_master_key(tmp_path):
    """Missing .master_key is a hard failure in proxy role."""
    runner = CliRunner()
    result = runner.invoke(
        serve_cmd,
        [
            "--role", "proxy",
            "--data-dir", str(tmp_path),
            "--proxy-port", "0",
            "--dry-run",
        ],
        env={"MYCELOS_PROXY_TOKEN": "t"},
    )
    assert result.exit_code != 0
    assert "master_key" in result.output.lower()


def test_serve_role_proxy_fails_without_token(tmp_path):
    """Missing MYCELOS_PROXY_TOKEN is a hard failure in proxy role."""
    (tmp_path / ".master_key").write_text("k")
    runner = CliRunner()
    result = runner.invoke(
        serve_cmd,
        [
            "--role", "proxy",
            "--data-dir", str(tmp_path),
            "--proxy-port", "0",
            "--dry-run",
        ],
        env={"MYCELOS_PROXY_TOKEN": ""},
    )
    assert result.exit_code != 0
    assert "token" in result.output.lower()


def test_serve_role_gateway_warns_without_proxy_url(tmp_path):
    """--role gateway without MYCELOS_PROXY_URL falls back to in-process proxy."""
    runner = CliRunner()
    result = runner.invoke(
        serve_cmd,
        ["--role", "gateway", "--data-dir", str(tmp_path), "--dry-run"],
        env={"MYCELOS_PROXY_URL": ""},
    )
    assert result.exit_code == 0
    assert "MYCELOS_PROXY_URL" in result.output or "in-process" in result.output.lower()
