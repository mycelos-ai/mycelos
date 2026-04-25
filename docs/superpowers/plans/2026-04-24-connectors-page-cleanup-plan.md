# Connectors Page Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Connectors page understandable on first look — Installed at top, Available below, no duplicate or stale entries.

**Architecture:** Three small changes: (a) drop the `mcp-memory` recipe; (b) ship a one-shot `scripts/cleanup_orphan_connectors.py` for the live `~/.mycelos/mycelos.db`; (c) reorder the Connectors page so the existing "Installed" card grid (today below) moves above the two "Available" sections, and the Available sections filter out anything already installed. No backend endpoint changes — both `/api/connectors/recipes` and `/api/connectors` already exist.

**Tech Stack:** Python 3.12+, SQLite, Alpine.js (vanilla, no build step), pytest.

**Spec:** `docs/superpowers/specs/2026-04-24-connectors-page-cleanup-design.md`

**Baseline rule:** After every task, `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q` must pass with zero failures.

---

## File Structure

Files this plan touches:

- `src/mycelos/connectors/mcp_recipes.py` — drop the `mcp-memory` entry from `RECIPES`.
- `src/mycelos/frontend/pages/connectors.html` — move existing Installed grid above the Available sections, add empty-state, filter Available against installed ids, drop the `mcp-memory` entry from `recipeIcons`.
- `scripts/cleanup_orphan_connectors.py` — NEW: one-shot DB cleanup CLI.
- `tests/test_cleanup_orphan_connectors.py` — NEW: covers dry-run + apply against an in-memory App.
- `tests/test_recipe_kind.py` — should still pass without changes (verify).
- `CHANGELOG.md` — Week 17 entry at the end.

What stays untouched:

- `/api/connectors` (`gateway/routes.py:1953`) — the installed-list endpoint already works.
- `/api/connectors/recipes` (`gateway/routes.py:1677`) — already returns `{channels, mcp}` after Spec 1.
- All Spec 1 tests, slash-command tests, CSRF tests.

---

## Task 1: Drop the `mcp-memory` recipe

**Files:**
- Modify: `src/mycelos/connectors/mcp_recipes.py`
- Test: `tests/test_recipe_kind.py` (no change expected — verify)

This is a tiny, surgical removal. Do it first so later tasks see the correct recipe set.

- [ ] **Step 1: Write the failing assertion**

Add a new test to `tests/test_recipe_kind.py` (append at the end of the file). The test pins the absence of `mcp-memory` so it can't sneak back via merge:

```python
def test_mcp_memory_recipe_is_gone() -> None:
    """mcp-memory was removed — Mycelos's own Knowledge Base owns this concept."""
    from mycelos.connectors.mcp_recipes import RECIPES
    assert "mcp-memory" not in RECIPES
```

- [ ] **Step 2: Run the test, confirm it fails**

```
PYTHONPATH=src pytest tests/test_recipe_kind.py::test_mcp_memory_recipe_is_gone -v
```

Expected: FAIL — `mcp-memory` is still in `RECIPES`.

- [ ] **Step 3: Delete the `mcp-memory` block from `RECIPES`**

In `src/mycelos/connectors/mcp_recipes.py`, find the entry that begins with `"mcp-memory": MCPRecipe(`. The block is approximately:

```python
    "mcp-memory": MCPRecipe(
        id="mcp-memory",
        name="Memory (Knowledge Graph)",
        description="Persistent memory via knowledge graph — entities, relations, observations",
        command="npx -y @modelcontextprotocol/server-memory",
        transport="stdio",
        credentials=[],
        capabilities_preview=["memory.entities", "memory.relations", "memory.search"],
        category="tools",
        requires_node=True,
    ),
```

Delete the whole block (the dict entry and its trailing comma). Leave every surrounding entry intact.

- [ ] **Step 4: Drop the matching frontend icon**

In `src/mycelos/frontend/pages/connectors.html`, find:

```javascript
'mcp-memory': '\uD83E\uDDE0',
```

(around line 987 in the `recipeIcons` map). Delete that single line. Don't touch any other icon entry.

- [ ] **Step 5: Run the new test + the full kind test**

```
PYTHONPATH=src pytest tests/test_recipe_kind.py -v
```

Expected: 5 passing (4 original + the new `test_mcp_memory_recipe_is_gone`).

- [ ] **Step 6: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures. The Hypothesis flake in `tests/test_policy_engine_property.py` may appear under load — re-run that file alone if so; passes in isolation = known flake, not a regression.

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/connectors/mcp_recipes.py src/mycelos/frontend/pages/connectors.html tests/test_recipe_kind.py
git commit -m "feat(connectors): drop mcp-memory recipe (overlaps with Mycelos Knowledge Base)"
```

---

## Task 2: One-shot orphan-cleanup script

**Files:**
- Create: `scripts/cleanup_orphan_connectors.py`
- Test: `tests/test_cleanup_orphan_connectors.py`

This is a CLI tool that walks `connector_registry`, finds rows whose id is no longer in `RECIPES`, and deletes them along with their capabilities + credentials. `--dry-run` is the default; `--apply` does the deletion and writes a backup first.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cleanup_orphan_connectors.py`:

```python
"""scripts/cleanup_orphan_connectors.py — dry-run + apply behaviors."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "cleanup_orphan_connectors.py"


def _seed_db(data_dir: Path) -> None:
    """Initialize an App so all tables exist, then inject one orphan row."""
    from mycelos.app import App
    os.environ["MYCELOS_MASTER_KEY"] = "cleanup-test-key"
    app = App(data_dir)
    app.initialize()
    # Inject a row whose id is NOT in RECIPES — this is the orphan.
    app.connector_registry.register(
        connector_id="web-search-duckduckgo",
        name="DuckDuckGo (legacy)",
        connector_type="search",
        capabilities=["search.web", "search.news"],
        description="legacy entry from before the registry unification",
        setup_type="none",
    )
    # Plus a real recipe-backed row that must SURVIVE the cleanup.
    app.connector_registry.register(
        connector_id="fetch",
        name="HTTP Fetch",
        connector_type="mcp",
        capabilities=["fetch"],
        description="real recipe — keep me",
        setup_type="none",
    )


def test_dry_run_lists_orphans_and_changes_nothing(tmp_data_dir: Path) -> None:
    """Default --dry-run should print the orphan and not mutate the DB."""
    _seed_db(tmp_data_dir)
    db_path = tmp_data_dir / "mycelos.db"
    before = _count_connectors(db_path)
    assert "web-search-duckduckgo" in before
    assert "fetch" in before

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--data-dir", str(tmp_data_dir)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert result.returncode == 0, result.stderr
    assert "web-search-duckduckgo" in result.stdout
    assert "DRY RUN" in result.stdout.upper() or "would" in result.stdout.lower()

    after = _count_connectors(db_path)
    assert after == before, "dry-run must not mutate the DB"


def test_apply_removes_orphans_and_keeps_real_recipes(tmp_data_dir: Path) -> None:
    """--apply deletes the orphan, keeps the real recipe, writes a backup."""
    _seed_db(tmp_data_dir)
    db_path = tmp_data_dir / "mycelos.db"

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--data-dir", str(tmp_data_dir), "--apply"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert result.returncode == 0, result.stderr

    after = _count_connectors(db_path)
    assert "web-search-duckduckgo" not in after
    assert "fetch" in after, "real recipe-backed row must survive cleanup"

    # Backup file must exist alongside the DB.
    backups = list(tmp_data_dir.glob("mycelos.db.bak-*"))
    assert backups, f"no backup file created in {tmp_data_dir}"

    # Audit log must record the removal.
    from mycelos.app import App
    app = App(tmp_data_dir)
    events = app.audit.query(event_type="connector.orphan_removed", limit=10)
    assert events, "orphan removal must emit a connector.orphan_removed audit event"
    assert any(e.get("details", {}).get("id") == "web-search-duckduckgo" for e in events)


def test_apply_is_idempotent(tmp_data_dir: Path) -> None:
    """Running --apply twice does nothing extra on the second run."""
    _seed_db(tmp_data_dir)
    subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--data-dir", str(tmp_data_dir), "--apply"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        check=True,
    )
    second = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--data-dir", str(tmp_data_dir), "--apply"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert second.returncode == 0, second.stderr
    assert "0 orphans" in second.stdout.lower() or "no orphans" in second.stdout.lower()


def test_missing_db_exits_nonzero(tmp_path: Path) -> None:
    """Pointing the script at a directory without mycelos.db must fail clean."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--data-dir", str(tmp_path)],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert result.returncode != 0
    assert "mycelos.db" in (result.stderr + result.stdout).lower()


def _count_connectors(db_path: Path) -> set[str]:
    """Direct read — bypass App so we test the script's effect, not the API."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT id FROM connectors").fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()
```

Note: `tmp_data_dir` is the project's existing pytest fixture (resolved from `tests/conftest.py`). It already creates an isolated temp directory per test.

- [ ] **Step 2: Run the tests, confirm they fail**

```
PYTHONPATH=src pytest tests/test_cleanup_orphan_connectors.py -v
```

Expected: all 4 fail because `scripts/cleanup_orphan_connectors.py` doesn't exist yet.

- [ ] **Step 3: Implement the script**

Create `scripts/cleanup_orphan_connectors.py` with this exact content:

```python
#!/usr/bin/env python3
"""Remove orphan connector_registry rows whose id is no longer in RECIPES.

One-shot maintenance tool. Default mode is --dry-run; pass --apply to
actually delete. --apply backs up mycelos.db before writing.

Usage:
    python scripts/cleanup_orphan_connectors.py --data-dir ~/.mycelos
    python scripts/cleanup_orphan_connectors.py --data-dir ~/.mycelos --apply
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _resolve_data_dir(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _backup_db(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    shutil.copy2(db_path, backup)
    return backup


def _find_orphans(app) -> list[dict]:
    from mycelos.connectors.mcp_recipes import RECIPES
    rows = app.connector_registry.list_connectors()
    return [r for r in rows if r["id"] not in RECIPES]


def _remove_orphan(app, orphan: dict) -> None:
    """Delete one orphan: capabilities, credentials, the row itself.
    Single transaction per orphan so partial failure is per-orphan."""
    cid = orphan["id"]
    storage = app.storage
    storage.execute(
        "DELETE FROM connector_capabilities WHERE connector_id = ?", (cid,)
    )
    # Credentials may live under bare id or "connector:<id>" (legacy).
    storage.execute(
        "DELETE FROM credentials WHERE service IN (?, ?)",
        (cid, f"connector:{cid}"),
    )
    storage.execute("DELETE FROM connectors WHERE id = ?", (cid,))
    app.audit.log(
        "connector.orphan_removed",
        details={"id": cid, "name": orphan.get("name") or cid},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Remove orphan connector_registry rows."
    )
    parser.add_argument(
        "--data-dir",
        default="~/.mycelos",
        help="Mycelos data directory (default: ~/.mycelos)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete (default is --dry-run, prints only).",
    )
    args = parser.parse_args(argv)

    data_dir = _resolve_data_dir(args.data_dir)
    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        print(f"error: mycelos.db not found at {db_path}", file=sys.stderr)
        return 1

    if not os.environ.get("MYCELOS_MASTER_KEY"):
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()

    from mycelos.app import App
    app = App(data_dir)

    orphans = _find_orphans(app)
    if not orphans:
        print("No orphans found. 0 orphans removed.")
        return 0

    if not args.apply:
        print("DRY RUN. Would remove:")
        for o in orphans:
            caps = len(o.get("capabilities") or [])
            print(
                f"  - {o['id']} ({o.get('name') or '?'}, "
                f"type={o.get('connector_type') or '?'}, {caps} capabilities)"
            )
        print(f"\n{len(orphans)} orphan(s) would be removed. "
              f"Re-run with --apply to delete.")
        return 0

    backup = _backup_db(db_path)
    print(f"Backup: {backup}")
    for o in orphans:
        _remove_orphan(app, o)
        caps = len(o.get("capabilities") or [])
        print(f"Removed {o['id']} ({caps} capabilities).")

    app.config.apply_from_state(
        state_manager=app.state_manager,
        description=f"Removed {len(orphans)} orphan connector(s)",
        trigger="orphan_cleanup",
    )

    names = ", ".join(o["id"] for o in orphans)
    print(f"\n{len(orphans)} orphan(s) removed: {names}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Make it executable**

```
chmod +x scripts/cleanup_orphan_connectors.py
```

- [ ] **Step 5: Run the new tests**

```
PYTHONPATH=src pytest tests/test_cleanup_orphan_connectors.py -v
```

Expected: 4 passing.

- [ ] **Step 6: Manual dry-run smoke test (no apply)**

```
PYTHONPATH=src python3 scripts/cleanup_orphan_connectors.py --data-dir ~/.mycelos
```

Expected: prints "DRY RUN" and lists Stefan's orphan rows (likely `web-search-duckduckgo`, `http`, possibly an old `brave-search` variant). Exit 0. **Do not** run `--apply` here — that's a separate manual step Stefan controls.

- [ ] **Step 7: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 8: Commit**

```bash
git add scripts/cleanup_orphan_connectors.py tests/test_cleanup_orphan_connectors.py
git commit -m "feat(scripts): one-shot connector orphan cleanup (dry-run + apply)"
```

---

## Task 3: Move the Installed grid above the Available sections

**Files:**
- Modify: `src/mycelos/frontend/pages/connectors.html`

This is purely HTML rearrangement. Both grids exist today; the Installed grid (line ~603) just lives below the two Available sections (lines ~143-222). Move it above and add a clean section header.

- [ ] **Step 1: Add a section header above the existing Installed grid markup**

In `src/mycelos/frontend/pages/connectors.html`, find the Installed grid block — it starts around line 603 and contains `<template x-for="connector in connectors" :key="connector.id || connector.name">`. The block extends to its closing `</div>` (the matching one of the outer `<div x-show="!loading && connectors.length > 0">`).

Identify the full Installed block precisely:

1. Loading state at line ~582 (`<div x-show="loading" ...>`).
2. Empty state at line ~588 (`<div x-show="!loading && connectors.length === 0">`).
3. Card grid at line ~603 (`<div x-show="!loading && connectors.length > 0">`).

These three sit together. Wrap them with a dedicated section header so the page reads "Installed", just like the Channels and MCP sections already do. New wrapper, inserted directly above the loading state:

```html
<!-- Installed connectors — connector_registry rows -->
<section class="installed-connectors mb-8">
  <div class="flex items-center gap-2 mb-4">
    <span class="material-symbols-outlined text-primary text-sm">check_circle</span>
    <h3 class="section-title text-[10px] font-label uppercase tracking-widest text-primary font-bold">Installed</h3>
    <div class="h-[1px] flex-1 mycelium-line"></div>
  </div>

  <!-- existing Loading / Empty-state / Card-grid markup goes here, unchanged -->

</section>
```

So the final structure is `<section class="installed-connectors">` containing the existing three blocks (loading, empty, grid) verbatim. Don't change the inner markup of those blocks.

- [ ] **Step 2: Move the wrapped Installed section above the Available sections**

In the same file, the page structure today is:

```
<section ... pt-20 pb-12 px-8>           ← outer page container
  <div ... max-w-5xl ...>
    <!-- Page header -->
    <!-- Available connectors (Channels + MCP) -->     ← currently here
    <!-- Setup wizard -->
    <!-- Loading + Empty + Installed grid -->          ← currently here, way below
  </div>
</section>
```

Cut the new `<section class="installed-connectors">` block (the wrapper from Step 1, with all three inner blocks inside it) and paste it BETWEEN the page header and the Available section. The new structure must read:

```
<!-- Page header -->
<!-- Installed (NEW position) -->
<!-- Available connectors (Channels + MCP) -->
<!-- Setup wizard -->
```

After the move, verify no orphan markup remains where the Installed grid used to be (no leftover closing tags).

- [ ] **Step 3: Hide the Installed section while loading**

The wrapper section should not flash an empty header on initial load. Add `x-show="!loading || connectors.length > 0"` to the section so it stays hidden until the first fetch completes. Since the inner loading state already handles "still loading" appearance, the simpler rule is: if loading and we have no prior data, show the loading spinner; otherwise show the empty-or-grid markup. The existing `x-show` flags on the inner blocks already do this — no extra Alpine state needed.

Concretely: leave the inner three `x-show` flags exactly as they are. Just keep the outer `<section>` always rendered (no `x-show` on it). This avoids a layout-shift flicker when loading completes.

- [ ] **Step 4: Manual smoke test**

Reload the page in your browser:

```
http://localhost:9100/pages/connectors.html
```

Expected:
- "Installed" header at top, then the existing card grid (or its empty-state).
- Below: "Channels" header (Telegram tile), then "MCP Connectors" (the recipes).

If the gateway isn't running for you, start it: `mycelos serve --data-dir ~/.mycelos &`. Stefan's running gateway will pick up frontend changes on reload (no restart needed for HTML).

- [ ] **Step 5: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures (frontend-only change; Python tests untouched).

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/frontend/pages/connectors.html
git commit -m "feat(web): move Installed section above Available on Connectors page"
```

---

## Task 4: Filter Installed recipes out of the Available sections

**Files:**
- Modify: `src/mycelos/frontend/pages/connectors.html`

After Task 3, an installed recipe like Brave Search appears in Installed AND in MCP Connectors. Filter the Available lists so each recipe shows up exactly once.

- [ ] **Step 1: Locate the existing `recipeChannels` / `recipeMcp` data binding**

In `src/mycelos/frontend/pages/connectors.html`, the Alpine state (a `connectorsApp()` factory or similar) holds:

```javascript
recipeChannels: [],
recipeMcp: [],
```

These are populated in `loadRecipes()` from `data.channels` / `data.mcp`. They're consumed directly by `<template x-for="recipe in recipeChannels">` and `<template x-for="recipe in recipeMcp">`.

- [ ] **Step 2: Rename the storage props and add computed getters**

Change the data props to hold the unfiltered lists:

```javascript
allChannels: [],
allMcp: [],
```

Update `loadRecipes()` to populate the renamed props:

```javascript
this.allChannels = Array.isArray(data.channels) ? data.channels : [];
this.allMcp = Array.isArray(data.mcp) ? data.mcp : [];
```

Add two getters that compute the filtered lists by removing anything already in `this.connectors`:

```javascript
get installedIds() {
  return new Set(this.connectors.map(c => c.id || c.name));
},
get recipeChannels() {
  return this.allChannels.filter(r => !this.installedIds.has(r.id));
},
get recipeMcp() {
  return this.allMcp.filter(r => !this.installedIds.has(r.id));
},
```

Place these near the other getters / computed properties in the Alpine factory (search for `recipeIcon(` — they go in the same object literal).

The existing `<template x-for="recipe in recipeChannels">` and `<template x-for="recipe in recipeMcp">` markup keeps working unchanged because Alpine resolves getters transparently.

- [ ] **Step 3: Drop the now-redundant "installed" UI on Available cards**

Since Available cards never contain installed recipes any more, the duplicate-state UI on those cards is dead code. In each Available card template (Channels at ~line 154 and MCP at ~line 186), remove:

- The `:class="isConnectorInstalled(recipe.id) && 'installed'"` binding.
- The `<span x-show="isConnectorInstalled(recipe.id)" ...>active</span>` and the matching `installed` badge span.
- The `:disabled="isConnectorInstalled(recipe.id)"` flag on the action button.
- The `:class="isConnectorInstalled(recipe.id) ? '...' : '...'"` ternary on the action button — keep only the not-installed branch.
- The MCP card's `@click="isConnectorInstalled(recipe.id) ? jumpToInstalled(recipe.id) : setupRecipe(recipe)"` simplifies to `@click="setupRecipe(recipe)"`. Same for the `:title`, `<span x-text>` icon and label — keep only the not-installed text/icon.

This deletes ~30 lines of conditional branches that can never fire any more. The `isConnectorInstalled()` helper itself stays — `jumpToInstalled()` and other code paths may use it (verify with `grep -n isConnectorInstalled src/mycelos/frontend/pages/connectors.html` — keep the function if any caller remains; only remove if grep shows zero callers after this step).

- [ ] **Step 4: Add the empty-state line for "no connectors yet"**

Inside the Installed section wrapper (Task 3), the existing empty-state at line ~588 already shows a friendly "No connectors yet" panel with an "Add your first connector" button. That's good — leave it.

But add a one-line subtitle hint to the page header so users understand the new layout. Find:

```html
<p class="text-on-surface-variant text-sm">Channels and MCP connectors</p>
```

Replace with:

```html
<p class="text-on-surface-variant text-sm">Your installed connectors plus what's available to add.</p>
```

- [ ] **Step 5: Manual smoke test**

Reload `http://localhost:9100/pages/connectors.html`.

Expected:
- Brave Search (or whichever connectors Stefan has installed) appears ONLY in Installed at top, NOT in MCP Connectors below.
- Telegram appears in either Installed (if installed) or Channels (if not), never both.
- Card buttons in Available say "Set up" / "Add" / "Connect" — never "Configured" / "View".

- [ ] **Step 6: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/frontend/pages/connectors.html
git commit -m "feat(web): filter installed recipes out of Available sections (no duplicates)"
```

---

## Task 5: CHANGELOG entry + final verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add CHANGELOG entry under Week 17**

Open `CHANGELOG.md`. The Week 17 section is near the top, between the v0.3.0 release notes and Week 16. Find the existing Week 17 entries and add at the END of the Week 17 block (before `## Week 16 (2026)`):

```markdown
### Connectors page cleanup
- Connectors page now leads with an "Installed" section. The two
  "Available" sections (Channels, MCP Connectors) sit below and never
  show a recipe that's already installed. No more duplicate cards or
  confusing "Configured" buttons in the catalog.
- The page subtitle now reads "Your installed connectors plus what's
  available to add."
- `mcp-memory` recipe (upstream `@modelcontextprotocol/server-memory`,
  generic knowledge graph) was removed — it overlaps with Mycelos's
  own Knowledge Base and offering both confused users about where data
  lands.
- New one-shot maintenance script:
  `scripts/cleanup_orphan_connectors.py --data-dir ~/.mycelos`. Default
  mode is `--dry-run`; `--apply` deletes orphan `connector_registry`
  rows (ids no longer in `RECIPES`) plus their capabilities and
  credentials, and writes a timestamped `mycelos.db.bak-*` first. Used
  to clear leftover `web-search-duckduckgo` / `http` rows that Spec 1
  surfaced but didn't migrate.
```

- [ ] **Step 2: Full baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 3: Manual end-to-end check**

Visually confirm in the browser at `http://localhost:9100/pages/connectors.html`:

- "Installed" header at top
- Brave Search / Gmail / HTTP Fetch (or whatever Stefan has) appear only there
- "Channels" header below — Telegram tile if not installed
- "MCP Connectors" header below that — no `mcp-memory` tile, no Brave duplicate
- "Add Connector" button still scrolls to the Available area when clicked

- [ ] **Step 4: Commit changelog**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): connectors page cleanup (Week 17)"
```

- [ ] **Step 5: Push**

```bash
git push origin main
```

- [ ] **Step 6: Stefan runs the cleanup script (controller offers, Stefan approves)**

After push, the controller (you, the orchestrator) prompts Stefan to authorize a manual run of:

```bash
PYTHONPATH=src python3 scripts/cleanup_orphan_connectors.py --data-dir ~/.mycelos
```

(dry-run first). On Stefan's "go", the controller runs `--apply` against `~/.mycelos`. The script writes a backup before deleting. Stefan reloads the browser to confirm the orphan cards are gone.

---

## Self-review notes

Spec coverage check (against `2026-04-24-connectors-page-cleanup-design.md`):

- D1 (drop `mcp-memory`) → Task 1.
- D2 (one-shot DB script with --dry-run/--apply, backup, audit, config-gen) → Task 2.
- D3 (Installed at top, Available below, no duplicates) → Tasks 3 + 4.
- D4 (no backend changes) → respected; both endpoints already exist.
- Success criterion 7 (CHANGELOG under Week 17) → Task 5.
- Non-goal: Custom MCP setup → not in this plan, deferred.
- Non-goal: auto-cleanup at boot → not in this plan, deferred.

Type/name consistency:

- `installedIds` getter (Task 4) returns a `Set` — both later filter calls use `Set.has()`. Consistent.
- `allChannels` / `allMcp` (Task 4) match what Task 4 names them; the existing `recipeChannels` / `recipeMcp` getters keep the same names so `<template x-for>` markup doesn't change.
- `connector_registry.list_connectors()` returns rows with an `id` key — Task 2 script and Task 4 getter both use `c["id"]` / `c.id || c.name` consistently with the existing frontend pattern.
- Script field names (`connector_type`, `capabilities`) match the registry's existing `register()` signature in `src/mycelos/connectors/connector_registry.py`.

No placeholders remain. Every step shows the actual code or command.
