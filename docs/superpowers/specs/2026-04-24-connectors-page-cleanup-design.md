# Connectors Page Cleanup — Design

**Date:** 2026-04-24
**Status:** Draft
**Scope:** Mini-refactor following Spec 1 (`2026-04-24-connector-registry-unification-design.md`). Removes UI/UX rough edges visible after Spec 1 shipped:

- `mcp-memory` recipe (overlaps with Mycelos's own Knowledge Base)
- Stale orphan rows in `connector_registry` from the pre-Spec-1 setup path (`web-search-duckduckgo`, `http`, possibly an old `brave-search` row)
- Confusing card order on the Connectors page: installed connectors render mixed into the "Available" sections instead of in a dedicated "Installed" section at the top
- Same recipe rendering twice (once as installed, once as available) — single biggest source of user confusion

A separate spec (Custom MCP Connector setup) follows once this is shipped.

## Goal

Make the Connectors page understandable on first look: see what you have, then see what you can add, with no duplicate or stale entries.

## Decisions

### D1: Remove `mcp-memory` recipe

`mcp-memory` is the upstream MCP-server-memory package — a generic knowledge graph. Mycelos already ships its own Knowledge Base (notes + Memory Service with four scopes). Offering both confuses the user about where data goes. Remove the recipe.

### D2: One-shot DB cleanup script

A single Python script (`scripts/cleanup_orphan_connectors.py`) walks `connector_registry` rows, finds IDs that no longer exist in `RECIPES`, and deletes them along with `connector_capabilities` rows and any `credentials` rows under the orphan id.

- Default mode is `--dry-run` (print, change nothing).
- `--apply` does the deletion. Before deletion, the script copies `mycelos.db` to `mycelos.db.bak-YYYYMMDD-HHMMSS`.
- Every removal emits an audit event `connector.orphan_removed`.
- After successful deletion, the script calls `app.config.apply_from_state(trigger="orphan_cleanup")` so the cleanup is captured in the next config generation (Constitution Rule 2).
- The script lives in `scripts/` (alongside `install.sh` etc.) and is intended as a one-shot tool. Stefan runs it once against his `~/.mycelos`. After the run we leave the script in tree as documentation of what was cleaned and so future similar cleanups are quicker — there's no per-boot auto-cleanup hook (Stefan's request: "do it once manually, then it's done").

### D3: Web UI — Installed at top, Available below, no duplicates

The Connectors page renders three vertical sections:

1. **Installed** — connectors with a `connector_registry` row, regardless of kind (channel or mcp). One card per connector. If empty, an empty-state line ("No connectors yet — pick one below to get started.") replaces the section.
2. **Available — Channels** — recipes with `kind="channel"` whose id is NOT in the installed list.
3. **Available — MCP Connectors** — recipes with `kind="mcp"` whose id is NOT in the installed list.

A recipe never appears in both Installed and Available. The Installed card has its own action button (View / Manage / Remove); the Available card has Setup / Add. Section headers stay; sections with zero entries are hidden via `x-show`.

The "Add Connector" button in the page header keeps its current behavior (scrolls to Available) — the future Custom-MCP dialog will hook into this same button.

### D4: Backend stays untouched

No new gateway endpoints. The page already has access to:
- `/api/connectors/recipes` — recipe catalog (returns `{channels, mcp}`)
- `/api/connectors` (or equivalent) — list of installed connectors

If both endpoints already exist, the frontend just needs to consume them in the new order. If only one exists, we use whatever the page already calls — the audit/Spec 1 work confirmed `app.connector_registry.list_connectors()` is exposed via the gateway. The plan task verifies which exact endpoint the frontend uses today.

## Architecture

```
┌────────────────────────────────────────────────────┐
│ src/mycelos/connectors/mcp_recipes.py              │
│   RECIPES["mcp-memory"] removed                    │
└────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────┐
│ scripts/cleanup_orphan_connectors.py (new)         │
│   --dry-run / --apply                              │
│   Removes orphan connectors + caps + credentials   │
│   Backs up DB before --apply                       │
└────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────┐
│ src/mycelos/frontend/pages/connectors.html         │
│   New "Installed" section at top                   │
│   Empty-state when nothing installed               │
│   Available sections filter out installed ids      │
└────────────────────────────────────────────────────┘
```

## Components

### `src/mycelos/connectors/mcp_recipes.py`

Delete the `"mcp-memory": MCPRecipe(...)` block from the `RECIPES` dict. No other change.

### `scripts/cleanup_orphan_connectors.py` (new)

CLI tool with `argparse`. Options:

- `--data-dir PATH` (default: `~/.mycelos`) — same convention as the main `mycelos` CLI.
- `--dry-run` (default: True if neither flag given) — print what would be deleted, exit 0.
- `--apply` — perform deletion.

Behavior:

1. Resolve `data_dir / "mycelos.db"`. Fail with clear error if missing.
2. Open the App context (so audit + config_engine work).
3. `installed_ids = {r["id"] for r in app.connector_registry.list_connectors()}`
4. `recipe_ids = set(RECIPES.keys())`
5. `orphans = installed_ids - recipe_ids`
6. If `--dry-run`: print one line per orphan with the recipe-row metadata (name, type, capability count). Exit 0.
7. If `--apply`:
   - Copy `mycelos.db` → `mycelos.db.bak-YYYYMMDD-HHMMSS` (use `shutil.copy2`).
   - For each orphan id, in a single transaction per orphan:
     - `DELETE FROM connector_capabilities WHERE connector_id = ?`
     - `DELETE FROM credentials WHERE service IN (?, ?)` (orphan id, `"connector:" + id`)
     - `DELETE FROM connectors WHERE id = ?`
     - `audit.log("connector.orphan_removed", details={"id": id, "name": ...})`
   - After the loop: `app.config.apply_from_state(state_manager=app.state_manager, description=f"Removed {N} orphan connectors", trigger="orphan_cleanup")`
   - Print summary ("Removed 3 orphan connectors: web-search-duckduckgo, http, brave-search. Backup: mycelos.db.bak-...")

The script lives at `scripts/cleanup_orphan_connectors.py`. It's executable (`chmod +x`) and starts with `#!/usr/bin/env python3` plus `from __future__ import annotations`. It imports from `mycelos.*` like the rest of the codebase.

### `src/mycelos/frontend/pages/connectors.html`

Add a new section at the top of the available-connectors area (replacing the current immediate jump to Channels):

```html
<section class="installed-connectors mb-8" x-show="!loading">
  <div class="flex items-center gap-2 mb-3">
    <span class="material-symbols-outlined text-primary text-base">check_circle</span>
    <h3 class="section-title text-[10px] font-label uppercase tracking-widest text-primary font-bold">
      Installed
    </h3>
  </div>
  <template x-if="installedConnectors.length === 0">
    <div class="text-sm text-on-surface-variant italic">
      No connectors yet — pick one below to get started.
    </div>
  </template>
  <div class="recipe-grid grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3"
       x-show="installedConnectors.length > 0">
    <template x-for="connector in installedConnectors" :key="connector.id">
      <!-- Installed card markup — uses the same .usecase-tile structure as
           today's installed-tagged Available cards, plus the "installed"
           green badge. Action button label/handler depends on the connector
           kind: channel → "Manage" (opens existing manage flow if any, else
           the matching setup wizard); mcp → "View" (opens the existing
           view-tools flow). Remove button stays as today. -->
    </template>
  </div>
</section>
```

The two existing Available sections stay in place but their `x-for` is filtered against `installedIds`:

```javascript
get recipeChannels() {
  const installed = new Set(this.installedConnectors.map(c => c.id));
  return this.allChannels.filter(r => !installed.has(r.id));
}
get recipeMcp() {
  const installed = new Set(this.installedConnectors.map(c => c.id));
  return this.allMcp.filter(r => !installed.has(r.id));
}
```

Where `allChannels` / `allMcp` are the unfiltered lists fetched from `/api/connectors/recipes`. Today's Alpine state likely uses `recipeChannels` / `recipeMcp` directly — rename to `allChannels` / `allMcp` and add the computed getters.

`installedConnectors` is fetched from whichever endpoint the page already uses for the installed list (verify in plan). If no such endpoint is wired into this page yet, add a `fetch('/api/connectors')` call alongside the existing recipe fetch.

## Data Flow

**Cleanup script (one-shot):**

```
$ python scripts/cleanup_orphan_connectors.py --data-dir ~/.mycelos
  → DRY RUN. Would remove:
    - web-search-duckduckgo (DuckDuckGo, type=search, 2 caps)
    - http (HTTP, type=web, 2 caps)
  Run with --apply to delete.

$ python scripts/cleanup_orphan_connectors.py --data-dir ~/.mycelos --apply
  → Backup: ~/.mycelos/mycelos.db.bak-20260424-153012
  → Removed web-search-duckduckgo (2 capabilities, 0 credentials).
  → Removed http (2 capabilities, 0 credentials).
  → Config generation 47 written (trigger=orphan_cleanup).
  → Done. 2 orphans removed.
```

**Connectors page render:**

```
Page load
  ↓
  Promise.all([
    fetch('/api/connectors/recipes'),  // {channels, mcp}
    fetch('/api/connectors'),           // installed list
  ])
  ↓
  Alpine state:
    allChannels = data[0].channels
    allMcp      = data[0].mcp
    installedConnectors = data[1]
  ↓
  Render order (top to bottom):
    1. Installed section (or empty-state if installedConnectors.length == 0)
    2. Channels section (filtered to recipes NOT in installedIds; hidden if empty)
    3. MCP Connectors section (same filter; hidden if empty)
```

## Error Handling

- Cleanup script `--apply` fails mid-loop → backup file is still on disk; user can `cp` it back. Each orphan is one transaction so partial state is per-orphan, not per-row.
- Frontend `/api/connectors` fails → show error toast, render Available sections unfiltered (degraded mode). Don't block the whole page.
- Frontend `/api/connectors/recipes` fails → show error toast, hide Available sections. Installed section still renders if its fetch succeeded.

## Testing

- `tests/test_recipe_kind.py` — must still pass (mcp-memory removal doesn't change `kind` invariants for remaining recipes).
- New: `tests/test_cleanup_orphan_connectors.py` — covers dry-run output (no DB mutation) and apply behavior (orphans deleted, real recipes untouched, backup file created).
- Existing baseline must stay green throughout.
- Web UI: manual browser verification after implementation. Stefan inspects the page and confirms (a) Installed at top, (b) no duplicates, (c) empty-state works when nothing is installed.

## Success Criteria

1. `mcp-memory` no longer in `RECIPES`. `tests/test_recipe_kind.py` still green.
2. `scripts/cleanup_orphan_connectors.py` exists, supports `--dry-run` (default) and `--apply`.
3. Running `--apply` against a DB with orphan rows: deletes them, creates a `.db.bak-*` backup, emits audit events, creates a config generation.
4. Connectors page renders Installed section at top, Available below, no recipe appears twice.
5. Empty-state line appears when nothing is installed.
6. Full test baseline stays green.
7. CHANGELOG entry under Week 17.

## Non-Goals

- Custom MCP connector setup form — separate spec.
- Capability hybrid / runtime discovery — Spec 2.
- Backend endpoint changes — none required.
- Auto-cleanup at boot — explicitly rejected by Stefan ("do it once manually").
