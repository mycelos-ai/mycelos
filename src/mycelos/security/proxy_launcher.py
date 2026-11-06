"""SecurityProxy Launcher — fork, health check, auto-restart."""

import atexit
import glob
import logging
import os
import secrets
import shutil
import signal
import tempfile
import time
from multiprocessing import Process
from pathlib import Path
from typing import Any

logger = logging.getLogger("mycelos.proxy")


def generate_session_token() -> str:
    """Generate a random 32-byte hex session token."""
    return secrets.token_hex(32)


def create_socket_dir() -> str:
    """Create a secure temporary directory for the proxy socket.

    Uses the system temp directory (not CWD) to avoid littering the repo.
    Returns the directory path. The caller is responsible for cleanup.
    """
    system_tmp = tempfile.gettempdir()
    sock_dir = tempfile.mkdtemp(prefix="mycelos-sec-", dir=system_tmp)
    os.chmod(sock_dir, 0o700)
    Path(sock_dir, "owner.pid").write_text(str(os.getpid()))
    return sock_dir


def cleanup_stale_socket_dirs() -> int:
    """Remove stale mycelos-sec-* dirs from the system temp directory.

    A directory is stale only when its socket is absent, its owner is gone,
    and it is at least one minute old. The age guard protects creation of
    the owner marker. Returns the number of directories removed.
    """
    system_tmp = tempfile.gettempdir()
    count = 0
    for pattern in ("mycelos-sec-*", "maicel-sec-*"):
        for d in glob.glob(os.path.join(system_tmp, pattern)):
            directory = Path(d)
            try:
                if directory.is_symlink() or not directory.is_dir():
                    continue
                if (directory / "proxy.sock").exists():
                    continue
                if time.time() - directory.stat().st_mtime < 60:
                    continue
                try:
                    owner_pid = int((directory / "owner.pid").read_text().strip())
                except FileNotFoundError:
                    owner_pid = None  # Directory from an older launcher.
                if owner_pid is not None:
                    if owner_pid <= 0:
                        continue
                    try:
                        os.kill(owner_pid, 0)
                    except ProcessLookupError:
                        pass
                    else:
                        continue
                shutil.rmtree(directory)
                count += 1
            except (OSError, ValueError):
                # Permission errors and concurrent cleanup cannot establish
                # that an owner has exited. Leave the directory alone.
                continue
    if count:
        logger.info("Cleaned up %d stale proxy socket directories", count)
    return count


def _run_proxy(socket_path: str) -> None:
    """Entry point for the proxy child process.

    Reads config from env vars:
    - MYCELOS_PROXY_TOKEN
    - MYCELOS_MASTER_KEY
    - MYCELOS_DB_PATH
    - MYCELOS_PROXY_SOCKET
    """
    import uvicorn
    from mycelos.security.proxy_server import create_proxy_app

    app = create_proxy_app()
    uvicorn.run(app, uds=socket_path, log_level="warning")


class ProxyLauncher:
    """Manages the SecurityProxy child process lifecycle."""

    MAX_RESTARTS = 3
    HEALTH_TIMEOUT = 5.0  # seconds to wait for health check
    HEALTH_INTERVAL = 0.1  # seconds between health polls

    def __init__(self, data_dir: Path, master_key: str):  # noqa: ARG002
        self._data_dir = data_dir
        # H-01 fix: Do NOT cache master_key as an instance attribute.
        # It is read from .master_key file each time it's needed.
        self._process: Process | None = None
        self._socket_dir: str | None = None
        self._socket_path: str | None = None
        self._session_token: str | None = None
        self._restart_count = 0

    def _read_master_key(self) -> str:
        """Read master key from the .master_key file on disk.

        This avoids keeping the key in Python memory for the process lifetime.
        """
        key_path = self._data_dir / ".master_key"
        return key_path.read_text().strip()

    @property
    def socket_path(self) -> str | None:
        return self._socket_path

    @property
    def session_token(self) -> str | None:
        return self._session_token

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def start(self) -> None:
        """Start the SecurityProxy child process."""
        # Clean up stale dirs from previous runs that weren't shut down cleanly
        cleanup_stale_socket_dirs()

        self._session_token = generate_session_token()
        self._socket_dir = create_socket_dir()
        self._socket_path = os.path.join(self._socket_dir, "proxy.sock")

        # Set env vars for child process
        # Note: multiprocessing.Process inherits env from parent.
        # We set env vars before forking, then clear master key from parent after.
        os.environ["MYCELOS_MASTER_KEY"] = self._read_master_key()
        os.environ["MYCELOS_PROXY_TOKEN"] = self._session_token
        os.environ["MYCELOS_PROXY_SOCKET"] = self._socket_path
        os.environ["MYCELOS_DB_PATH"] = str(self._data_dir / "mycelos.db")

        self._process = Process(
            target=_run_proxy,
            args=(self._socket_path,),
            daemon=True,
            name="mycelos-security-proxy",
        )
        self._process.start()

        # Clear master key from parent process env
        if "MYCELOS_MASTER_KEY" in os.environ:
            del os.environ["MYCELOS_MASTER_KEY"]

        # Wait for health check
        self._wait_for_health()

        # Register cleanup for graceful and ungraceful shutdown
        atexit.register(self.stop)

        logger.info(
            "SecurityProxy started (pid=%s, socket=%s)",
            self._process.pid,
            self._socket_path,
        )

    def _wait_for_health(self) -> None:
        """Poll /health until proxy is ready or timeout."""
        import httpx

        deadline = time.time() + self.HEALTH_TIMEOUT
        while time.time() < deadline:
            if not self.is_running:
                raise RuntimeError("SecurityProxy process died during startup")
            try:
                client = httpx.Client(
                    transport=httpx.HTTPTransport(uds=self._socket_path),
                    base_url="http://proxy",
                )
                resp = client.get(
                    "/health",
                    headers={"Authorization": f"Bearer {self._session_token}"},
                    timeout=1.0,
                )
                client.close()
                if resp.status_code == 200:
                    return
            except (httpx.ConnectError, httpx.ConnectTimeout, OSError):
                pass
            time.sleep(self.HEALTH_INTERVAL)

        raise RuntimeError("SecurityProxy did not become healthy within timeout")

    def stop(self) -> None:
        """Stop the proxy process and clean up."""
        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
            if self._process.is_alive():
                self._process.kill()

        if self._socket_dir and os.path.exists(self._socket_dir):
            shutil.rmtree(self._socket_dir, ignore_errors=True)

        self._process = None
        logger.info("SecurityProxy stopped")

    def restart(self) -> None:
        """Stop and restart the proxy."""
        self.stop()
        self._restart_count += 1
        # Re-read master key from file for the new child (it was cleared from parent env after first start)
        os.environ["MYCELOS_MASTER_KEY"] = self._read_master_key()
        self.start()
        logger.info("SecurityProxy restarted (attempt %d)", self._restart_count)

    def ensure_alive(self) -> bool:
        """Check if proxy is alive, auto-restart if dead. Returns True if running."""
        if self.is_running:
            return True

        if self._restart_count >= self.MAX_RESTARTS:
            logger.critical(
                "SecurityProxy exceeded max restarts (%d) — degraded mode",
                self.MAX_RESTARTS,
            )
            return False

        try:
            self.restart()
            return True
        except Exception as e:
            logger.error("Failed to restart SecurityProxy: %s", e)
            self._restart_count += 1
            return False
