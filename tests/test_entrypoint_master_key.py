"""docker-entrypoint.sh: the proxy container owns the master key.

Review finding: docker-compose mounted ./data into both containers and
the key lived there, so the gateway could read it. The proxy entrypoint
now keeps the key in its own secrets mount and moves a key left in /data
by an older install. These tests run the entrypoint's
``prepare_proxy_key`` function in isolation (chown to the container user
fails harmlessly outside Docker).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[1] / "docker-entrypoint.sh"


def _function_source() -> str:
    text = ENTRYPOINT.read_text()
    match = re.search(r"^prepare_proxy_key\(\) \{.*?^\}\n", text, re.S | re.M)
    assert match, "prepare_proxy_key not found in docker-entrypoint.sh"
    return match.group(0)


def _run(data: Path, key_file: Path | None) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ["PATH"], "DATA_DIR": str(data)}
    if key_file is not None:
        env["MYCELOS_MASTER_KEY_FILE"] = str(key_file)
    script = _function_source() + "\nprepare_proxy_key\n"
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "data"
    data.mkdir()
    return data, tmp_path / "secrets" / ".master_key"


def test_first_start_generates_key_outside_data_dir(dirs) -> None:
    data, key = dirs
    result = _run(data, key)
    assert result.returncode == 0, result.stderr
    assert len(key.read_text().strip()) >= 32
    assert not (data / ".master_key").exists()
    assert oct(key.stat().st_mode & 0o777) == "0o600"


def test_legacy_key_is_moved_verbatim(dirs) -> None:
    data, key = dirs
    (data / ".master_key").write_text("legacy-key-abc\n")
    result = _run(data, key)
    assert result.returncode == 0, result.stderr
    assert key.read_text() == "legacy-key-abc\n"
    assert not (data / ".master_key").exists()


def test_existing_key_is_kept(dirs) -> None:
    data, key = dirs
    key.parent.mkdir()
    key.write_text("stored-key\n")
    assert _run(data, key).returncode == 0
    assert key.read_text() == "stored-key\n"


def test_identical_duplicate_is_removed(dirs) -> None:
    data, key = dirs
    key.parent.mkdir()
    key.write_text("same\n")
    (data / ".master_key").write_text("same\n")
    assert _run(data, key).returncode == 0
    assert not (data / ".master_key").exists()


def test_two_different_keys_refuse_to_start(dirs) -> None:
    data, key = dirs
    key.parent.mkdir()
    key.write_text("new\n")
    (data / ".master_key").write_text("old\n")
    result = _run(data, key)
    assert result.returncode != 0
    assert "two different master keys" in result.stderr
    # Nothing destroyed.
    assert key.read_text() == "new\n"
    assert (data / ".master_key").read_text() == "old\n"


def test_old_compose_without_secrets_mount_keeps_working(dirs) -> None:
    data, _ = dirs
    (data / ".master_key").write_text("legacy\n")
    result = _run(data, None)
    assert result.returncode == 0
    assert (data / ".master_key").read_text() == "legacy\n"
    assert "gateway container can read it" in result.stderr
