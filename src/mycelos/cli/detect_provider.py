"""Provider auto-detection from API key or server URL."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProviderDetection:
    provider: str | None = None       # "anthropic", "openai", "openrouter", "gemini", "ollama"
    default_model: str = ""
    is_url: bool = False
    server_url: str = ""
    needs_manual_selection: bool = False
    env_var: str = ""                  # e.g. "ANTHROPIC_API_KEY"


_KEY_PREFIXES = [
    ("sk-ant-", "anthropic", "anthropic/claude-sonnet-4-6", "ANTHROPIC_API_KEY"),
    ("sk-or-", "openrouter", "openrouter/anthropic/claude-sonnet-4-6", "OPENROUTER_API_KEY"),
    ("sk-", "openai", "openai/gpt-4o", "OPENAI_API_KEY"),
    ("AIza", "gemini", "gemini/gemini-2.5-flash", "GEMINI_API_KEY"),
]


def detect_provider(input_str: str) -> ProviderDetection:
    input_str = input_str.strip()
    if not input_str:
        return ProviderDetection(needs_manual_selection=True)

    if input_str.startswith("http://") or input_str.startswith("https://"):
        return ProviderDetection(
            provider="ollama", is_url=True,
            server_url=input_str.rstrip("/"),
        )

    for prefix, provider_id, default_model, env_var in _KEY_PREFIXES:
        if input_str.startswith(prefix):
            return ProviderDetection(
                provider=provider_id, default_model=default_model, env_var=env_var,
            )

    return ProviderDetection(needs_manual_selection=True)
