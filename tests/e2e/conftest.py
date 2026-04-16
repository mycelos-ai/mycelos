"""E2E test fixtures — Playwright browser + live Mycelos server.

Usage:
    pytest tests/e2e/ -v                  # Run E2E tests
    pytest tests/e2e/ -v --headed         # Run with visible browser
    pytest tests/e2e/ -v --slowmo 500     # Slow down for debugging

Requirements:
    pip install -e ".[dev]"
    python -m playwright install chromium
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from pathlib import Path

import pytest
import uvicorn


# ---------------------------------------------------------------------------
# Server fixture: fresh DB, real FastAPI, random port
# ---------------------------------------------------------------------------

class _ServerThread(threading.Thread):
    """Run uvicorn in a background thread."""

    def __init__(self, app, host: str, port: int):
        super().__init__(daemon=True)
        self.config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self.server = uvicorn.Server(self.config)

    def run(self):
        self.server.run()

    def stop(self):
        self.server.should_exit = True


def _find_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def e2e_server(tmp_path_factory):
    """Start a fresh Mycelos server for E2E testing.

    Creates a temporary data directory with:
    - Fresh SQLite database
    - Master key for credential encryption
    - Anthropic API key stored (from .env.test or env)
    - System agents + seed workflows registered

    Yields (base_url, data_dir) tuple.
    """
    data_dir = tmp_path_factory.mktemp("mycelos-e2e")

    # Set master key
    master_key = secrets.token_urlsafe(32)
    os.environ["MYCELOS_MASTER_KEY"] = master_key

    # Write master key file (init expects it)
    key_file = data_dir / ".master_key"
    key_file.write_text(master_key)
    key_file.chmod(0o600)

    # Initialize the app
    from mycelos.app import App
    app = App(data_dir)
    app.initialize_with_config(
        default_model="anthropic/claude-sonnet-4-6",
        provider="anthropic",
    )

    # Store API key if available (for real LLM calls)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        app.credentials.store_credential(
            "anthropic",
            {"api_key": api_key, "env_var": "ANTHROPIC_API_KEY", "provider": "anthropic"},
        )

    # Register system agents
    from mycelos.cli.init_cmd import _register_system_agents, _import_seed_workflows
    _register_system_agents(app)
    _import_seed_workflows(app)

    # Create initial config generation
    app.config.apply_from_state(
        state_manager=app.state_manager,
        description="E2E test setup",
        trigger="test",
    )

    # Create FastAPI app
    from mycelos.gateway.server import create_app
    fastapi_app = create_app(data_dir=data_dir, debug=False, no_scheduler=True)

    # Start server on random port
    port = _find_free_port()
    server = _ServerThread(fastapi_app, "127.0.0.1", port)
    server.start()

    # Wait for server to be ready
    import httpx
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(30):
        try:
            r = httpx.get(f"{base_url}/api/health", timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.3)
    else:
        raise RuntimeError("E2E server did not start within 10 seconds")

    yield base_url, data_dir

    # Teardown
    server.stop()
    os.environ.pop("MYCELOS_MASTER_KEY", None)


@pytest.fixture(scope="session")
def base_url(e2e_server) -> str:
    """Base URL for the E2E test server."""
    return e2e_server[0]


@pytest.fixture(scope="session")
def e2e_data_dir(e2e_server) -> Path:
    """Data directory for the E2E test server."""
    return e2e_server[1]


# ---------------------------------------------------------------------------
# Playwright fixtures (override pytest-playwright defaults with our base_url)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, base_url):
    """Configure Playwright browser context."""
    return {
        **browser_context_args,
        "base_url": base_url,
        "viewport": {"width": 1280, "height": 720},
    }
