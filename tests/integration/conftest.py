"""Integration test configuration.

Integration tests make REAL API calls and cost real money.
Skipped by default — run with: pytest -m integration tests/integration/ -v

Key management:
  - Create .env.test in project root with your API keys
  - Or set keys as environment variables
  - Tests skip automatically if required keys are missing
"""

import os
from pathlib import Path

import pytest

# Load .env.test if it exists
_ENV_TEST = Path(__file__).parent.parent.parent / ".env.test"
if _ENV_TEST.exists():
    for line in _ENV_TEST.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            value = value.strip()
            # Strip surrounding quotes (dotenv convention): VAR="foo bar" → foo bar
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        'integration: marks tests that make real API calls (deselect with \'-m "not integration"\')',
    )


def pytest_collection_modifyitems(config, items):
    """Skip integration tests unless explicitly requested."""
    if config.getoption("-m") and "integration" in config.getoption("-m"):
        return
    skip = pytest.mark.skip(reason="Run with: pytest -m integration")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def require_anthropic_key():
    """Skip if ANTHROPIC_API_KEY not set."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or key.startswith("sk-ant-your"):
        pytest.skip("ANTHROPIC_API_KEY not set (add to .env.test)")
    return key


@pytest.fixture
def require_brave_key():
    """Skip if BRAVE_API_KEY not set."""
    key = os.environ.get("BRAVE_API_KEY")
    if not key or key == "your-brave-key-here":
        pytest.skip("BRAVE_API_KEY not set (add to .env.test)")
    return key


@pytest.fixture
def integration_app(tmp_path, request):
    """Create a fully initialized App in a temp directory for integration testing."""
    from mycelos.app import App

    data_dir = tmp_path / "mycelos-test"
    os.environ["MYCELOS_MASTER_KEY"] = "integration-test-key-2026"

    app = App(data_dir)
    app.initialize_with_config(
        default_model="anthropic/claude-haiku-4-5",
        provider="anthropic",
    )

    # Install LLM cassette recorder. Mode is controlled by MYCELOS_LLM_CASSETTE
    # (default: replay). Cassette file is derived from the test nodeid so each
    # test owns its own recordings.
    from mycelos.llm.cassette import CassetteRecorder
    from pathlib import Path as _Path
    mode = os.environ.get("MYCELOS_LLM_CASSETTE", "replay")
    cassette_dir = _Path(__file__).parent.parent / "cassettes"
    # nodeid example: tests/integration/test_creator_agent.py::test_creates_simple_agent
    safe_nodeid = (
        request.node.nodeid
        .replace("tests/integration/", "")
        .replace("::", "__")
        .replace("/", "_")
        .replace(".py", "")
    )
    cassette_path = cassette_dir / f"{safe_nodeid}.json"
    app.llm._recorder = CassetteRecorder(cassette_path, mode=mode)

    # Store LLM credential if available
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        app.credentials.store_credential(
            "anthropic",
            {
                "api_key": api_key,
                "env_var": "ANTHROPIC_API_KEY",
                "provider": "anthropic",
            },
        )

    # Store Brave credential if available
    brave_key = os.environ.get("BRAVE_API_KEY")
    if brave_key:
        app.credentials.store_credential(
            "connector:web-search-brave",
            {
                "api_key": brave_key,
                "env_var": "BRAVE_API_KEY",
                "connector": "web-search-brave",
            },
        )

    # Store Telegram bot token if available (channel-type, bare name)
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if telegram_token:
        app.credentials.store_credential(
            "telegram",
            {"bot_token": telegram_token},
        )

    # Store Gmail credentials if available
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_password = os.environ.get("GMAIL_PASSWORD")
    if gmail_user and gmail_password:
        app.credentials.store_credential(
            "email",
            {
                "email": gmail_user,
                "password": gmail_password,
                "imap_server": "imap.gmail.com",
                "imap_port": 993,
                "smtp_server": "smtp.gmail.com",
                "smtp_port": 587,
            },
        )

    yield app

    # Persist any newly recorded entries
    try:
        if getattr(app.llm, "_recorder", None) is not None:
            app.llm._recorder.flush()
    except Exception:
        pass


@pytest.fixture
def require_telegram_token():
    """Skip if TELEGRAM_BOT_TOKEN not set."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        pytest.skip("TELEGRAM_BOT_TOKEN not set (add to .env.test)")
    return token


@pytest.fixture
def require_gmail():
    """Skip unless Gmail creds AND explicit live-gate are set.

    Gmail tests are gated behind MYCELOS_RUN_GMAIL_LIVE=1 because real
    SMTP/IMAP calls hit Gmail rate limits and are flaky in a full-suite
    run (the SMTP banner response can take 30+ seconds once Gmail starts
    throttling). Run them explicitly with:

        MYCELOS_RUN_GMAIL_LIVE=1 pytest tests/integration/test_email_live.py -m integration
    """
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_PASSWORD")
    if not user or not pw:
        pytest.skip("GMAIL_USER/GMAIL_PASSWORD not set (add to .env.test)")
    if os.environ.get("MYCELOS_RUN_GMAIL_LIVE") != "1":
        pytest.skip("Gmail live tests gated — set MYCELOS_RUN_GMAIL_LIVE=1 to run")
    return {"user": user, "password": pw}
