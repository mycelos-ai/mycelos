"""LLM cassette recorder — record/replay for integration tests.

Cassettes live under tests/cassettes/ and are checked into git. They store
the LLM request fingerprint (sha256 of model + messages + tools) and the
recorded LLMResponse. On replay, a matching fingerprint returns the recorded
response without hitting the real API.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from mycelos.llm.broker import LLMResponse


def _canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint_request(
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
) -> str:
    """SHA256 of (model, messages, tools). Stable across dict key order."""
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools or [],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class Cassette:
    """A single test's recorded LLM responses, keyed by request fingerprint."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._entries: dict[str, dict] = {}
        self._dirty = False

    def load(self) -> None:
        """Load entries from disk. Missing file is treated as empty."""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._entries = data.get("entries", {})

    def save(self) -> None:
        """Write entries to disk if dirty."""
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": self._entries}
        self._path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        self._dirty = False

    def get(self, fingerprint: str) -> LLMResponse | None:
        entry = self._entries.get(fingerprint)
        if entry is None:
            return None
        return LLMResponse(
            content=entry.get("content", ""),
            total_tokens=entry.get("total_tokens", 0),
            model=entry.get("model", ""),
            tool_calls=entry.get("tool_calls"),
            cost=entry.get("cost", 0.0),
        )

    def put(self, fingerprint: str, response: LLMResponse) -> None:
        self._entries[fingerprint] = {
            "content": response.content,
            "total_tokens": response.total_tokens,
            "model": response.model,
            "tool_calls": response.tool_calls,
            "cost": response.cost,
        }
        self._dirty = True


class CassetteMissError(RuntimeError):
    """Raised in replay mode when a request has no recorded response."""


class CassetteRecorder:
    """Wraps LLM calls with record/replay logic.

    Modes:
      - replay: must find a matching cassette entry, else raise CassetteMissError
      - record: always call the real API, write the response to the cassette
      - auto:   replay if entry exists, otherwise record
    """

    VALID_MODES = ("replay", "record", "auto")

    def __init__(self, cassette_path: Path, mode: str = "replay"):
        if mode not in self.VALID_MODES:
            raise ValueError(f"Invalid cassette mode: {mode}")
        self._mode = mode
        self._cassette = Cassette(cassette_path)
        self._cassette.load()

    def intercept(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
        real_call: Callable[[], LLMResponse],
    ) -> LLMResponse:
        fp = fingerprint_request(model, messages, tools)
        existing = self._cassette.get(fp)

        if self._mode == "replay":
            if existing is None:
                raise CassetteMissError(
                    f"No cassette entry for {model} (fingerprint {fp[:12]}…). "
                    f"Re-record with MYCELOS_LLM_CASSETTE=auto and ANTHROPIC_API_KEY set."
                )
            return existing

        if self._mode == "auto" and existing is not None:
            return existing

        response = real_call()
        self._cassette.put(fp, response)
        return response

    def flush(self) -> None:
        """Persist any newly recorded entries to disk."""
        self._cassette.save()
