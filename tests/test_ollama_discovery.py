"""Tests for Ollama model discovery with mocked HTTP."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mycelos.llm.ollama import (
    OllamaModel,
    classify_ollama_tier,
    discover_ollama_models,
    is_ollama_running,
)


def _mock_ollama_response() -> dict:
    """Create a mock response matching Ollama API format."""
    return {
        "models": [
            {
                "name": "llama3.3:latest",
                "model": "llama3.3:latest",
                "size": 4661224676,
                "details": {
                    "parameter_size": "8.0B",
                    "quantization_level": "Q4_0",
                    "family": "llama",
                },
            },
            {
                "name": "mistral:latest",
                "model": "mistral:latest",
                "size": 4109853536,
                "details": {
                    "parameter_size": "7.2B",
                    "quantization_level": "Q4_0",
                    "family": "mistral",
                },
            },
            {
                "name": "phi3:latest",
                "model": "phi3:latest",
                "size": 2176000000,
                "details": {
                    "parameter_size": "3.8B",
                    "quantization_level": "Q4_0",
                    "family": "phi",
                },
            },
        ]
    }


def _make_mock_resp(json_data: dict, status_code: int = 200) -> MagicMock:
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# discover_ollama_models
# ---------------------------------------------------------------------------


class TestDiscoverOllamaModels:
    """Tests for the discover_ollama_models function."""

    def test_returns_list_of_models(self) -> None:
        mock_resp = _make_mock_resp(_mock_ollama_response())

        with patch("httpx.get", return_value=mock_resp):
            models = discover_ollama_models()

        assert len(models) == 3
        assert models[0].id == "ollama/llama3.3:latest"
        assert models[0].name == "llama3.3:latest"
        assert models[0].size_bytes == 4661224676
        assert models[0].parameter_size == "8.0B"

    def test_second_model_parsed_correctly(self) -> None:
        mock_resp = _make_mock_resp(_mock_ollama_response())

        with patch("httpx.get", return_value=mock_resp):
            models = discover_ollama_models()

        assert models[1].id == "ollama/mistral:latest"
        assert models[1].name == "mistral:latest"
        assert models[1].size_bytes == 4109853536
        assert models[1].parameter_size == "7.2B"

    def test_custom_url(self) -> None:
        mock_resp = _make_mock_resp(_mock_ollama_response())

        with patch("httpx.get", return_value=mock_resp) as mock_get:
            discover_ollama_models("http://my-server:11434")
            mock_get.assert_called_once_with(
                "http://my-server:11434/api/tags", timeout=5
            )

    def test_empty_response(self) -> None:
        mock_resp = _make_mock_resp({"models": []})

        with patch("httpx.get", return_value=mock_resp):
            models = discover_ollama_models()

        assert models == []

    def test_connection_error_returns_empty(self) -> None:
        """Ollama not running should return empty list, no crash."""
        with patch("httpx.get", side_effect=Exception("Connection refused")):
            models = discover_ollama_models()

        assert models == []

    def test_timeout_returns_empty(self) -> None:
        with patch("httpx.get", side_effect=Exception("Timeout")):
            models = discover_ollama_models()

        assert models == []

    def test_http_error_returns_empty(self) -> None:
        """Non-200 status should return empty list."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("500 Server Error")

        with patch("httpx.get", return_value=mock_resp):
            models = discover_ollama_models()

        assert models == []

    def test_missing_details_uses_defaults(self) -> None:
        """Model entry without 'details' should still parse."""
        mock_resp = _make_mock_resp({
            "models": [
                {
                    "name": "custom:latest",
                    "size": 1000000,
                    # no "details" key
                }
            ]
        })

        with patch("httpx.get", return_value=mock_resp):
            models = discover_ollama_models()

        assert len(models) == 1
        assert models[0].id == "ollama/custom:latest"
        assert models[0].parameter_size == "unknown"

    def test_missing_name_falls_back_to_model_key(self) -> None:
        """If 'name' key is absent, fall back to 'model' key."""
        mock_resp = _make_mock_resp({
            "models": [
                {
                    "model": "fallback-model:v1",
                    "size": 500000,
                    "details": {"parameter_size": "3B"},
                }
            ]
        })

        with patch("httpx.get", return_value=mock_resp):
            models = discover_ollama_models()

        assert models[0].name == "fallback-model:v1"
        assert models[0].id == "ollama/fallback-model:v1"

    def test_malformed_json_returns_empty(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = ValueError("Invalid JSON")

        with patch("httpx.get", return_value=mock_resp):
            models = discover_ollama_models()

        assert models == []


# ---------------------------------------------------------------------------
# classify_ollama_tier
# ---------------------------------------------------------------------------


class TestClassifyOllamaTier:
    """Tests for tier classification of Ollama models."""

    @pytest.mark.parametrize(
        "name,parameter_size,expected",
        [
            ("llama3.3:latest", "8.0B", "sonnet"),
            ("mistral:latest", "7.2B", "sonnet"),
            ("qwen2.5:latest", "7B", "sonnet"),
        ],
    )
    def test_sonnet_tier_models(
        self, name: str, parameter_size: str, expected: str
    ) -> None:
        m = OllamaModel(
            id=f"ollama/{name}", name=name, size_bytes=0, parameter_size=parameter_size
        )
        assert classify_ollama_tier(m) == expected

    @pytest.mark.parametrize(
        "name,parameter_size,expected",
        [
            ("phi3:latest", "3.8B", "haiku"),
            ("gemma:latest", "2B", "haiku"),
            ("tinyllama:latest", "1.1B", "haiku"),
        ],
    )
    def test_haiku_tier_models(
        self, name: str, parameter_size: str, expected: str
    ) -> None:
        m = OllamaModel(
            id=f"ollama/{name}", name=name, size_bytes=0, parameter_size=parameter_size
        )
        assert classify_ollama_tier(m) == expected

    @pytest.mark.parametrize(
        "name,parameter_size,expected",
        [
            ("llama3.1:70b", "70B", "opus"),
            ("deepseek:latest", "67B", "opus"),
        ],
    )
    def test_opus_tier_models(
        self, name: str, parameter_size: str, expected: str
    ) -> None:
        m = OllamaModel(
            id=f"ollama/{name}", name=name, size_bytes=0, parameter_size=parameter_size
        )
        assert classify_ollama_tier(m) == expected

    def test_unknown_model_defaults_to_sonnet(self) -> None:
        m = OllamaModel(
            id="ollama/mymodel:latest",
            name="mymodel:latest",
            size_bytes=0,
            parameter_size="13B",
        )
        assert classify_ollama_tier(m) == "sonnet"

    def test_parameter_size_fallback_70b(self) -> None:
        """Unknown model name but 70B param size should be opus."""
        m = OllamaModel(
            id="ollama/custom:70b",
            name="custom:70b",
            size_bytes=0,
            parameter_size="70B",
        )
        assert classify_ollama_tier(m) == "opus"

    def test_parameter_size_fallback_2b(self) -> None:
        """Unknown model name but 2B param size should be haiku."""
        m = OllamaModel(
            id="ollama/custom:small",
            name="custom:small",
            size_bytes=0,
            parameter_size="2B",
        )
        assert classify_ollama_tier(m) == "haiku"

    def test_parameter_size_13b_not_haiku(self) -> None:
        """13B should NOT match haiku (no false substring match on '3b')."""
        m = OllamaModel(
            id="ollama/unknown:13b",
            name="unknown:13b",
            size_bytes=0,
            parameter_size="13B",
        )
        assert classify_ollama_tier(m) == "sonnet"

    def test_parameter_size_405b_is_opus(self) -> None:
        m = OllamaModel(
            id="ollama/llama3.1:405b",
            name="llama3.1:405b",
            size_bytes=0,
            parameter_size="405B",
        )
        assert classify_ollama_tier(m) == "opus"


# ---------------------------------------------------------------------------
# is_ollama_running
# ---------------------------------------------------------------------------


class TestIsOllamaRunning:
    """Tests for Ollama server health check."""

    def test_running(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.get", return_value=mock_resp):
            assert is_ollama_running() is True

    def test_not_running(self) -> None:
        with patch("httpx.get", side_effect=Exception("refused")):
            assert is_ollama_running() is False

    def test_custom_url(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            is_ollama_running("http://remote:11434")
            mock_get.assert_called_once_with(
                "http://remote:11434", timeout=3
            )

    def test_non_200_status(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp):
            assert is_ollama_running() is False


# ---------------------------------------------------------------------------
# OllamaModel dataclass
# ---------------------------------------------------------------------------


class TestOllamaModel:
    """Tests for the OllamaModel dataclass."""

    def test_creation(self) -> None:
        m = OllamaModel(
            id="ollama/test:latest",
            name="test:latest",
            size_bytes=1024,
            parameter_size="7B",
        )
        assert m.id == "ollama/test:latest"
        assert m.name == "test:latest"
        assert m.size_bytes == 1024
        assert m.parameter_size == "7B"

    def test_equality(self) -> None:
        m1 = OllamaModel(id="ollama/a", name="a", size_bytes=0, parameter_size="7B")
        m2 = OllamaModel(id="ollama/a", name="a", size_bytes=0, parameter_size="7B")
        assert m1 == m2
