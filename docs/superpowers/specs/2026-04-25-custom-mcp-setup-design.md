# Custom MCP Connector Setup — Design

**Date:** 2026-04-25
**Status:** Draft
**Scope:** Restore a usable Custom-MCP setup path on the Connectors page after the slash-command cleanup removed the chat-side `/connector add` flow. Recipes are out of scope (they have their own dedicated setup flows since Spec 1).

## Goal

Let the user add an arbitrary MCP server through the Web UI by entering a name, a launch command, and any number of environment variables (each marked secret or not). Pre-fill the env-var fields when the MCP Registry knows the package; otherwise leave them empty for manual entry.

## Problem

Today the "Add Connector" form on the Connectors page accepts a single `secret` string. The backend then either:

- looks up the env-var name from the recipe (e.g. `BRAVE_API_KEY` for `brave-search`), or
- falls back to the heuristic `<NAME>_API_KEY` for unknown packages.

The heuristic fails for ~50% of MCPs (some use `API_TOKEN`, `PAT`, `WORKSPACE_ID`, multiple vars, etc.). After the slash-command cleanup, the chat-side `/connector add <name> --secret <key>` path is also gone — Web UI is the only way to add a Custom MCP, and it's effectively broken for anything outside our recipes.

## Decisions

### D1: Recipes are not affected

The two existing setup flows — recipe cards' Setup button (Channels, MCP recipes with `setup_flow="secret"`) and OAuth wizard (`setup_flow="oauth_http"`) — stay exactly as they are. They use `recipe.credentials[0].env_var` already and that works.

This spec only changes the **Add Connector** inline form (the one that opens when the header button is clicked).

### D2: Multi-variable form replaces the single-secret field

Form fields:

- **Name** (single line, required)
- **Command** (single line, required, must start with one of: `npx`, `docker`, `python`, `python3`, `node`, `uvx`, `deno`, `bun`)
- **Environment Variables** — repeatable rows, each with: Key, Value, Secret-Toggle, Delete-button. Empty rows are filtered out before submit. At least zero is allowed (some MCPs need none).

Submit is disabled when Name or Command is blank.

### D3: MCP-Registry lookup as a hint, not a gate

When the Command field loses focus, the frontend extracts the npm package (the first arg starting with `@` or containing `/`, ignoring `-y`/`--yes` etc.) and calls a new endpoint:

```
GET /api/connectors/lookup-env-vars?package=<encoded-package>
```

It returns a list of `{ name, secret }` entries from the existing `mcp_search.lookup_env_vars()` helper. On hit, the frontend pre-fills the env-vars rows (one row per known var, key field disabled, value empty for the user to fill, secret-toggle reflecting the registry's hint). On miss or error, the frontend keeps whatever the user already typed and adds one empty row.

A small text hint above the env-vars list ("MCP Registry suggested 2 variables") informs the user when prefill happened.

### D4: One credential row per connector, env-vars stored as JSON blob

The `credentials` table already stores complex blobs (OAuth client_secret JSON in `oauth_client_credential_service`, OAuth tokens in `oauth_token_credential_service`). We extend the same pattern.

For Custom MCPs:

- `service` = connector name (e.g. `"context7"`)
- `value.api_key` = `json.dumps({"API_KEY": "...", "WORKSPACE_ID": "..."})`
- `value.env_var` = `"__multi__"` (sentinel marker — distinguishes from legacy single-var case)

This avoids any DB schema change. The `__multi__` sentinel is treated as a tag at spawn time, not a real env-var name.

### D5: Proxy spawn-time logic

The proxy reads the credential row when starting the MCP subprocess. If `env_var == "__multi__"`, it parses `api_key` as JSON and merges every key/value pair into the subprocess env. Otherwise it falls back to the legacy single `{env_var: api_key}` pair (recipes, old custom MCPs).

### D6: Empty-rows policy

Frontend allows empty Key/Value rows for UX (the `+ Variable` button always adds a fresh empty row). Backend filters out rows where Key is empty before storing. Rows where Key is set but Value is empty are stored as empty-string values — the MCP server gets the env var defined but blank. We do not warn about this (some packages use empty as a feature flag).

### D7: Legacy `secret: string` payload is preserved

`POST /api/connectors` keeps accepting the old `secret: string` field for compatibility. New frontend code uses the new `env_vars: dict[str, str]` field. If both are sent, `env_vars` wins. Recipe-setup code paths (which still send `secret`) are untouched.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│ src/mycelos/frontend/pages/connectors.html           │
│   newConnector.envVars = [{key, value, isSecret}]    │
│   onCommandBlur → fetch /api/connectors/lookup-...   │
│   addConnector → POST /api/connectors {env_vars}     │
└──────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────┐
│ src/mycelos/gateway/routes.py                        │
│   GET  /api/connectors/lookup-env-vars               │
│        → mcp_search.lookup_env_vars(package)         │
│   POST /api/connectors                               │
│        accepts env_vars: dict[str, str]              │
│        stores as {api_key: json.dumps(...),          │
│                   env_var: "__multi__"}              │
└──────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────┐
│ src/mycelos/security/proxy_server.py                 │
│   _resolve_credential / spawn path:                  │
│     if env_var == "__multi__":                       │
│         env.update(json.loads(api_key))              │
│     else:                                            │
│         env[env_var] = api_key  # legacy             │
└──────────────────────────────────────────────────────┘
```

## Components

### `src/mycelos/frontend/pages/connectors.html`

Replace the single `<input type="password" x-model="newConnector.secret">` with a repeatable env-vars block. Alpine state changes:

```javascript
newConnector: {
  name: '',
  command: '',
  envVars: [{ key: '', value: '', isSecret: true }],
  lookupHit: 0,  // 0 = no lookup yet, N = lookup found N vars
}
```

New methods on the Alpine factory:

```javascript
addEnvVar() { this.newConnector.envVars.push({ key: '', value: '', isSecret: true }); }

removeEnvVar(idx) {
  this.newConnector.envVars.splice(idx, 1);
  if (this.newConnector.envVars.length === 0) this.addEnvVar();
}

async onCommandBlur() {
  const cmd = (this.newConnector.command || '').trim();
  if (!cmd) return;
  const parts = cmd.split(/\s+/);
  // Find the package: first arg that looks like @scope/name or contains /
  const pkg = parts.find(p => p.startsWith('@') || (p && !p.startsWith('-') && p.includes('/')));
  if (!pkg) return;
  try {
    const resp = await fetch('/api/connectors/lookup-env-vars?package=' + encodeURIComponent(pkg));
    if (!resp.ok) return;
    const data = await resp.json();
    const known = Array.isArray(data?.env_vars) ? data.env_vars : [];
    if (!known.length) { this.newConnector.lookupHit = 0; return; }
    this.newConnector.envVars = known.map(v => ({
      key: v.name,
      value: '',
      isSecret: v.secret !== false,
    }));
    this.newConnector.lookupHit = known.length;
  } catch (_) {
    /* network error → leave the form alone */
  }
}
```

`addConnector()` builds the payload:

```javascript
const env_vars = {};
for (const row of this.newConnector.envVars) {
  const k = (row.key || '').trim();
  if (!k) continue;
  env_vars[k] = (row.value || '').toString();
}
await MycelosAPI.post('/api/connectors', {
  name: this.newConnector.name.trim(),
  command: this.newConnector.command.trim(),
  env_vars,
});
```

`resetForm()` resets `envVars` to one empty row and `lookupHit` to 0.

The form HTML adds a Material icon for delete, the lookup-hit hint span, and the `+ Variable` button. Tailwind classes follow existing form conventions on the page.

### `src/mycelos/gateway/routes.py`

**New endpoint:**

```python
@api.get("/api/connectors/lookup-env-vars")
async def lookup_connector_env_vars(package: str) -> dict:
    """Look up env-var hints for an MCP package from the registry."""
    from mycelos.connectors.mcp_search import lookup_env_vars
    try:
        env_vars = lookup_env_vars(package) or []
    except Exception:
        env_vars = []
    return {"env_vars": env_vars}
```

**Modified endpoint:** the existing `POST /api/connectors` request model gets a new optional field `env_vars: dict[str, str] | None = None`. Handler logic:

```python
if body.env_vars:
    # Filter empty keys; values may be empty.
    cleaned = {k: v for k, v in body.env_vars.items() if k.strip()}
    credential_value = {
        "api_key": json.dumps(cleaned),
        "env_var": "__multi__",
        "connector": body.name,
    }
elif body.secret:
    # Legacy single-var path (recipes, old payloads).
    env_var_name = (
        recipe.credentials[0].get("env_var", "") if recipe and recipe.credentials
        else f"{body.name.upper().replace('-', '_')}_API_KEY"
    )
    credential_value = {
        "api_key": body.secret,
        "env_var": env_var_name,
        "connector": body.name,
    }
else:
    credential_value = None  # no creds — some MCPs need none
```

The rest of the handler (storing the credential, registering the connector, audit, config-gen) stays the same.

### `src/mycelos/security/proxy_server.py` and/or `src/mycelos/connectors/mcp_client.py`

Wherever credentials are resolved into env vars at MCP-spawn time, add the multi-var branch:

```python
if cred.get("env_var") == "__multi__":
    try:
        multi = json.loads(cred.get("api_key") or "{}")
        if isinstance(multi, dict):
            for k, v in multi.items():
                env[k] = str(v)
    except json.JSONDecodeError:
        pass  # malformed — skip; the MCP will fail with a clear error
else:
    env_name = cred.get("env_var")
    if env_name:
        env[env_name] = cred.get("api_key", "")
```

Locate the existing single-var injection by greppping for the strings `env_var` and `api_key` together in `proxy_server.py` and `mcp_client.py`. The plan task pinpoints the exact location.

### `src/mycelos/connectors/mcp_search.py`

`lookup_env_vars(package)` already exists from the pre-cleanup code. Verify in the plan that it still works — if it was deleted along with the `/connector add` slash code, restore from git history.

## Data Flow

### Add Custom MCP

```
1. User clicks "Add Connector" → form opens (post-Task-4 form is below header)
2. User types Name="context7"
3. User pastes Command="npx -y @upstash/context7-mcp@latest"
4. Command field loses focus → frontend extracts "@upstash/context7-mcp@latest"
5. fetch GET /api/connectors/lookup-env-vars?package=@upstash/context7-mcp@latest
6. Backend → mcp_search.lookup_env_vars(...) → e.g. [{name: "API_KEY", secret: true}]
7. Frontend prefills one row: key="API_KEY" (disabled), value="" (focus), secret=true
   plus shows hint: "MCP Registry suggested 1 variable"
8. User pastes the API key into the value field
9. User clicks "Add"
10. POST /api/connectors {name: "context7", command: "...", env_vars: {API_KEY: "ctx7_..."}}
11. Backend stores credential: service="context7",
        value={"api_key": '{"API_KEY": "ctx7_..."}', "env_var": "__multi__", "connector": "context7"}
12. Backend registers connector_registry row, audit, config-gen
13. Frontend reloads connector list, the new card appears in "Installed"
14. First tool call → MCP-spawn reads credential, sees __multi__, injects {API_KEY: "ctx7_..."} into env
15. context7 MCP server starts, tools become available
```

### Add MCP without registry hit

Same flow except step 6 returns `{env_vars: []}`. Frontend shows zero hint text, leaves a single empty row, user enters Key + Value manually. Step 9+ identical.

## Error Handling

- Form validation: Submit disabled when Name or Command blank. Command prefix validated client-side (warning under field if not in the allowed launchers list — does not block submit; backend re-validates).
- Lookup endpoint failure: silent (frontend treats as no-hit, leaves user's input alone). No toast — registry availability is not the user's problem.
- Backend payload missing both `secret` and `env_vars`: connector still registers (some MCPs are envless), no credential stored.
- Spawn-time JSON parse error: skip injection, MCP server starts without the env vars; will fail with its own clear error message visible in MCP logs.
- Duplicate name: backend rejects with 409; frontend shows the error.

## Testing

- `tests/test_custom_mcp_setup.py` (new):
  - `POST /api/connectors {env_vars: {A: "1", B: "2"}}` stores credential with `env_var == "__multi__"` and `json.loads(api_key) == {"A": "1", "B": "2"}`.
  - `POST /api/connectors {secret: "x"}` (legacy path) still stores with the old shape.
  - `POST /api/connectors {env_vars: {}, secret: "x"}` → legacy path wins (empty env_vars treated as not-present).
  - `POST /api/connectors {env_vars: {"": "ignored", "A": "1"}}` → empty key filtered; only `A` stored.
  - Duplicate name returns 409 (existing behavior; verify regression).
- `tests/test_lookup_env_vars_endpoint.py` (new):
  - `GET /api/connectors/lookup-env-vars?package=@modelcontextprotocol/server-fetch` returns shape `{env_vars: [...]}` (registry hit OR miss both produce 200).
  - Endpoint never raises on bad input.
- `tests/test_mcp_spawn_multi_env.py` (new):
  - Mock the subprocess spawn; verify that a credential with `env_var == "__multi__"` results in all JSON keys being present in the constructed env dict.
- Existing baseline (`pytest tests/`) stays at zero failures.

Manual verification (Stefan):

1. Open Connectors page, click "Add Connector".
2. Form appears below header (existing fix).
3. Paste a known package command (e.g. `npx -y @upstash/context7-mcp`), tab away from Command field.
4. Verify env-vars rows pre-fill with the registry hint.
5. Submit, verify the new connector appears in "Installed".
6. Test the connector via the existing card "Test" button.

## Success Criteria

1. Add Connector form has Name, Command, and a repeatable Environment Variables list.
2. Command-field blur triggers a registry lookup and pre-fills env-vars when found.
3. Backend `POST /api/connectors` accepts `env_vars: dict[str, str]` and stores them as a JSON blob with the `__multi__` sentinel.
4. MCP spawn injects all entries from the JSON blob into the subprocess env.
5. Recipe setup paths (Channels, MCP recipes with secret/oauth_http) are unchanged and still pass their existing tests.
6. Full test baseline stays green.
7. CHANGELOG entry under Week 17.

## Non-Goals

- Recipe setup form changes — out of scope, recipes have their own wizards.
- Capability auto-discovery for the new connector — that's the next spec (Capability Hybrid).
- Removing the legacy `secret: string` field — kept for backward compatibility with recipe code.
- Per-variable rotation UI — single-shot create now; rotation is a future feature.
- Per-variable encryption granularity — entire JSON blob is encrypted by the existing credential-store layer.
