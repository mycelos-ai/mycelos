"""Smoke tests for local LLM backends (Ollama + LM Studio).

These tests prove that Mycelos can run entirely on local models — no cloud
API keys, no outbound HTTP except to the user's own machine. They're the
live counterpart to the "Build with Cloud. Run on Your Data." promise.

Skipped unless:
  - OLLAMA_HOST is set and reachable, and/or
  - LM_STUDIO_HOST is set and reachable.

NOTE: Running both inference roundtrips at once requires >=16 GB VRAM/RAM
because both backends page an 8B model in parallel. To avoid the
"Compute error" from whichever backend loses the race, each inference
roundtrip is gated on LOCAL_LLM_BACKEND. Run them one at a time:

    LOCAL_LLM_BACKEND=ollama    pytest -m integration tests/integration/test_local_llm_smoke.py -v
    LOCAL_LLM_BACKEND=lm_studio pytest -m integration tests/integration/test_local_llm_smoke.py -v

Leaving LOCAL_LLM_BACKEND unset runs the cheap `/tags` and `/models`
endpoint checks for both, which is safe to run anywhere.
"""
from __future__ import annotations

import os

import pytest

# Local inference is slow on first load (model must page into RAM) and the
# default 30s pytest-timeout from pyproject is too tight for an 8B model.
pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


def _skip_if_not_selected(backend: str):
    """Skip inference roundtrips unless this backend was explicitly requested.

    Protects machines that can only hold one 8B model in RAM at a time.
    Default (no env var) = run neither roundtrip; the /tags and /models
    endpoint probes still run.
    """
    selected = os.environ.get("LOCAL_LLM_BACKEND", "").strip().lower()
    if not selected:
        pytest.skip(
            "LOCAL_LLM_BACKEND not set — set to 'ollama' or 'lm_studio' to run "
            "inference roundtrips one at a time (avoids double-loading 8B "
            "models on the same machine)."
        )
    if selected != backend:
        pytest.skip(f"LOCAL_LLM_BACKEND={selected!r}, skipping {backend} roundtrip")


@pytest.mark.parametrize("integration_app_local", ["ollama"], indirect=True)
def test_ollama_direct_chat_roundtrip(integration_app_local):
    """Minimal: resolve the default model, send a one-line prompt, get content back.

    Exercises the whole LLM broker path (credentials, resolution, LiteLLM
    dispatch, response unwrapping) without any tool loop.
    """
    _skip_if_not_selected("ollama")
    app = integration_app_local
    model = app.resolve_cheapest_model()
    assert model and model.startswith("ollama/"), f"expected ollama/* model, got {model}"

    response = app.llm.complete(
        [
            {"role": "system", "content": "You respond in one short sentence."},
            {"role": "user", "content": "Say 'hello' and nothing else."},
        ],
        model=model,
    )
    assert response.content
    assert len(response.content.strip()) > 0


@pytest.mark.parametrize("integration_app_local", ["lm_studio"], indirect=True)
def test_lm_studio_direct_chat_roundtrip(integration_app_local):
    """Same smoke check against LM Studio's OpenAI-compatible server."""
    _skip_if_not_selected("lm_studio")
    app = integration_app_local
    model = app.resolve_cheapest_model()
    assert model and model.startswith("lm_studio/"), f"expected lm_studio/* model, got {model}"

    response = app.llm.complete(
        [
            {"role": "system", "content": "You respond in one short sentence."},
            {"role": "user", "content": "Say 'hello' and nothing else."},
        ],
        model=model,
    )
    assert response.content
    assert len(response.content.strip()) > 0


def test_ollama_endpoint_exposes_models(require_ollama):
    """Doc-style check: the /api/tags endpoint lists the models we expect.

    Doesn't talk to the LLM — just verifies Mycelos can discover what's
    available, which is the pattern the onboarding and model-updater use.
    """
    host = require_ollama
    import httpx
    data = httpx.get(f"{host}/api/tags", timeout=5).json()
    model_names = [m["name"] for m in data.get("models", [])]
    assert len(model_names) > 0
    # We don't hard-assert gemma4 — users may have a different set; the
    # smoke test above picks automatically.


def test_lm_studio_endpoint_exposes_models(require_lm_studio):
    """Counterpart for LM Studio's OpenAI-compatible /models endpoint."""
    host = require_lm_studio
    import httpx
    data = httpx.get(f"{host}/models", timeout=5).json()
    model_ids = [m["id"] for m in data.get("data", [])]
    assert len(model_ids) > 0
