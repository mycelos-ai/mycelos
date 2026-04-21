"""CLI helpers shared across all mycelos-cli subcommands."""

from __future__ import annotations

import os
from pathlib import Path


def default_data_dir() -> Path:
    """Resolve the Mycelos data directory.

    Precedence:
      1. ``$MYCELOS_DATA_DIR`` — set by the Docker entrypoint to ``/data``,
         so `mycelos config list` Just Works inside the container without
         every invocation needing ``--data-dir /data``.
      2. ``~/.mycelos`` — the legacy single-container / local-dev default.
    """
    override = os.environ.get("MYCELOS_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".mycelos"
