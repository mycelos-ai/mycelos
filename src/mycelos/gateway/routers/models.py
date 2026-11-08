"""Models, tools, and system update endpoints."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get('/api/models/task-settings')
async def get_task_settings(request: Request):
    from mycelos.llm.task_settings import TASKS, read
    return {key: read(request.app.state.mycelos.storage, key) for key in TASKS}


@router.put('/api/models/task-settings/{purpose}')
async def put_task_settings(request: Request, purpose: str, payload: dict[str, Any]):
    from mycelos.llm.task_settings import save
    try:
        return save(request.app.state.mycelos, purpose, payload)
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)


@router.get("/api/models")
async def list_models(request: Request) -> dict[str, Any]:
    """All models, registered agents, and agent assignments.

    `agents` lists every registered agent (name + id) so the UI can show
    an explicit row for agents that currently inherit system defaults.
    `assignments` rows carry `agent_name` for labeling.
    """
    mycelos = request.app.state.mycelos
    models = mycelos.storage.fetchall("SELECT * FROM llm_models ORDER BY provider, tier")
    agents = mycelos.storage.fetchall(
        "SELECT id, name FROM agents ORDER BY id"
    )
    assignments = mycelos.storage.fetchall(
        """
        SELECT a.agent_id, a.model_id, a.priority, a.purpose,
               COALESCE(g.name, a.agent_id) AS agent_name
        FROM agent_llm_models a
        LEFT JOIN agents g ON g.id = a.agent_id
        ORDER BY COALESCE(a.agent_id, 'zzz'), a.priority
        """
    )
    return {
        "models": [dict(m) for m in models],
        "agents": [dict(r) for r in agents],
        "assignments": [dict(a) for a in assignments],
    }


@router.get("/api/tools")
async def list_tools() -> dict[str, Any]:
    """Return all registered built-in tools with category + permission.

    Used by the Agents detail page to render tool checkboxes grouped by
    category. Custom/persona agents see a writable matrix; system agents
    see the same list as a read-only reference.

    Does NOT expose dynamic MCP tools — those are reached via the
    ``connector_call`` meta-tool.
    """
    from mycelos.tools.registry import ToolRegistry

    ToolRegistry._ensure_initialized()
    tools: list[dict[str, Any]] = []
    for name, entry in sorted(ToolRegistry._tools.items()):
        schema = entry.get("schema", {})
        func = schema.get("function", {}) if isinstance(schema, dict) else {}
        tools.append({
            "name": name,
            "category": entry.get("category") or "uncategorized",
            "permission": entry["permission"].value,
            "description": func.get("description", ""),
        })
    return {"tools": tools}


@router.get("/api/system/update-status")
async def system_update_status(request: Request) -> dict[str, Any]:
    """Return the cached Mycelos release-check state.

    Cheap read: never hits GitHub. The background ModelUpdaterHandler
    refreshes the cache once a day; this endpoint serves whatever is
    stored in memory so the Doctor banner and Settings toggle can
    render without an extra network call.
    """
    import json as _json
    mycelos = request.app.state.mycelos
    try:
        raw = mycelos.memory.get(
            user_id="default", scope="system", key="system.update.latest"
        )
    except Exception:
        raw = None
    state: dict[str, Any] = {}
    if raw:
        if isinstance(raw, dict):
            state = raw
        else:
            try:
                state = _json.loads(raw)
            except Exception:
                state = {}
    try:
        opt = mycelos.memory.get(
            user_id="default", scope="system", key="system.check_for_updates"
        )
    except Exception:
        opt = None
    checks_enabled = True
    if opt is not None:
        checks_enabled = str(opt).lower() not in {"0", "false", "off", "no"}
    state["checks_enabled"] = checks_enabled
    return state


@router.put("/api/system/update-check-enabled")
async def set_update_check_enabled(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Enable/disable the daily GitHub release check."""
    mycelos = request.app.state.mycelos
    enabled = bool(payload.get("enabled", True))
    mycelos.memory.set(
        user_id="default",
        scope="system",
        key="system.check_for_updates",
        value="true" if enabled else "false",
    )
    return {"ok": True, "enabled": enabled}


@router.post("/api/models/refresh")
async def refresh_models(request: Request) -> dict[str, Any]:
    """Trigger an on-demand refresh of the LLM model registry.

    Delegates to the ModelUpdaterHandler (deterministic — no LLM call).
    Returns ``{"added": [...], "updated_count": N, "total": N}``.
    """
    mycelos = request.app.state.mycelos
    result = mycelos.model_updater.run("default")
    return result


@router.get("/api/models/winners")
async def model_winners() -> dict[str, Any]:
    """Top-3-per-provider 'winners' that the auto-setup picks.

    Reuses register_provider_models's logic: filters out legacy
    models, sorts newest-version-first within each tier, and
    returns the same one-per-tier set the onboarding flow would
    pick on a fresh install. Used by Settings → Models to render
    the prominent recipes-style cards before the full table.

    Shape: ``{provider_id: [{id, tier, ...}]}`` per provider that
    has any winner. Providers with no current-generation models
    (e.g. ollama before discovery) return an empty list.
    """
    from mycelos.llm.providers import PROVIDERS, get_provider_models

    result: dict[str, list[dict[str, Any]]] = {}
    for provider_id in PROVIDERS:
        try:
            catalog = get_provider_models(provider_id) or []
        except Exception:
            catalog = []
        picked: list[dict[str, Any]] = []
        seen_tiers: set[str] = set()
        for m in catalog:
            if m.tier and m.tier not in seen_tiers:
                picked.append({
                    "id": m.id,
                    "name": m.name,
                    "provider": m.provider,
                    "tier": m.tier,
                    "input_cost_per_1k": m.input_cost_per_1k,
                    "output_cost_per_1k": m.output_cost_per_1k,
                    "max_context": m.max_context,
                })
                seen_tiers.add(m.tier)
        if picked:
            result[provider_id] = picked
    return {"providers": result}


def _is_date_only_bump(old_id: str, new_id: str) -> bool:
    """True when old_id and new_id only differ by a trailing date.

    Matches patterns like ``gpt-5.4-2026-03-05`` vs. ``gpt-5.4-2026-04-15``
    (or single bare ``...-20260305`` variants). Same base, different
    date-stamp → weekly spam rather than a real upgrade, so we skip
    surfacing it in the migration banner.
    """
    date_suffix = re.compile(r"[-_](\d{4}-\d{2}-\d{2}|\d{8})$")
    old_base = date_suffix.sub("", old_id)
    new_base = date_suffix.sub("", new_id)
    if old_base == new_base and old_base != old_id:
        return True
    return False


@router.get("/api/models/upgrades")
async def model_upgrades(request: Request) -> dict[str, Any]:
    """Detect which currently-registered models have a newer version
    in the same (provider, tier) bucket, and which agent / system /
    workflow assignments use the old one.

    For each old model that has a newer counterpart we return:
        {
          "old_id": "anthropic/claude-opus-4-5",
          "new_id": "anthropic/claude-opus-4-7",
          "tier":   "opus",
          "provider": "anthropic",
          "assignments": [
              {"key": "agent:mycelos:execution", "label": "Mycelos · execution",
               "agent_id": "mycelos", "purpose": "execution", "priority": 1},
              {"key": "system::execution",       "label": "System default · execution", ...},
          ],
        }

    Sorted by 'most assignments first' so the UI prioritizes the
    upgrade with the broadest impact. Date-suffix-only bumps
    (e.g. gpt-5.4-2026-03-05 → gpt-5.4-2026-04-15) are excluded —
    only major / minor version jumps qualify, otherwise users get
    spammed weekly.
    """
    from mycelos.llm.providers import (
        get_provider_models,
        _version_key,
    )

    mycelos = request.app.state.mycelos
    registered_rows = mycelos.storage.fetchall(
        "SELECT id, provider, tier FROM llm_models"
    )
    registered_ids = {r["id"] for r in registered_rows}

    # Group registered models by (provider, tier)
    by_bucket: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in registered_rows:
        if not r.get("provider") or not r.get("tier"):
            continue
        by_bucket.setdefault((r["provider"], r["tier"]), []).append(dict(r))

    # Find candidate upgrades by inspecting providers we have.
    upgrades: list[dict[str, Any]] = []
    seen_provs: set[str] = set()
    for prov, tier in by_bucket:
        seen_provs.add(prov)

    for prov in seen_provs:
        try:
            catalog = get_provider_models(prov) or []
        except Exception:
            continue
        # Latest per tier from catalog.
        latest_per_tier: dict[str, str] = {}
        for m in catalog:
            if m.tier and m.tier not in latest_per_tier:
                latest_per_tier[m.tier] = m.id

        for tier, latest_id in latest_per_tier.items():
            bucket = by_bucket.get((prov, tier), [])
            for row in bucket:
                if row["id"] == latest_id:
                    continue
                # Old version — check if 'latest' is genuinely newer
                # by version key, not just a date-suffix sibling.
                if _version_key(latest_id) >= _version_key(row["id"]):
                    # latest_id sorts later (= older with our negated
                    # version key) than the row → not an upgrade.
                    continue
                if _is_date_only_bump(row["id"], latest_id):
                    continue
                # Find which assignments still pin this old model.
                rows = mycelos.storage.fetchall(
                    """SELECT a.agent_id, a.purpose, a.priority,
                              COALESCE(g.name, a.agent_id) AS agent_name
                         FROM agent_llm_models a
                         LEFT JOIN agents g ON g.id = a.agent_id
                        WHERE a.model_id = ?""",
                    (row["id"],),
                )
                assignments = []
                for slot in rows:
                    agent_id = slot["agent_id"]
                    purpose = slot.get("purpose") or "execution"
                    if agent_id is None:
                        label = f"System default · {purpose}"
                        key = f"system::{purpose}"
                    else:
                        label = f"{slot.get('agent_name') or agent_id} · {purpose}"
                        key = f"agent:{agent_id}:{purpose}"
                    assignments.append({
                        "key": key,
                        "label": label,
                        "agent_id": agent_id,
                        "purpose": purpose,
                        "priority": slot.get("priority", 1),
                    })
                if not assignments:
                    # No live use of the old model — nothing to migrate.
                    continue
                upgrades.append({
                    "old_id": row["id"],
                    "new_id": latest_id,
                    "tier": tier,
                    "provider": prov,
                    "new_already_registered": latest_id in registered_ids,
                    "assignments": assignments,
                })

    upgrades.sort(key=lambda u: -len(u["assignments"]))
    return {"upgrades": upgrades}


@router.post("/api/models/migrate")
async def migrate_model(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Replace one model with another across the selected assignment slots.

    Body: ``{"old_id": "...", "new_id": "...", "keys": [
        "system::execution", "agent:mycelos:execution", ...
    ]}``

    Atomic per-slot: if the new model isn't in the registry yet,
    register it first using the catalog metadata. Selected slots
    get re-pointed; unselected ones are left alone — that's the
    explicit-opt-out the user picked in the UI.
    """
    from mycelos.llm.providers import get_provider_models

    mycelos = request.app.state.mycelos
    old_id = payload.get("old_id") or ""
    new_id = payload.get("new_id") or ""
    keys = payload.get("keys") or []
    if not old_id or not new_id or not isinstance(keys, list):
        return JSONResponse(
            {"error": "old_id, new_id, and keys[] required"}, status_code=400
        )

    # Ensure the new model exists in llm_models — if the registry hasn't
    # synced it yet, pick the metadata from the catalog and register on
    # the fly so the assignment FK is satisfiable.
    if not mycelos.model_registry.get_model(new_id):
        provider = new_id.split("/", 1)[0] if "/" in new_id else ""
        target = None
        if provider:
            for m in get_provider_models(provider) or []:
                if m.id == new_id:
                    target = m
                    break
        if target is None:
            return JSONResponse(
                {"error": f"Cannot register unknown model '{new_id}'"},
                status_code=400,
            )
        mycelos.model_registry.add_model(
            model_id=target.id,
            provider=target.provider,
            tier=target.tier,
            input_cost_per_1k=target.input_cost_per_1k,
            output_cost_per_1k=target.output_cost_per_1k,
            max_context=target.max_context,
        )

    # Apply the migration slot-by-slot.
    migrated: list[str] = []
    for key in keys:
        parts = key.split(":")
        if len(parts) != 3:
            continue
        kind, agent_id_raw, purpose = parts
        if kind not in ("agent", "system"):
            continue
        if kind == "system":
            # Replace any system-default row (agent_id IS NULL) that
            # currently points at old_id, preserving priority.
            rows = mycelos.storage.fetchall(
                """SELECT priority FROM agent_llm_models
                    WHERE agent_id IS NULL AND purpose = ? AND model_id = ?""",
                (purpose, old_id),
            )
            for r in rows:
                mycelos.storage.execute(
                    """UPDATE agent_llm_models
                          SET model_id = ?
                        WHERE agent_id IS NULL
                          AND purpose = ?
                          AND model_id = ?
                          AND priority = ?""",
                    (new_id, purpose, old_id, r["priority"]),
                )
            migrated.append(key)
        else:
            rows = mycelos.storage.fetchall(
                """SELECT priority FROM agent_llm_models
                    WHERE agent_id = ? AND purpose = ? AND model_id = ?""",
                (agent_id_raw, purpose, old_id),
            )
            for r in rows:
                mycelos.storage.execute(
                    """UPDATE agent_llm_models
                          SET model_id = ?
                        WHERE agent_id = ? AND purpose = ?
                          AND model_id = ? AND priority = ?""",
                    (new_id, agent_id_raw, purpose, old_id, r["priority"]),
                )
            migrated.append(key)

    mycelos.audit.log("models.migrated", details={
        "old_id": old_id,
        "new_id": new_id,
        "keys": migrated,
    })
    return {"status": "migrated", "old_id": old_id, "new_id": new_id, "keys": migrated}


@router.put("/api/models/system-defaults")
async def update_system_defaults(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Replace the system-wide default model chain for a given purpose.

    Body: {"purpose": "execution" | "classification", "model_ids": [...]}
    System defaults are used when an agent has no explicit assignment
    (execution) or for background/cheapest-model calls (classification).
    """
    mycelos = request.app.state.mycelos
    purpose = payload.get("purpose")
    if purpose not in ("execution", "classification"):
        return JSONResponse(
            {"error": "purpose must be 'execution' or 'classification'"},
            status_code=400,
        )
    model_ids = payload.get("model_ids") or []
    if not isinstance(model_ids, list) or not all(isinstance(m, str) for m in model_ids):
        return JSONResponse({"error": "model_ids must be a list of strings"}, status_code=400)
    for model_id in model_ids:
        if not mycelos.model_registry.get_model(model_id):
            return JSONResponse(
                {"error": f"Model '{model_id}' is not registered"}, status_code=400
            )
    # set_system_defaults rewrites ALL system-default purposes at once, so
    # we need to preserve the other purpose's chain alongside this update.
    other = "classification" if purpose == "execution" else "execution"
    other_chain = mycelos.model_registry.resolve_models(None, other)
    by_purpose = {purpose: model_ids}
    if other_chain:
        by_purpose[other] = other_chain
    mycelos.model_registry.set_system_defaults(by_purpose)
    return {"ok": True, "purpose": purpose, "model_ids": model_ids}


@router.put("/api/models/assignments/{agent_id}")
async def update_agent_assignments(
    request: Request, agent_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Replace the model assignment list for one agent+purpose.

    Body: {"purpose": "execution", "model_ids": ["provider/model-a", "provider/model-b"]}
    Order is priority (first = highest).
    """
    mycelos = request.app.state.mycelos
    if not mycelos.agent_registry.get(agent_id):
        return JSONResponse({"error": f"Agent '{agent_id}' not found"}, status_code=404)
    purpose = payload.get("purpose", "execution")
    model_ids = payload.get("model_ids") or []
    if not isinstance(model_ids, list) or not all(isinstance(m, str) for m in model_ids):
        return JSONResponse({"error": "model_ids must be a list of strings"}, status_code=400)
    # Validate every model exists in the registry (fail-closed).
    for model_id in model_ids:
        if not mycelos.model_registry.get_model(model_id):
            return JSONResponse(
                {"error": f"Model '{model_id}' is not registered"}, status_code=400
            )
    mycelos.model_registry.set_agent_models(agent_id, model_ids, purpose=purpose)
    return {"ok": True, "agent_id": agent_id, "purpose": purpose, "model_ids": model_ids}


@router.get('/api/models/settings')
def model_settings(request: Request) -> dict[str, Any]:
    """Local configuration only; never query a provider or expose secrets."""
    from dataclasses import asdict
    from mycelos.llm.providers import PROVIDERS
    app = request.app.state.mycelos
    services = {c['service'] for c in app.storage.fetchall("SELECT service FROM credentials WHERE user_id = 'default'")}
    providers = []
    for provider in PROVIDERS.values():
        row = asdict(provider)
        row['configured'] = provider.id in services
        if provider.id in ('ollama', 'custom'):
            saved_url = app.memory.get('default', 'system', provider.id + '_url')
            row['url'] = saved_url or provider.default_url
            row['configured'] = row['configured'] or bool(saved_url)
        if provider.id == 'custom':
            from mycelos.llm.custom import env_api_base, env_has_secret
            # LITELLM_HOST / LITELLM_SECRET fill blanks; only flags leave here.
            env_url = env_api_base()
            row['env_url'] = bool(env_url)
            row['env_secret'] = env_has_secret()
            row['url'] = row['url'] or env_url
            row['configured'] = row['configured'] or bool(env_url and row['env_secret'])
        providers.append(row)
    return {'providers': providers}


@router.put('/api/models/providers/{provider_id}')
def save_model_provider(request: Request, provider_id: str, payload: dict[str, Any]):
    """Save provider access independently from onboarding and model assignment."""
    from urllib.parse import urlsplit
    from fastapi import HTTPException
    from mycelos.llm.providers import PROVIDERS
    from mycelos.setup import _validate_multi_field_credentials, SetupError, ensure_master_key
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise HTTPException(400, 'Dieser Anbieter ist nicht verfügbar.')
    app = request.app.state.mycelos
    ensure_master_key(app.data_dir)
    if provider_id in ('ollama', 'custom'):
        url = str(payload.get('url') or '').strip().rstrip('/')
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise HTTPException(400, 'Bitte eine gültige Server-Adresse mit http oder https eingeben.')
        credential = {'api_base': url}
        if provider_id == 'custom':
            from mycelos.llm.custom import env_has_secret
            old = app.credentials.get_credential('custom') or {}
            key = str(payload.get('api_key') or '').strip() or old.get('api_key')
            if key and any(c in key for c in ('\r', '\n')):
                raise HTTPException(400, 'Bitte ein gültiges Bearer-Token eingeben.')
            if not key and not env_has_secret():
                raise HTTPException(400, 'Bitte ein gültiges Bearer-Token eingeben.')
            if key:
                # Without a stored token, LITELLM_SECRET is read at call time
                # and never copied into the credential store.
                credential['api_key'] = key
        app.credentials.store_credential(provider_id, credential)
        app.memory.set('default', 'system', provider_id + '_url', url)
    elif provider.credential_fields:
        try:
            cleaned = _validate_multi_field_credentials(provider, payload.get('credentials') or {})
        except (SetupError, TypeError, AttributeError) as exc:
            raise HTTPException(400, str(exc))
        app.credentials.store_credential(provider_id, cleaned)
    else:
        key = str(payload.get('api_key') or '').strip()
        if not key:
            if not app.credentials.get_credential(provider_id):
                raise HTTPException(400, 'Bitte den API-Schlüssel eingeben.')
        else:
            app.credentials.store_credential(provider_id, {'api_key': key})
    app.audit.log('models.provider_saved', details={'provider': provider_id})
    return {'ok': True, 'provider': provider_id}


_STT_CHOICES = ('', 'custom')


@router.get('/api/models/stt')
def stt_settings(request: Request) -> dict[str, Any]:
    """Speech input choice. '' keeps the environment default (MYCELOS_STT_PROVIDER)."""
    import os
    from mycelos.speech.transcription import CUSTOM_STT_DEFAULT_MODEL
    app = request.app.state.mycelos
    provider = app.memory.get('default', 'system', 'stt_provider') or ''
    model = app.memory.get('default', 'system', 'stt_model') or ''
    return {'provider': provider if provider in _STT_CHOICES else '',
            'model': model or CUSTOM_STT_DEFAULT_MODEL,
            'env_default': os.environ.get('MYCELOS_STT_PROVIDER', 'openai').strip().lower() or 'openai'}


@router.put('/api/models/stt')
def save_stt_settings(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    from fastapi import HTTPException
    from mycelos.speech.transcription import CUSTOM_STT_DEFAULT_MODEL
    provider = str(payload.get('provider') or '').strip().lower()
    model = str(payload.get('model') or '').strip() or CUSTOM_STT_DEFAULT_MODEL
    if provider not in _STT_CHOICES:
        raise HTTPException(400, 'Diese Spracherkennung ist nicht verfügbar.')
    if len(model) > 256 or any(c.isspace() for c in model):
        raise HTTPException(400, 'Bitte einen Modellnamen ohne Leerzeichen eingeben.')
    app = request.app.state.mycelos
    app.memory.set('default', 'system', 'stt_provider', provider)
    app.memory.set('default', 'system', 'stt_model', model)
    app.audit.log('models.stt_saved', details={'provider': provider or 'environment', 'model': model})
    return {'ok': True, 'provider': provider, 'model': model}


@router.get('/api/models/catalog/{provider_id}')
def model_catalog(request: Request, provider_id: str):
    """Catalog lookup uses no completion; local discovery lists installed models."""
    from dataclasses import asdict
    from mycelos.llm.providers import PROVIDERS, get_provider_models
    from fastapi import HTTPException
    if provider_id not in PROVIDERS:
        raise HTTPException(400, 'Dieser Anbieter ist nicht verfügbar.')
    if provider_id == 'custom':
        from mycelos.llm.provider_test import discover_custom_models
        try:
            return {'models': discover_custom_models(request.app.state.mycelos)}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    if provider_id == 'ollama':
        from mycelos.llm.ollama import discover_ollama_models, classify_ollama_tier
        app = request.app.state.mycelos
        url = app.memory.get('default', 'system', 'ollama_url') or 'http://localhost:11434'
        models = discover_ollama_models(url)
        return {'models': [{'id': m.id, 'name': m.name, 'tier': classify_ollama_tier(m)} for m in models
                           if 'embed' not in m.id.lower()]}
    return {'models': [asdict(m) for m in get_provider_models(provider_id)]}


@router.post('/api/models')
def save_model(request: Request, payload: dict[str, Any]):
    from fastapi import HTTPException
    from mycelos.llm.providers import PROVIDERS
    provider = payload.get('provider')
    model_id = str(payload.get('model_id') or '').strip()
    tier = payload.get('tier', 'sonnet')
    if provider not in PROVIDERS or tier not in ('haiku', 'sonnet', 'opus'):
        raise HTTPException(400, 'Bitte Anbieter und Leistungsstufe wählen.')
    if not model_id or any(c.isspace() for c in model_id):
        raise HTTPException(400, 'Bitte eine Modell-ID ohne Leerzeichen eingeben.')
    if '/' in model_id and not model_id.startswith(provider + '/'):
        # OpenRouter model names include their upstream provider.
        if provider not in ('openrouter', 'custom'):
            raise HTTPException(400, 'Die Modell-ID gehört zu einem anderen Anbieter.')
    if not model_id.startswith(provider + '/'):
        model_id = provider + '/' + model_id
    app = request.app.state.mycelos
    existing = app.model_registry.get_model(model_id) or {}
    from mycelos.setup import register_system_agents
    register_system_agents(app)
    app.model_registry.add_model(model_id, provider, tier,
        input_cost_per_1k=existing.get('input_cost_per_1k'),
        output_cost_per_1k=existing.get('output_cost_per_1k'),
        max_context=existing.get('max_context'))
    return {'ok': True, 'model_id': model_id}


@router.post('/api/models/providers/{provider_id}/test')
def test_model_provider(request: Request, provider_id: str):
    from fastapi import HTTPException
    from mycelos.llm.providers import PROVIDERS
    from mycelos.llm.provider_test import check_provider
    if provider_id not in PROVIDERS:
        raise HTTPException(400, 'Dieser Anbieter ist nicht verfügbar.')
    return check_provider(request.app.state.mycelos, provider_id)


@router.post('/api/models/providers/{provider_id}/discover')
def discover_provider_models(request: Request, provider_id: str):
    from fastapi import HTTPException
    from mycelos.llm.provider_test import discover_custom_models
    if provider_id != 'custom':
        raise HTTPException(400, 'Diese Modellerkennung ist für Custom vorgesehen.')
    app = request.app.state.mycelos
    try:
        models = discover_custom_models(app)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if models:
        from mycelos.setup import register_system_agents
        register_system_agents(app)
    with app.storage.transaction():
        for model in models:
            # Discovery adds missing aliases; existing choices remain untouched.
            existing = app.model_registry.get_model(model['id'])
            if not existing:
                app.model_registry.add_model(model['id'], 'custom', model['tier'],
                                             max_context=model.get('max_context'))
            elif model.get('max_context') and model['max_context'] != existing.get('max_context'):
                # Only the proxy-reported context window is refreshed.
                app.model_registry.add_model(model['id'], 'custom', existing['tier'],
                    input_cost_per_1k=existing.get('input_cost_per_1k'),
                    output_cost_per_1k=existing.get('output_cost_per_1k'),
                    max_context=model['max_context'],
                    status=existing.get('status') or 'available')
    app.audit.log('models.custom_discovered', details={'count': len(models)})
    return {'models': models, 'count': len(models)}


@router.get('/api/models/providers/{provider_id}/delete-preview')
async def provider_delete_preview(request: Request, provider_id: str):
    from mycelos.llm.provider_delete import preview
    try:
        return preview(request.app.state.mycelos, provider_id)
    except ValueError as error:
        return JSONResponse(status_code=422, content={'detail':str(error)})


@router.delete('/api/models/providers/{provider_id}')
async def delete_provider(request: Request, provider_id: str, payload: dict[str, Any]):
    from mycelos.llm.provider_delete import delete
    try:
        return delete(request.app.state.mycelos, provider_id, payload.get('token'))
    except ValueError as error:
        return JSONResponse(status_code=409, content={'detail':str(error)})
