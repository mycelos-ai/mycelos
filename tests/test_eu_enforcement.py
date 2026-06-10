"""Unit tests for the EU-residency enforcement helpers.

Pure logic — no broker, no storage. These pin the rules the broker, STT,
and embedding gates all rely on.
"""
from __future__ import annotations

import pytest

from mycelos.llm.eu_enforcement import (
    provider_of_model,
    is_eu_model,
    filter_eu_models,
    vertex_region_is_eu,
    EUResidencyError,
)


class TestProviderOfModel:
    def test_prefixed_model(self):
        assert provider_of_model("mistral/mistral-large-latest") == "mistral"
        assert provider_of_model("vertex_ai/gemini-2.5-pro") == "vertex_ai"

    def test_unprefixed_model_guessed(self):
        assert provider_of_model("claude-sonnet-4-6") == "anthropic"
        assert provider_of_model("gpt-5") == "openai"


class TestIsEuModel:
    def test_eu_providers_allowed(self):
        assert is_eu_model("mistral/mistral-large-latest")
        assert is_eu_model("vertex_ai/gemini-2.5-pro")
        assert is_eu_model("ollama/llama3")

    def test_us_providers_denied(self):
        assert not is_eu_model("anthropic/claude-sonnet-4-6")
        assert not is_eu_model("openai/gpt-5")
        assert not is_eu_model("gemini/gemini-2.5-pro")  # AI Studio, not Vertex

    def test_unknown_provider_denied_fail_closed(self):
        # An unrecognized provider must be treated as non-EU (Rule 3).
        assert not is_eu_model("mystery/some-model")


class TestFilterEuModels:
    def test_keeps_only_eu_models_in_order(self):
        models = [
            "anthropic/claude-sonnet-4-6",
            "mistral/mistral-large-latest",
            "openai/gpt-5",
            "vertex_ai/gemini-2.5-pro",
        ]
        assert filter_eu_models(models) == [
            "mistral/mistral-large-latest",
            "vertex_ai/gemini-2.5-pro",
        ]

    def test_all_us_yields_empty(self):
        assert filter_eu_models(["anthropic/claude-sonnet-4-6", "openai/gpt-5"]) == []


class TestVertexRegion:
    def test_europe_regions_pass(self):
        assert vertex_region_is_eu("europe-west4")
        assert vertex_region_is_eu("europe-west1")

    def test_non_europe_regions_fail(self):
        assert not vertex_region_is_eu("us-central1")
        assert not vertex_region_is_eu("asia-southeast1")
        assert not vertex_region_is_eu(None)


def test_eu_residency_error_is_raisable():
    with pytest.raises(EUResidencyError):
        raise EUResidencyError("denied")


class TestEmbeddingProviderEuGate:
    def test_eu_mode_never_selects_openai_embeddings(self):
        """With EU mode on, get_embedding_provider must not return the OpenAI
        provider (which POSTs note text to api.openai.com), even when an
        OpenAI key + proxy exist."""
        from mycelos.knowledge.embeddings import get_embedding_provider
        provider = get_embedding_provider(
            openai_key="available", proxy_client=object(), eu_mode=True
        )
        assert provider.name in ("local", "none")

    def test_eu_mode_off_keeps_openai_when_available(self):
        from mycelos.knowledge.embeddings import get_embedding_provider
        provider = get_embedding_provider(
            openai_key="available", proxy_client=object(), eu_mode=False
        )
        assert provider.name == "openai"


class TestSttEuGate:
    def test_eu_mode_denies_cloud_stt(self):
        """With EU mode on, cloud STT backends (OpenAI Whisper, Google) must be
        refused; only local/openai_compatible is permitted."""
        from mycelos.speech.transcription import stt_backend_allowed_in_eu
        assert not stt_backend_allowed_in_eu("openai")
        assert not stt_backend_allowed_in_eu("gemini")
        assert stt_backend_allowed_in_eu("local")
        assert stt_backend_allowed_in_eu("openai_compatible")


class TestVertexRegionSetupGate:
    def test_eu_mode_rejects_non_europe_vertex_region(self):
        """With EU mode on, storing a Vertex credential pinned to a non-EU
        region must be refused — otherwise the EU-badged provider routes to
        the US."""
        from mycelos.setup import _validate_multi_field_credentials, SetupError
        from mycelos.llm.providers import PROVIDERS
        vertex = PROVIDERS["vertex_ai"]
        creds = {
            "vertex_credentials": '{"type":"service_account"}',
            "vertex_project": "proj",
            "vertex_location": "us-central1",
        }
        with pytest.raises(SetupError):
            _validate_multi_field_credentials(vertex, creds, eu_mode=True)

    def test_eu_mode_accepts_europe_vertex_region(self):
        from mycelos.setup import _validate_multi_field_credentials
        from mycelos.llm.providers import PROVIDERS
        vertex = PROVIDERS["vertex_ai"]
        creds = {
            "vertex_credentials": '{"type":"service_account"}',
            "vertex_project": "proj",
            "vertex_location": "europe-west4",
        }
        cleaned = _validate_multi_field_credentials(vertex, creds, eu_mode=True)
        assert cleaned["vertex_location"] == "europe-west4"
