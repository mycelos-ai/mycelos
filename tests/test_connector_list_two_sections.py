"""`mycelos connector list` renders Installed / Channels / MCP Connectors sections."""

from __future__ import annotations

import os
from pathlib import Path

from click.testing import CliRunner

from mycelos.cli.connector_cmd import connector_cmd


def test_list_empty_shows_channels_and_mcp_sections(tmp_data_dir: Path) -> None:
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-list-sections"
    try:
        App(tmp_data_dir).initialize()
        runner = CliRunner()
        result = runner.invoke(connector_cmd, ["list", "--data-dir", str(tmp_data_dir)])
        assert result.exit_code == 0
        assert "Channels" in result.output
        assert "MCP Connectors" in result.output
        # Telegram must be under Channels, github under MCP Connectors.
        channels_idx = result.output.find("Channels")
        mcp_idx = result.output.find("MCP Connectors")
        assert channels_idx >= 0 and mcp_idx > channels_idx
        tg_idx = result.output.find("telegram")
        gh_idx = result.output.find("github")
        assert channels_idx < tg_idx < mcp_idx, "telegram must appear under Channels"
        assert mcp_idx < gh_idx, "github must appear under MCP Connectors"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_list_drops_kind_column(tmp_data_dir: Path) -> None:
    """The available-recipes tables no longer have a Kind column (sections do that job)."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-list-nokind"
    try:
        App(tmp_data_dir).initialize()
        runner = CliRunner()
        result = runner.invoke(connector_cmd, ["list", "--data-dir", str(tmp_data_dir)])
        # The word "Kind" appears only in the Installed table header, not in
        # the available-recipes tables. When no connectors are configured, the
        # Installed table isn't shown — so the string shouldn't be present at all.
        assert "Kind" not in result.output, (
            "Kind column should not appear in available-recipes tables; "
            "sections replace it."
        )
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)
