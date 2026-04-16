"""Tests for custom server LLM provider support."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App
from mycelos.llm.providers import PROVIDERS, ModelInfo


def test_custom_provider_exists() -> None:
    """Custom provider should be registered in PROVIDERS dict."""
    assert "custom" in PROVIDERS
    p = PROVIDERS["custom"]
    assert p.requires_key is False
    assert p.name == "Custom Server"


def test_custom_provider_has_no_env_var() -> None:
    """Custom provider should not require an env var."""
    p = PROVIDERS["custom"]
    assert p.env_var is None


def test_custom_model_info_format() -> None:
    """Custom models should use custom/ prefix."""
    m = ModelInfo(
        id="custom/my-model",
        name="my-model",
        tier="sonnet",
        provider="custom",
        input_cost_per_1k=0.0,
        output_cost_per_1k=0.0,
    )
    assert m.id.startswith("custom/")
    assert m.provider == "custom"


def test_custom_model_info_zero_cost() -> None:
    """Custom/local models should typically have zero cost."""
    m = ModelInfo(
        id="custom/local-llm",
        name="local-llm",
        tier="sonnet",
        provider="custom",
        input_cost_per_1k=0.0,
        output_cost_per_1k=0.0,
    )
    assert m.input_cost_per_1k == 0.0
    assert m.output_cost_per_1k == 0.0


def test_custom_model_registered_in_db(tmp_path: Path) -> None:
    """Custom server model should be stored in llm_models."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-custom-reg"
    try:
        app = App(tmp_path)
        app.initialize()

        app.model_registry.add_model(
            model_id="custom/my-local-llm",
            provider="custom",
            tier="sonnet",
            input_cost_per_1k=0.0,
            output_cost_per_1k=0.0,
        )

        m = app.model_registry.get_model("custom/my-local-llm")
        assert m is not None
        assert m["provider"] == "custom"
        assert m["tier"] == "sonnet"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_custom_model_listed_by_provider(tmp_path: Path) -> None:
    """Custom models should be filterable by provider."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-custom-list"
    try:
        app = App(tmp_path)
        app.initialize()

        app.model_registry.add_model(
            "custom/model-a", "custom", "sonnet", 0.0, 0.0
        )
        app.model_registry.add_model(
            "custom/model-b", "custom", "haiku", 0.0, 0.0
        )

        models = app.model_registry.list_models(provider="custom")
        assert len(models) == 2
        assert all(m["provider"] == "custom" for m in models)
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_custom_model_in_snapshot(tmp_path: Path) -> None:
    """Custom models should appear in config generation snapshot."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-custom-snap"
    try:
        app = App(tmp_path)
        app.initialize()

        app.model_registry.add_model(
            "custom/my-model", "custom", "sonnet", 0.0, 0.0
        )
        app.model_registry.set_system_defaults({"execution": ["custom/my-model"]})

        gen_id = app.config.apply_from_state(
            app.state_manager, "custom model", "test"
        )

        row = app.storage.fetchone(
            "SELECT config_snapshot FROM config_generations WHERE id = ?",
            (gen_id,),
        )
        snapshot = json.loads(row["config_snapshot"])
        assert "custom/my-model" in snapshot["llm"]["models"]
        assert "system:execution" in snapshot["llm"]["assignments"]
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_custom_credential_stored(tmp_path: Path) -> None:
    """Custom server API key should be stored encrypted."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-custom-cred"
    try:
        app = App(tmp_path)
        app.initialize()

        app.credentials.store_credential("custom", {
            "api_key": "sk-custom-key",
            "api_base": "http://my-server:8080",
            "provider": "custom",
        })

        cred = app.credentials.get_credential("custom")
        assert cred is not None
        assert cred["api_key"] == "sk-custom-key"
        assert cred["api_base"] == "http://my-server:8080"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_custom_credential_in_snapshot(tmp_path: Path) -> None:
    """Stored custom credentials should appear in state snapshot."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-custom-credsnap"
    try:
        app = App(tmp_path)
        app.initialize()

        app.credentials.store_credential("custom", {
            "api_key": "sk-test",
            "api_base": "http://localhost:8080",
            "provider": "custom",
        })

        snapshot = app.state_manager.snapshot()
        assert "custom" in snapshot["credentials"]
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_custom_model_resolve_as_system_default(tmp_path: Path) -> None:
    """Custom model set as system default should resolve correctly."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-custom-resolve"
    try:
        app = App(tmp_path)
        app.initialize()

        app.model_registry.add_model(
            "custom/my-model", "custom", "sonnet", 0.0, 0.0
        )
        app.model_registry.set_system_defaults({"execution": ["custom/my-model"]})

        resolved = app.model_registry.resolve_models(None, "execution")
        assert resolved == ["custom/my-model"]
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)
