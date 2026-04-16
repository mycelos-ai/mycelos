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
