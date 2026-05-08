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
    {"id": "doctor", "name": "Doctor", "agent_type": "full_model", "capabilities": []},
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


def _validate_multi_field_credentials(
    provider: ProviderConfig, credentials: dict[str, Any]
) -> dict[str, Any]:
    """Check that all required fields for a multi-field provider were supplied.

    Strips whitespace, raises SetupError naming the first missing field.
    Returns a clean dict that is safe to encrypt-and-store.
    """
    if not provider.credential_fields:
        return credentials
    cleaned: dict[str, Any] = {}
    for field in provider.credential_fields:
        value = credentials.get(field.key)
        if isinstance(value, str):
            value = value.strip()
        if field.required and not value:
            raise SetupError(
                f"Missing required field '{field.label}' for {provider.name}."
            )
        if value is not None and value != "":
            cleaned[field.key] = value
    # Vertex-specific sanity check: the JSON should at least parse.
    if provider.id == "vertex_ai" and "vertex_credentials" in cleaned:
        import json as _json
        raw = cleaned["vertex_credentials"]
        if isinstance(raw, str):
            try:
                _json.loads(raw)
            except _json.JSONDecodeError as e:
                raise SetupError(
                    "Service Account JSON is not valid JSON: "
                    f"{e.msg} at line {e.lineno}."
                )
    return cleaned


def web_init(
    app: App,
    *,
    api_key: str | None = None,
    provider_id: str | None = None,
    ollama_url: str | None = None,
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full onboarding sequence in a non-interactive, idempotent way.

    Exactly one of these credential paths must be supplied:
      * ``api_key`` (with optional ``provider_id`` override) — single-key providers
      * ``ollama_url`` — local Ollama
      * ``credentials`` + ``provider_id`` — multi-field providers (Vertex AI)

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
    elif credentials:
        # Multi-field flow: provider_id is required (no auto-detection from a
        # single string), and the payload must satisfy the provider's schema.
        if not provider_id:
            raise SetupError(
                "provider_id is required when supplying multi-field credentials."
            )
        provider = PROVIDERS.get(provider_id)
        if provider is None:
            raise SetupError(f"Unknown provider: {provider_id}")
        if not provider.credential_fields:
            raise SetupError(
                f"Provider {provider.name} does not accept multi-field credentials. "
                "Use api_key instead."
            )
        cleaned = _validate_multi_field_credentials(provider, credentials)
        app.credentials.store_credential(provider.id, cleaned)
        app.audit.log("credential.stored", details={"service": provider.id})
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
        if provider.credential_fields:
            raise SetupError(
                f"{provider.name} requires multi-field credentials. "
                "Use the 'credentials' payload instead of 'api_key'."
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
        raise SetupError(
            "One of api_key, ollama_url, or credentials must be provided."
        )

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
