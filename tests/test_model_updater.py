"""Tests for ModelUpdaterHandler (deterministic — no LLM)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def handler(tmp_path: Path, monkeypatch):
    from mycelos.agents.handlers.model_updater_handler import ModelUpdaterHandler
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "test-key-model-updater")
    app = MagicMock()
    # Default: user has anthropic credentials only
    app.credentials.list_credentials.return_value = [
        {"service": "anthropic", "label": "default"},
    ]
    app.memory.get.return_value = None  # no ollama
    app.model_registry.sync_from_litellm.return_value = {
        "added": [], "updated": [], "total": 0,
    }
    return ModelUpdaterHandler(app), app


def test_handler_reports_no_additions(handler) -> None:
    h, app = handler
    result = h.run("default")
    assert result["added"] == []
    assert result["total"] == 0
    # No audit event when nothing new
    assert not any(
        call.args and call.args[0] == "models.discovered"
        for call in app.audit.log.call_args_list
    )


def test_handler_emits_audit_on_new_models(handler) -> None:
    h, app = handler
    app.model_registry.sync_from_litellm.return_value = {
        "added": ["anthropic/claude-opus-4-7", "anthropic/claude-sonnet-4-7"],
        "updated": [],
        "total": 2,
    }
    result = h.run("default")
    assert len(result["added"]) == 2
    # Audit event logged
    assert any(
        call.args and call.args[0] == "models.discovered"
        for call in app.audit.log.call_args_list
    )


def test_handler_uses_prefer_remote(handler) -> None:
    """The periodic handler must request the remote cost map so users
    get fresh-off-the-press models without a pip upgrade."""
    h, app = handler
    h.run("default")
    call = app.model_registry.sync_from_litellm.call_args
    assert call.kwargs["prefer_remote"] is True


def test_handler_restricts_to_configured_providers(handler) -> None:
    """Only providers the user has credentials for should be synced.
    Avoids flooding the registry with 200 Gemini models when only
    Anthropic is configured."""
    h, app = handler
    app.credentials.list_credentials.return_value = [
        {"service": "anthropic", "label": "default"},
        {"service": "openai", "label": "default"},
    ]
    h.run("default")
    call = app.model_registry.sync_from_litellm.call_args
    assert sorted(call.kwargs["providers"]) == ["anthropic", "openai"]


def test_handler_skips_sync_when_no_credentials(handler) -> None:
    """No credentials → no sync. Nothing to discover that's actually usable."""
    h, app = handler
    app.credentials.list_credentials.return_value = []
    app.memory.get.return_value = None
    result = h.run("default")
    assert result["total"] == 0
    app.model_registry.sync_from_litellm.assert_not_called()


def test_handler_includes_ollama_when_configured(handler) -> None:
    """Ollama is credential-less. When the endpoint is stored, include it."""
    h, app = handler
    app.credentials.list_credentials.return_value = []
    app.memory.get.return_value = "http://localhost:11434"
    h.run("default")
    call = app.model_registry.sync_from_litellm.call_args
    assert "ollama" in call.kwargs["providers"]


def test_handler_survives_sync_failure(handler) -> None:
    """If the refresh itself raises, the handler must not crash — it's
    a periodic job; a bad network day cannot take the scheduler down."""
    h, app = handler
    app.model_registry.sync_from_litellm.side_effect = RuntimeError("boom")
    result = h.run("default")
    assert result.get("error")
    assert any(
        call.args and call.args[0] == "models.refresh_failed"
        for call in app.audit.log.call_args_list
    )


def test_handler_is_pure_python_no_llm(handler) -> None:
    """Proof point: the handler never touches app.llm. This is a
    deterministic workflow — the whole point of this feature."""
    h, app = handler
    h.run("default")
    app.llm.complete.assert_not_called()
    app.llm.assert_not_called()
