# Connector Registry Unification — Design

**Date:** 2026-04-24
**Status:** Draft
**Scope:** Spec 1 of 3. A follow-up spec (`ui.open_page` tool + deep-link catalog) lets the Chat agent route users to admin pages. A further spec (Capability Hybrid) adds runtime discovery of capabilities for ad-hoc MCP servers.

## Goal

Eliminate drift between the `CONNECTORS` dict (`cli/connector_cmd.py`) and the `MCPRecipe` registry (`connectors/mcp_recipes.py`). Make the distinction between **Channel** connectors and **MCP** connectors explicit in both code and UI.

## Problem Statement

Today the codebase has two parallel connector registries with overlapping but drifting data:

1. `CONNECTORS` dict in `cli/connector_cmd.py` — six entries: `web-search-duckduckgo`, `web-search-brave`, `http`, `telegram`, `github`. Used by `_setup_connector`, tested in `test_connector_setup.py`.
2. `RECIPES` dict in `connectors/mcp_recipes.py` — ~20 entries including `gmail`, `github`, `brave-search`, `filesystem`, `postgres`. Used at runtime by `mcp_client`, `mcp_manager`, slash commands.

Concrete symptoms:

- `github` exists in both; capabilities defined twice and have drifted.
- `connector list` reads from the DB-backed registry, but `connector setup` reads from the hardcoded dict. Adding a recipe makes it visible in `list` but not setupable.
- Channels (Telegram — long-running listener, outbound pushing) and MCP connectors (request/response subprocess or HTTP) share the same dict but have fundamentally different lifecycles.
- `http` and `web-search-duckduckgo` are not real "connectors" — they are Python tools registered in the in-process tool registry. Their presence in `CONNECTORS` implies a framework they don't participate in.

## Decisions

### D1: One registry, explicit `kind` field

Telegram is already in the `RECIPES` registry today (differentiated only by `transport="channel"`). Rather than extract it into a separate `CHANNELS` registry and break ~15 call sites, we make the distinction explicit on `MCPRecipe` itself:

- Add `kind: Literal["channel", "mcp"]` to `MCPRecipe`. Default `"mcp"`.
- `transport="channel"` is deprecated; the Telegram recipe sets `kind="channel"` and its `transport` field becomes irrelevant.
- CLI `list`, Web UI, and gateway endpoints read `recipe.kind` to decide which section a recipe belongs to.

No `service` kind. HTTP / DuckDuckGo are removed from the connector framework entirely.

### D2: HTTP and DuckDuckGo remain as always-on Python tools

They are registered in the tool registry at startup (like Knowledge-Base, Memory). Their capability grants (`http.get`, `http.post`, `search.web`) are applied during `mycelos init`, not through connector setup. They no longer appear in `connector list`.

### D3: Display split

Both CLI (`connector list`) and Web UI (`/connectors` page) render two separate sections: **Channels** and **MCP Connectors**. The `Kind` column introduced as a drift mitigation is removed from the "not yet configured" tables (redundant once sections are separate). The "Installed" section keeps a kind badge since it's a single list.

### D4: Capabilities stay statically in recipes (for now)

For known recipes (both channel and MCP), capabilities are declared in the recipe and are the single source of truth for policy grants. Runtime discovery for user-registered ad-hoc MCPs is deferred to Spec 2.

### D5: No database migration required

Stefan is the only user. Any stray `connector_registry` rows with `kind='service'` are tolerated with a boot-time warning, not migrated. Fresh installs never produce them.

### D6: Connector setup happens only in CLI and Web UI

The Chat-Slash command `/connector` is trimmed to read-only operations. All setup verbs (`add`, `setup`, `remove`, `test`) are removed from the slash handler. Setup happens through one of two paths:

- **Terminal** — `mycelos connector setup <id>` (interactive prompts, OAuth via browser link the CLI prints).
- **Web UI** — `/connectors` page (full wizard, OAuth redirect, file upload).

Rationale:

- Chat setup duplicates CLI and Web logic without adding a use case — OAuth flows cannot run inside a chat transcript, and `/connector add x --secret <token>` writes raw credentials into conversation history, violating Constitution Rule 4 (Credentials Never Visible).
- Chat remains useful for discovery (`/connector list`) and status inspection (`/connector test` — see caveat below).
- A follow-up spec adds a `ui.open_page` tool so the Chat agent can actively route users into the right admin page instead of explaining setup in prose.

**Kept in Chat:** `/connector list`, `/connector help`.
**Removed from Chat:** `/connector add`, `/connector setup`, `/connector remove`, `/connector test`. `test` moves to CLI only (it already exists there).

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  src/mycelos/connectors/mcp_recipes.py                  │
│    MCPRecipe gains `kind: "channel" | "mcp"` field      │
│    RECIPES["telegram"] updated: kind="channel"          │
│    get_recipe, list_recipes unchanged                   │
└─────────────────────────────────────────────────────────┘
                         ▲
                         │
┌─────────────────────────────────────────────────────────┐
│  src/mycelos/cli/connector_cmd.py (REDUCED ~903 → ~500) │
│    CONNECTORS dict: REMOVED                             │
│    _setup_mcp(app, recipe: MCPRecipe)                   │
│    _setup_channel(app, recipe: MCPRecipe)               │
│       — handles recipe.kind == "channel" (Telegram)     │
│    setup_cmd: dispatches on recipe.kind                 │
│    list_cmd: renders 2 separate "available" sections    │
│       by grouping on recipe.kind                        │
└─────────────────────────────────────────────────────────┘
                         ▲
                         │
┌─────────────────────────────────────────────────────────┐
│  src/mycelos/chat/slash_commands.py                     │
│    /connector setup/add/remove/test verbs REMOVED       │
│    /connector list + /connector help retained           │
│    Help text points to CLI and Web UI for setup         │
└─────────────────────────────────────────────────────────┘
```

## Components

### `src/mycelos/connectors/mcp_recipes.py` (extended)

Add a `kind` field to `MCPRecipe`:

```python
from typing import Literal

@dataclass(frozen=True)
class MCPRecipe:
    id: str
    name: str
    description: str
    kind: Literal["channel", "mcp"] = "mcp"
    # ... all existing fields unchanged
```

Update the `telegram` entry in `RECIPES` to set `kind="channel"`. Everything else stays `kind="mcp"` (default, no change needed).

No new module. No new imports. `get_recipe` / `list_recipes` work unchanged.

### `src/mycelos/cli/connector_cmd.py` (reduced)

- Remove the top-level `CONNECTORS` dict.
- Split `_setup_connector(app, key, info)` into two functions:
  - `_setup_mcp(app, recipe: MCPRecipe)` — handles `kind="mcp"` recipes. Uses `recipe.credentials[0]` for env-var/help prompting and `recipe.capabilities_preview` for policy grants. Dispatches OAuth flow if `recipe.setup_flow == "oauth_http"`.
  - `_setup_channel(app, recipe: MCPRecipe)` — handles `kind="channel"` recipes. Keeps Telegram-specific logic (allowlist collection via `getUpdates`, mode selection, `channels`-table write). Future channels plug in here by adding their own branch; for now it's Telegram-only.
- `setup_cmd(name)`:
  1. `get_recipe(name)` → if `recipe.kind == "channel"`: `_setup_channel`; else: `_setup_mcp`.
  2. `None` → error with pointer to `connector list`.
- `list_cmd` renders three sections:
  - **Installed** (configured connectors from `connector_registry`, kind badge kept — single table)
  - **Channels (not yet configured)** — columns: Recipe, Setup, Description
  - **MCP Connectors (not yet configured)** — columns: Recipe, Category, Setup, Description
  - Grouping is done by `recipe.kind` from `RECIPES.values()`.

### `src/mycelos/connectors/connector_registry.py`

- `Kind` enum: keep `channel` and `mcp`. No `service`.
- On boot: if a row has `kind='service'`, log a warning and render it under "Installed" with badge `unknown (legacy)`. Do not delete.

### `src/mycelos/chat/slash_commands.py` (trimmed)

Remove setup verbs from `/connector`:

- **Keep:** `/connector list`, `/connector help` (or bare `/connector`).
- **Remove:** `/connector add`, `/connector setup`, `/connector remove`, `/connector test`, `_connector_add_smart`, `_connector_add_with_key`, `_connector_add_custom`, `_connector_add` — entire setup code path in the slash handler.
- Help text rewritten to point at CLI (`mycelos connector setup <id>`) and Web UI (`/connectors` page) for setup actions.
- Update the autocomplete registry in `src/mycelos/cli/completer.py` (`SLASH_COMMANDS` dict) to reflect the reduced verb set — per CLAUDE.md "Slash Commands & Autocomplete" rule.

### Gateway endpoint `/api/connectors/recipes`

Returns the registry grouped by `kind`:

```json
{
  "channels": [
    { "id": "telegram", "name": "Telegram Bot", "kind": "channel",
      "description": "...", "setup_flow": "secret" }
  ],
  "mcp": [
    { "id": "github", "name": "GitHub", "kind": "mcp",
      "category": "code", "setup_flow": "secret",
      "description": "...", "capabilities_preview": [...] }
  ]
}
```

The existing `/api/connectors/recipes/{id}` keeps working unchanged — it returns a single recipe by id, now including the `kind` field.

### Web UI

`src/mycelos/frontend/pages/connectors.html` is updated to render three sections matching the CLI: Installed, Channels, MCP Connectors. Card layout per entry, category-based subgrouping inside the MCP section if it helps readability.

## Data Flow

### Setup

```
mycelos connector setup <id>
  ↓
recipe = get_recipe(id)
  ├─ None                    → error + "see mycelos connector list"
  ├─ recipe.kind == "channel" → _setup_channel(recipe)
  │                               ├─ collect credential (recipe.credentials[0])
  │                               ├─ channel-specific setup (Telegram: getUpdates, mode, allowlist, channels row)
  │                               ├─ credential_store.store(recipe.id, ...)
  │                               ├─ connector_registry.register(id, kind="channel")
  │                               ├─ config.apply_from_state()
  │                               └─ audit.log("connector.setup", id=id, kind="channel")
  │
  └─ recipe.kind == "mcp"    → _setup_mcp(recipe)
                                  ├─ collect credential (recipe.credentials[0], or OAuth flow if setup_flow="oauth_http")
                                  ├─ credential_store.store(recipe.id, ...)
                                  ├─ policy_engine grants for recipe.capabilities_preview
                                  ├─ connector_registry.register(id, kind="mcp")
                                  ├─ config.apply_from_state()
                                  └─ audit.log("connector.setup", id=id, kind="mcp")
```

### List

```
mycelos connector list
  ↓
configured = connector_registry.list_configured()
  ↓
render "Installed" table with Kind column
  ↓
not_configured_channels = [c for c in CHANNELS if c.id not in configured_ids]
render "Channels (not yet configured)" table
  ↓
not_configured_mcp = [r for r in RECIPES if r.id not in configured_ids]
render "MCP Connectors (not yet configured)" table
```

## Error Handling

- **Unknown id in setup:** CLI exits with exit code 1, shows "Unknown connector: {id}. Run `mycelos connector list` to see available connectors."
- **Channel setup handler raises:** Credential store is rolled back (transactional), `connector.setup.failed` audit event is emitted, config generation is not created. User sees the raised error.
- **Policy grant fails:** Same — rollback credential, audit `connector.setup.failed`.
- **Legacy `kind='service'` rows in DB:** Boot-time warning log, rendered with `unknown (legacy)` badge. No blocking.

## Testing

Every change ships with tests. The baseline must stay green (zero failing tests in `pytest tests/ --ignore=tests/e2e --ignore=tests/integration`).

### New tests

- `tests/test_recipe_kind.py` — `MCPRecipe.kind` defaults to `"mcp"`; `RECIPES["telegram"].kind == "channel"`; all other recipes have `kind == "mcp"`.
- `tests/test_connector_list_two_sections.py` — output contains both "Channels" and "MCP Connectors" headers; Telegram is under Channels; GitHub is under MCP.
- `tests/test_frontend_connectors_api.py` — `/api/connectors/recipes` returns `channels` and `mcp` keys with correct contents.

### Tests to rewrite

- `tests/test_connector_setup.py` — delete `CONNECTORS`-based assertions. Test `_setup_mcp` with a real `MCPRecipe` fixture; test `_setup_channel` with the Telegram recipe. Keep the policy-grant assertion pattern.
- `tests/test_telegram_channel.py` — replace `CONNECTORS["telegram"]` lookups with `RECIPES["telegram"]` / `get_recipe("telegram")`. Assertions target the same fields (`env_var`, `kind == "channel"`).
- `tests/test_slash_commands.py` (and any test that invokes `/connector add`/`setup`/`test`/`remove`) — delete or rewrite to assert that those verbs now return a "not supported in chat; use CLI / Web UI" message.

### Tests that should stay green unchanged

- All `tests/test_mcp_*.py`
- All `tests/security/*.py`
- All `tests/test_chat_*.py`

## Success Criteria

1. `CONNECTORS` dict is removed from `cli/connector_cmd.py`.
2. `MCPRecipe` has `kind: Literal["channel", "mcp"]` field; `RECIPES["telegram"].kind == "channel"`.
3. `mycelos connector setup telegram` and `mycelos connector setup github` both work.
4. `mycelos connector list` shows three sections: Installed / Channels / MCP Connectors.
5. Web UI `/connectors` page matches the CLI structure.
6. `/connector` slash command supports only read-only verbs (`list`, `help`). Setup verbs return a pointer to CLI / Web UI.
7. Autocomplete (`completer.SLASH_COMMANDS`) reflects the reduced verb set.
8. Full test baseline passes (zero failures, zero regressions).
9. `CHANGELOG.md` updated.

## Non-Goals (deferred to follow-up specs)

**Deferred to Spec 1.5 (`ui.open_page` tool + deep-link catalog):**

- A Chat-agent tool that renders a clickable "open in admin page" link.
- System prompt additions that direct the agent to use deep links for all admin tasks.
- Frontend anchor handling on target pages (`/connectors#gmail`, `/settings/models`, etc.).

**Deferred to Spec 2 (Capability Hybrid):**

- Runtime discovery of capabilities for ad-hoc user-registered MCP servers.
- `discovered_capabilities` DB column.
- `/connector refresh-caps <name>` command.
- Boot-time discovery for unknown MCPs.
