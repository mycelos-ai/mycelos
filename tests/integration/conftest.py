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
def require_ollama():
    """Skip if OLLAMA_HOST not set or the endpoint isn't reachable."""
    host = os.environ.get("OLLAMA_HOST", "").rstrip("/")
    if not host:
        pytest.skip("OLLAMA_HOST not set (add to .env.test)")
    import httpx
    try:
        resp = httpx.get(f"{host}/api/tags", timeout=3)
        if resp.status_code != 200:
            pytest.skip(f"Ollama not reachable at {host} (status {resp.status_code})")
        data = resp.json()
        if not data.get("models"):
            pytest.skip(f"Ollama at {host} has no models pulled")
    except Exception as e:
        pytest.skip(f"Ollama not reachable at {host}: {e}")
    return host


@pytest.fixture
def require_lm_studio():
    """Skip if LM_STUDIO_HOST not set or the endpoint isn't reachable."""
    host = os.environ.get("LM_STUDIO_HOST", "").rstrip("/")
    if not host:
        pytest.skip("LM_STUDIO_HOST not set (add to .env.test)")
    import httpx
    try:
        resp = httpx.get(f"{host}/models", timeout=3)
        if resp.status_code != 200:
            pytest.skip(f"LM Studio not reachable at {host} (status {resp.status_code})")
        data = resp.json()
        if not data.get("data"):
            pytest.skip(f"LM Studio at {host} has no models loaded")
    except Exception as e:
        pytest.skip(f"LM Studio not reachable at {host}: {e}")
    return host


def _pick_ollama_chat_model(host: str) -> str | None:
    """Return an Ollama chat model id (prefix-stripped), preferring gemma4."""
    import httpx
    try:
        resp = httpx.get(f"{host}/api/tags", timeout=3)
        models = resp.json().get("models", [])
    except Exception:
        return None
    names = [m.get("name") for m in models if m.get("name")]
    # Filter out embedding-only tags (heuristic: names containing "embed")
    chat_names = [n for n in names if "embed" not in n.lower()]
    for preferred in ("gemma4:latest", "gemma4", "gemma3:4b", "llama3:latest", "qwen3.5:9b"):
        if preferred in chat_names:
            return preferred
    return chat_names[0] if chat_names else None


def _pick_lm_studio_chat_model(host: str) -> str | None:
    """Return an LM Studio loaded-model id, preferring gemma-4."""
    import httpx
    try:
        resp = httpx.get(f"{host}/models", timeout=3)
        ids = [m.get("id") for m in resp.json().get("data", []) if m.get("id")]
    except Exception:
        return None
    # Skip embeddings
    chat_ids = [i for i in ids if "embed" not in i.lower()]
    for preferred in ("gemma-4-e4b-it", "gemma-4"):
        for cid in chat_ids:
            if cid.startswith(preferred):
                return cid
    return chat_ids[0] if chat_ids else None


@pytest.fixture
def integration_app_local(tmp_path, request, monkeypatch):
    """Fully-initialized App wired to a local LLM backend.

    Parametrize with indirect=True and values "ollama" or "lm_studio".
    Skips cleanly when the requested backend is not reachable.
    No cassette — local inference is free and deterministic-enough.
    """
    backend = getattr(request, "param", None) or "ollama"

    if backend == "ollama":
        host = os.environ.get("OLLAMA_HOST", "").rstrip("/")
        if not host:
            pytest.skip("OLLAMA_HOST not set (add to .env.test)")
        model_name = _pick_ollama_chat_model(host)
        if not model_name:
            pytest.skip(f"No chat-capable Ollama model at {host}")
        # LiteLLM id format for Ollama
        default_model = f"ollama/{model_name}"
        monkeypatch.setenv("OLLAMA_API_BASE", host)
        # Warm up: first inference pages the model into RAM (can take 30s+ on
        # an 8B). Do that here so the test's timeout covers only the response.
        try:
            import httpx as _httpx
            _httpx.post(
                f"{host}/api/chat",
                json={
                    "model": model_name,
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                    "options": {"num_predict": 1},
                },
                timeout=180,
            )
        except Exception:
            pass
    elif backend == "lm_studio":
        host = os.environ.get("LM_STUDIO_HOST", "").rstrip("/")
        if not host:
            pytest.skip("LM_STUDIO_HOST not set (add to .env.test)")
        model_name = _pick_lm_studio_chat_model(host)
        if not model_name:
            pytest.skip(f"No chat-capable LM Studio model at {host}")
        # LiteLLM talks to LM Studio via the openai-compatible path. The
        # lm_studio/ prefix in recent LiteLLM versions routes directly.
        default_model = f"lm_studio/{model_name}"
        monkeypatch.setenv("LM_STUDIO_API_BASE", host)
        # Some LiteLLM versions still want OPENAI_API_BASE; set both.
        monkeypatch.setenv("OPENAI_API_BASE", host)
        monkeypatch.setenv("OPENAI_API_KEY", "lm-studio-dummy-key")
    else:
        pytest.skip(f"Unknown local LLM backend: {backend}")

    from mycelos.app import App

    data_dir = tmp_path / "mycelos-test-local"
    os.environ["MYCELOS_MASTER_KEY"] = "integration-test-local-key-2026"

    app = App(data_dir)
    app.initialize_with_config(
        default_model=default_model,
        provider=backend,
    )

    # Seed the chosen model in the registry so resolve_models finds it.
    app.model_registry.add_model(
        model_id=default_model,
        provider=backend,
        tier="haiku",
        input_cost_per_1k=0.0,
        output_cost_per_1k=0.0,
    )
    app.model_registry.set_system_defaults({"execution": [default_model]})

    # Annotate the fixture value so tests can log / assert against it.
    yield app


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
