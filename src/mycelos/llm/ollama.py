"""Ollama model discovery -- queries local Ollama API for available models."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class OllamaModel:
    """A locally available Ollama model."""

    id: str  # "ollama/llama3.3:latest"
    name: str  # "llama3.3:latest"
    size_bytes: int  # model file size
    parameter_size: str  # "8B", "70B", etc. (from details if available)


def discover_ollama_models(
    url: str = "http://localhost:11434",
) -> list[OllamaModel]:
    """Query Ollama API for locally available models.

    Sends GET {url}/api/tags and parses the response into OllamaModel
    instances. The Ollama API returns a JSON object like::

        {
            "models": [
                {
                    "name": "llama3.3:latest",
                    "model": "llama3.3:latest",
                    "size": 4661224676,
                    "details": {
                        "parameter_size": "8.0B",
                        "quantization_level": "Q4_0",
                        "family": "llama"
                    }
                }
            ]
        }

    Args:
        url: Base URL of the Ollama server.

    Returns:
        List of discovered models. Empty list if Ollama is unreachable.
        Never raises -- network errors are caught and logged.
    """
    try:
        import httpx
        import json as _json
        from mycelos.connectors import http_tools as _http_tools

        pc = getattr(_http_tools, "_proxy_client", None)
        if pc is not None:
            # Two-container mode: Ollama sits on the user's host. The
            # gateway cannot reach it directly (mycelos-internal network).
            resp = pc.http_get(f"{url}/api/tags", timeout=5)
            body = resp.get("body", "")
            if resp.get("status", 0) >= 400 or not body:
                logger.debug("Ollama not reachable at %s (proxy status=%s)", url, resp.get("status"))
                return []
            data = _json.loads(body)
        else:
            resp = httpx.get(f"{url}/api/tags", timeout=5)
            resp.raise_for_status()
            data = resp.json()

        result: list[OllamaModel] = []
        for m in data.get("models", []):
            name = m.get("name", m.get("model", "unknown"))
            details = m.get("details", {})
            result.append(
                OllamaModel(
                    id=f"ollama/{name}",
                    name=name,
                    size_bytes=m.get("size", 0),
                    parameter_size=details.get("parameter_size", "unknown"),
                )
            )
        return result
    except Exception:
        logger.debug("Ollama not reachable at %s", url)
        return []


def classify_ollama_tier(model: OllamaModel) -> str:
    """Classify an Ollama model into a Mycelos tier based on its identity.

    Tiers map to Mycelos's cost-optimized execution model:
      - ``"haiku"`` -- small/fast models (phi, gemma, tinyllama, <=3B)
      - ``"sonnet"`` -- capable general models (llama3.3, mistral, 7-13B)
      - ``"opus"``  -- large models (70B+, deepseek, mixtral 8x)

    Args:
        model: The Ollama model to classify.

    Returns:
        One of ``"haiku"``, ``"sonnet"``, or ``"opus"``.
    """
    lower = model.name.lower()

    # Check known large model variants first (more specific patterns)
    if any(
        x in lower
        for x in ["llama3.1:70b", "llama3.3:70b", "mixtral:8x", "deepseek"]
    ):
        return "opus"

    # Known capable general models
    if any(
        x in lower
        for x in ["llama3.3", "llama-3.3", "mistral", "mixtral", "qwen2.5"]
    ):
        return "sonnet"

    # Known smaller/faster models
    if any(x in lower for x in ["phi", "gemma", "tinyllama", "qwen2"]):
        return "haiku"

    # Fall back to parameter size string
    import re

    param = model.parameter_size.strip().lower()
    if re.search(r"(?:^|[\s.])(?:70|72|405)b", param):
        return "opus"
    if re.search(r"(?:^|[\s.])[123](?:\.\d+)?b", param):
        return "haiku"

    return "sonnet"  # default for medium models (7B-13B)


def is_ollama_running(url: str = "http://localhost:11434") -> bool:
    """Check if Ollama server is reachable.

    Args:
        url: Base URL of the Ollama server.

    Returns:
        True if the server responds with HTTP 200, False otherwise.
    """
    try:
        import httpx
        from mycelos.connectors import http_tools as _http_tools

        pc = getattr(_http_tools, "_proxy_client", None)
        if pc is not None:
            resp = pc.http_get(url, timeout=3)
            return resp.get("status", 0) == 200
        resp = httpx.get(url, timeout=3)
        return resp.status_code == 200
    except Exception:
        return False
