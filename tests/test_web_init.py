"""Tests for the idempotent web-init / onboarding flow."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mycelos.app import App
from mycelos.setup import SetupError, is_initialized, web_init


def _fresh_app(tmp_path: Path) -> App:
    os.environ.pop("MYCELOS_MASTER_KEY", None)
    return App(tmp_path / "mycelos")


def test_is_initialized_false_on_empty(tmp_path: Path) -> None:
    app = _fresh_app(tmp_path)
    app.initialize()
    assert is_initialized(app) is False


def test_web_init_anthropic_key(tmp_path: Path) -> None:
    app = _fresh_app(tmp_path)
    result = web_init(app, api_key="sk-ant-api03-FAKEKEYFORTESTING")
    assert result["ok"] is True
    assert result["provider"] == "anthropic"
    assert result["models"]
    assert result["ready"] is True
    assert is_initialized(app) is True

    # System agents registered
    assert app.agent_registry.get("mycelos") is not None
    assert app.agent_registry.get("builder") is not None


def test_web_init_openai_key(tmp_path: Path) -> None:
    app = _fresh_app(tmp_path)
    result = web_init(app, api_key="sk-proj-FAKEOPENAIKEYFORTESTING")
    assert result["ok"] is True
    assert result["provider"] == "openai"


def test_web_init_idempotent(tmp_path: Path) -> None:
    app = _fresh_app(tmp_path)
    web_init(app, api_key="sk-ant-api03-FAKE")
    # Second call must not raise — re-registering agents/models/policies is safe.
    result = web_init(app, api_key="sk-ant-api03-FAKE")
    assert result["ok"] is True


def test_web_init_empty_key_rejected(tmp_path: Path) -> None:
    app = _fresh_app(tmp_path)
    with pytest.raises(SetupError):
        web_init(app, api_key="   ")


def test_web_init_no_input_rejected(tmp_path: Path) -> None:
    app = _fresh_app(tmp_path)
    with pytest.raises(SetupError):
        web_init(app)


def test_web_init_unknown_key_rejected(tmp_path: Path) -> None:
    app = _fresh_app(tmp_path)
    with pytest.raises(SetupError):
        web_init(app, api_key="totally-not-a-real-key-format")


# ---------------------------------------------------------------------------
# Multi-field credentials (Vertex AI EU mode)
# ---------------------------------------------------------------------------


_FAKE_VERTEX_JSON = (
    '{"type": "service_account", "project_id": "test-proj", '
    '"private_key_id": "abc", "private_key": "fake", '
    '"client_email": "test@test-proj.iam.gserviceaccount.com"}'
)


def test_web_init_vertex_ai_multi_field(tmp_path: Path) -> None:
    """Vertex AI requires three fields and provider_id; the credential is
    stored as-is and Gemini-via-Vertex models are registered."""
    app = _fresh_app(tmp_path)
    result = web_init(
        app,
        provider_id="vertex_ai",
        credentials={
            "vertex_credentials": _FAKE_VERTEX_JSON,
            "vertex_project": "test-proj",
            "vertex_location": "europe-west4",
        },
    )
    assert result["ok"] is True
    assert result["provider"] == "vertex_ai"
    # Credential fully round-trips through encryption.
    cred = app.credentials.get_credential("vertex_ai")
    assert cred is not None
    assert cred["vertex_project"] == "test-proj"
    assert cred["vertex_location"] == "europe-west4"
    assert cred["vertex_credentials"] == _FAKE_VERTEX_JSON


def test_web_init_vertex_ai_requires_provider_id(tmp_path: Path) -> None:
    app = _fresh_app(tmp_path)
    with pytest.raises(SetupError, match="provider_id"):
        web_init(
            app,
            credentials={
                "vertex_credentials": _FAKE_VERTEX_JSON,
                "vertex_project": "p",
                "vertex_location": "europe-west4",
            },
        )


def test_web_init_vertex_ai_rejects_missing_field(tmp_path: Path) -> None:
    app = _fresh_app(tmp_path)
    with pytest.raises(SetupError, match="Missing required field"):
        web_init(
            app,
            provider_id="vertex_ai",
            credentials={
                "vertex_credentials": _FAKE_VERTEX_JSON,
                # vertex_project missing
                "vertex_location": "europe-west4",
            },
        )


def test_web_init_vertex_ai_rejects_invalid_json(tmp_path: Path) -> None:
    app = _fresh_app(tmp_path)
    with pytest.raises(SetupError, match="not valid JSON"):
        web_init(
            app,
            provider_id="vertex_ai",
            credentials={
                "vertex_credentials": "this-is-not-json",
                "vertex_project": "p",
                "vertex_location": "europe-west4",
            },
        )


def test_web_init_rejects_api_key_for_multi_field_provider(tmp_path: Path) -> None:
    """Vertex AI cannot be set up via the simple api_key flow — the user
    must use the credentials payload."""
    app = _fresh_app(tmp_path)
    with pytest.raises(SetupError, match="multi-field credentials"):
        web_init(app, api_key="anything", provider_id="vertex_ai")


def test_web_init_mistral_via_api_key(tmp_path: Path) -> None:
    """Mistral is single-key — provider_id override + api_key works."""
    app = _fresh_app(tmp_path)
    # No public mistral key prefix exists, so detection fails — provider_id
    # must be supplied explicitly.
    result = web_init(
        app,
        api_key="mistral-fake-key-for-tests-12345",
        provider_id="mistral",
    )
    assert result["ok"] is True
    assert result["provider"] == "mistral"
    cred = app.credentials.get_credential("mistral")
    assert cred is not None
    assert cred["api_key"] == "mistral-fake-key-for-tests-12345"
