"""Where the master key lives, and which process may read it.

Single-container / CLI installs keep the key next to the database
(``<data_dir>/.master_key``). In the two-container Docker deployment the
key belongs to the SecurityProxy alone: it sits in a separate directory
that only the proxy container mounts (``MYCELOS_MASTER_KEY_FILE``,
default ``/secrets/.master_key``), and the gateway — recognisable by
``MYCELOS_PROXY_URL`` — must never read, create or export it.
"""

from __future__ import annotations

import os
from pathlib import Path


def master_key_path(data_dir: Path) -> Path:
    """Path of the master key file for this process."""
    override = os.environ.get("MYCELOS_MASTER_KEY_FILE", "").strip()
    if override:
        return Path(override)
    return Path(data_dir) / ".master_key"


def key_held_by_external_proxy() -> bool:
    """True in the gateway of a two-container deployment.

    There the SecurityProxy runs in its own container and owns the key;
    every credential operation goes through it.
    """
    return bool(os.environ.get("MYCELOS_PROXY_URL", "").strip())
