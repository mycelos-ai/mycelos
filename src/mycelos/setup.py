"""Idempotent non-interactive setup used by both the CLI init wizard
and the web onboarding endpoint.

The `mycelos init` CLI is still the richer experience (connectivity retries,
filesystem permissions, provider picker). This module extracts the pieces
that must also work from a browser onboarding flow on a fresh install:

- initialize DB schema + default user (via App.initialize)
- detect provider from API key (or accept an explicit provider)
- store credential
- register provider models (best capable + cheap)
- register system agents (mycelos, builder, workflow-agent, ...)
- apply smart model defaults
- register built-in connectors

It is safe to call multiple times — each sub-step checks for existing state.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any

from mycelos.app import App
from mycelos.llm.providers import PROVIDERS, ModelInfo, ProviderConfig, get_provider_models
from mycelos.llm.smart_defaults import compute_smart_defaults

logger = logging.getLogger("mycelos.setup")


SYSTEM_AGENTS: list[dict[str, Any]] = [
    {"id": "mycelos", "name": "Mycelos", "agent_type": "full_model", "capabilities": []},
    {"id": "builder", "name": "Builder", "agent_type": "full_model", "capabilities": []},
    {"id": "workflow-agent", "name": "Workflow Agent", "agent_type": "light_model", "capabilities": []},
    {"id": "evaluator-agent", "name": "Evaluator Agent", "agent_type": "light_model", "capabilities": []},
    {"id": "auditor-agent", "name": "Auditor Agent", "agent_type": "full_model", "capabilities": []},
]


class SetupError(Exception):
    """Raised when web-init fails with a user-actionable message."""


def ensure_master_key(data_dir: Path) -> None:
    """Create ~/.mycelos/.master_key if missing and export it into the env."""
    data_dir.mkdir(parents=True, exist_ok=True)
    key_file = data_dir / ".master_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(32))
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
    if not os.environ.get("MYCELOS_MASTER_KEY"):
        os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()


def is_initialized(app: App) -> bool:
    """Return True when Mycelos has at least one credential AND a registered model."""
    try:
        creds = app.credentials.list_credentials("default")
        if not creds:
            return False
    except Exception:
        return False
    try:
        models = app.model_registry.list_models() or []
        return len(models) > 0
    except Exception:
        return False


def register_system_agents(app: App) -> None:
    for agent in SYSTEM_AGENTS:
        if app.agent_registry.get(agent["id"]) is None:
            app.agent_registry.register(
                agent["id"], agent["name"], agent["agent_type"],
                agent["capabilities"], "system",
            )
            app.agent_registry.set_status(agent["id"], "active")


def register_provider_models(app: App, provider: ProviderConfig) -> list[ModelInfo]:
    """Pick best capable + cheap tier models for `provider` and register them."""
    catalog = get_provider_models(provider.id) or []
    if not catalog:
        return []
    # Pick one model per tier (smart / standard / fast) — first match wins.
    picked: list[ModelInfo] = []
    seen_tiers: set[str] = set()
    for m in catalog:
        if m.tier and m.tier not in seen_tiers:
            picked.append(m)
            seen_tiers.add(m.tier)
    if not picked:
        picked = catalog[:3]
    for m in picked:
        app.model_registry.add_model(
            model_id=m.id,
            provider=m.provider,
            tier=m.tier,
            input_cost_per_1k=m.input_cost_per_1k,
            output_cost_per_1k=m.output_cost_per_1k,
            max_context=m.max_context,
        )
    return picked


def apply_defaults(app: App, picked_models: list[ModelInfo]) -> None:
    defaults = compute_smart_defaults(picked_models)
    system_defaults: dict[str, list[str]] = {}
    agent_assignments: dict[str, dict[str, list[str]]] = {}
    for role, model_ids in defaults.items():
        if not model_ids:
            continue
        parts = role.split(":", 1)
        agent = parts[0]
        purpose = parts[1] if len(parts) > 1 else "execution"
        if agent == "system":
            system_defaults[purpose] = model_ids
        else:
            agent_assignments.setdefault(agent, {})[purpose] = model_ids
    if system_defaults:
        app.model_registry.set_system_defaults(system_defaults)
    for agent_id, purposes in agent_assignments.items():
        for purpose, model_ids in purposes.items():
            app.model_registry.set_agent_models(agent_id, model_ids, purpose)


def register_builtin_connectors(app: App) -> None:
    """Register DuckDuckGo + HTTP with permissive default policies."""
    try:
        app.connector_registry.register(
            "web-search-duckduckgo", "DuckDuckGo", "search",
            ["search.web", "search.news"],
            description="Search the web -- no API key needed",
            setup_type="none",
        )
        app.policy_engine.set_policy("default", None, "search.web", "always")
        app.policy_engine.set_policy("default", None, "search.news", "always")
    except Exception:
        logger.debug("DuckDuckGo connector already registered or failed", exc_info=True)

    try:
        app.connector_registry.register(
            "http", "HTTP", "http",
            ["http.get", "http.post"],
            description="Fetch web pages and call APIs",
            setup_type="none",
        )
        app.policy_engine.set_policy("default", None, "http.get", "always")
        app.policy_engine.set_policy("default", None, "http.post", "always")
    except Exception:
        logger.debug("HTTP connector already registered or failed", exc_info=True)

    for tool in ("note.write", "note.read", "note.search", "note.list", "note.update", "note.link"):
        try:
            app.policy_engine.set_policy("default", None, tool, "always")
        except Exception:
            pass


def web_init(
    app: App,
    *,
    api_key: str | None = None,
    provider_id: str | None = None,
    ollama_url: str | None = None,
) -> dict[str, Any]:
    """Run the full onboarding sequence in a non-interactive, idempotent way.

    Exactly one of `api_key` (with optional `provider_id` override) or
    `ollama_url` must be supplied.

    Returns a small status dict with the resolved provider, registered models,
    and a flag indicating whether Mycelos is now ready to chat.
    """
    ensure_master_key(app.data_dir)

    # App.initialize() is idempotent enough — it only creates Gen 0 when missing.
    app.initialize()

    # Resolve provider
    provider: ProviderConfig | None = None
    if ollama_url:
        provider = PROVIDERS.get("ollama")
        if provider is None:
            raise SetupError("Ollama provider not supported in this build.")
        app.memory.set("default", "system", "ollama_url", ollama_url)
    elif api_key:
        api_key = api_key.strip()
        if not api_key:
            raise SetupError("Empty API key.")
        if provider_id:
            provider = PROVIDERS.get(provider_id)
        else:
            from mycelos.cli.detect_provider import detect_provider
            detection = detect_provider(api_key)
            if detection.provider:
                provider = PROVIDERS.get(detection.provider)
        if provider is None:
            raise SetupError(
                "Could not detect provider from the API key. "
                "Please pick a provider explicitly."
            )
        if provider.requires_key:
            app.credentials.store_credential(
                provider.id,
                {"api_key": api_key, "env_var": provider.env_var},
            )
            if provider.env_var:
                os.environ[provider.env_var] = api_key
            app.audit.log("credential.stored", details={"service": provider.id})
    else:
        raise SetupError("Either api_key or ollama_url must be provided.")

    # Order matters: agents must exist before apply_defaults writes
    # agent_llm_models rows (FK → agents.id).
    picked = register_provider_models(app, provider)
    if not picked:
        raise SetupError(f"No models available for provider {provider.id}.")
    register_system_agents(app)
    apply_defaults(app, picked)
    register_builtin_connectors(app)

    app.audit.log("setup.web_init_completed", details={
        "provider": provider.id,
        "models": [m.id for m in picked],
    })

    return {
        "ok": True,
        "provider": provider.id,
        "models": [m.id for m in picked],
        "ready": is_initialized(app),
    }
