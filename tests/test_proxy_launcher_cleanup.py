"""Socket cleanup must not remove another proxy's startup directory."""
import os
import time
from pathlib import Path

from mycelos.security import proxy_launcher


def test_cleanup_preserves_directory_before_socket_is_created(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy_launcher.tempfile, 'gettempdir', lambda: str(tmp_path))
    directory = Path(proxy_launcher.create_socket_dir())
    # Another launch runs cleanup before uvicorn binds the first socket.
    assert proxy_launcher.cleanup_stale_socket_dirs() == 0
    assert directory.is_dir()


def test_cleanup_preserves_live_owner_after_slow_start(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy_launcher.tempfile, 'gettempdir', lambda: str(tmp_path))
    directory = Path(proxy_launcher.create_socket_dir())
    old = time.time() - 3600
    os.utime(directory, (old, old))
    assert proxy_launcher.cleanup_stale_socket_dirs() == 0
    assert directory.is_dir()


def test_cleanup_removes_old_unowned_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy_launcher.tempfile, 'gettempdir', lambda: str(tmp_path))
    directory = tmp_path / 'mycelos-sec-orphan'
    directory.mkdir()
    old = time.time() - 3600
    os.utime(directory, (old, old))
    assert proxy_launcher.cleanup_stale_socket_dirs() == 1
    assert not directory.exists()


def test_cleanup_removes_old_directory_after_owner_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy_launcher.tempfile, 'gettempdir', lambda: str(tmp_path))
    directory = Path(proxy_launcher.create_socket_dir())
    old = time.time() - 3600
    os.utime(directory, (old, old))
    def exited(pid, signal):
        raise ProcessLookupError()
    monkeypatch.setattr(proxy_launcher.os, 'kill', exited)
    assert proxy_launcher.cleanup_stale_socket_dirs() == 1
    assert not directory.exists()


def test_cleanup_preserves_new_directory_before_owner_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy_launcher.tempfile, 'gettempdir', lambda: str(tmp_path))
    directory = tmp_path / 'mycelos-sec-new'
    directory.mkdir()
    assert proxy_launcher.cleanup_stale_socket_dirs() == 0
    assert directory.is_dir()
