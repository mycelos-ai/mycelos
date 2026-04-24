# Connector Registry Unification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify the two parallel connector registries, introduce an explicit `kind` field on `MCPRecipe`, remove chat-side setup verbs, and render Channels vs. MCP Connectors as distinct sections in CLI and Web UI.

**Architecture:** Single source of truth is `RECIPES` in `src/mycelos/connectors/mcp_recipes.py`. `MCPRecipe` gains `kind: Literal["channel", "mcp"]` (default `"mcp"`). `CONNECTORS` dict in `src/mycelos/cli/connector_cmd.py` is removed. CLI setup routes to `_setup_channel` or `_setup_mcp` based on `recipe.kind`. Chat `/connector` keeps only read verbs.

**Tech Stack:** Python 3.12+, Click, Rich, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-04-24-connector-registry-unification-design.md`

**Baseline rule:** After every task, `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q` must pass with zero failures. No commits to main if tests are red.

---

## File Structure

Files touched in this plan:

- `src/mycelos/connectors/mcp_recipes.py` — add `kind` field, set Telegram to `kind="channel"`.
- `src/mycelos/cli/connector_cmd.py` — remove `CONNECTORS` dict; split `_setup_connector` into `_setup_mcp` + `_setup_channel`; rewrite `list_cmd` to render three sections; rewrite `_show_connector_menu` to iterate `RECIPES`.
- `src/mycelos/chat/slash_commands.py` — remove `add`/`setup`/`remove`/`test` verbs from `/connector`; rewrite help text.
- `src/mycelos/cli/completer.py` — update `SLASH_COMMANDS` so autocomplete matches the reduced verb set.
- `src/mycelos/gateway/routes.py` — update `/api/connectors/recipes` to return `{channels: [...], mcp: [...]}`.
- `src/mycelos/frontend/pages/connectors.html` — render two sections (Channels + MCP Connectors).
- `tests/test_connector_setup.py` — rewrite `CONNECTORS`-based tests to recipe-based.
- `tests/test_telegram_channel.py` — switch from `CONNECTORS["telegram"]` to `get_recipe("telegram")`.
- `tests/test_slash_commands.py` / chat tests — update for removed verbs.
- `tests/test_recipe_kind.py` — NEW: schema check for `kind` field.
- `tests/test_connector_list_two_sections.py` — NEW: verify CLI list output structure.
- `tests/test_frontend_connectors_api.py` — NEW: verify the split API response.
- `CHANGELOG.md` — final entry.

Code that stays unchanged:
- `src/mycelos/connectors/connector_registry.py` — the `register(..., connector_type=...)` call stores `recipe.kind` as the `connector_type`. No schema change needed; rows already accept any string.
- `src/mycelos/connectors/mcp_client.py`, `mcp_manager.py` — they read `transport` and never `kind`, so the `kind` field is invisible to them.

---

## Task 1: Add `kind` field to `MCPRecipe`

**Files:**
- Modify: `src/mycelos/connectors/mcp_recipes.py`
- Test: `tests/test_recipe_kind.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_recipe_kind.py`:

```python
"""MCPRecipe has an explicit `kind` field."""
from __future__ import annotations

from mycelos.connectors.mcp_recipes import RECIPES, MCPRecipe, get_recipe


def test_recipe_kind_default_is_mcp() -> None:
    r = MCPRecipe(id="x", name="X", description="Y", command="")
    assert r.kind == "mcp"


def test_telegram_is_channel_kind() -> None:
    r = get_recipe("telegram")
    assert r is not None
    assert r.kind == "channel"


def test_all_non_telegram_recipes_are_mcp_kind() -> None:
    for rid, recipe in RECIPES.items():
        if rid == "telegram":
            continue
        assert recipe.kind == "mcp", f"{rid} kind is {recipe.kind!r}, expected 'mcp'"


def test_kind_only_accepts_channel_or_mcp() -> None:
    # Exhaustive check: every recipe's kind is one of the two allowed values.
    for recipe in RECIPES.values():
        assert recipe.kind in ("channel", "mcp")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_recipe_kind.py -v`
Expected: FAIL (MCPRecipe has no `kind` attribute).

- [ ] **Step 3: Implement — add `kind` field and update Telegram**

In `src/mycelos/connectors/mcp_recipes.py`:

Add `from typing import Literal` to the imports.

Inside the `MCPRecipe` dataclass, add `kind` as the first defaulted field:

```python
@dataclass(frozen=True)
class MCPRecipe:
    """A predefined MCP connector recipe."""

    id: str
    name: str
    description: str
    command: str
    transport: str = "stdio"
    kind: Literal["channel", "mcp"] = "mcp"
    credentials: list[dict] = field(default_factory=list)
    # ... rest unchanged
```

In the `telegram` entry in `RECIPES`, add `kind="channel",` on its own line right after `description=...`:

```python
    "telegram": MCPRecipe(
        id="telegram",
        name="Telegram Bot",
        description="Chat with Mycelos via Telegram",
        kind="channel",
        command="",
        transport="channel",
        # ... rest unchanged
    ),
```

Leave every other recipe untouched (they inherit the default `kind="mcp"`).

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_recipe_kind.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Run baseline**

Run: `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q`
Expected: all pass. The `kind` field is additive — existing code never reads it.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/connectors/mcp_recipes.py tests/test_recipe_kind.py
git commit -m "feat(connectors): add kind field to MCPRecipe (channel vs mcp)"
```

---

## Task 2: New helper `_setup_mcp` (recipe-driven replacement for CONNECTORS path)

**Files:**
- Modify: `src/mycelos/cli/connector_cmd.py`
- Test: `tests/test_connector_setup.py`

This task adds the new function alongside the old code. Old code is still called; we only add the new path. Removal of old code happens in Task 4 once everything is wired.

- [ ] **Step 1: Write the failing test**

Replace the body of `tests/test_connector_setup.py` (keep the imports section, but change what's imported) with:

```python
"""Tests for connector setup CLI (recipe-driven)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from mycelos.cli.connector_cmd import connector_cmd, _setup_mcp
from mycelos.connectors.mcp_recipes import RECIPES, get_recipe


def test_github_recipe_exists() -> None:
    """GitHub is an MCP recipe."""
    r = get_recipe("github")
    assert r is not None
    assert r.kind == "mcp"
    assert r.credentials and r.credentials[0]["env_var"] == "GITHUB_PERSONAL_ACCESS_TOKEN"


def test_brave_recipe_exists() -> None:
    """Brave Search is an MCP recipe."""
    r = get_recipe("brave-search")
    assert r is not None
    assert r.kind == "mcp"


def test_telegram_recipe_is_channel(tmp_data_dir: Path) -> None:
    """Telegram recipe has kind=channel."""
    r = get_recipe("telegram")
    assert r is not None
    assert r.kind == "channel"


def test_setup_mcp_no_key_grants_policy(tmp_data_dir: Path) -> None:
    """Setting up an MCP recipe without credentials grants its capabilities."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-setup-mcp-nokey"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        recipe = get_recipe("fetch")  # no credentials
        assert recipe is not None
        _setup_mcp(app, recipe)

        for cap in recipe.capabilities_preview:
            decision = app.policy_engine.evaluate("default", "any-agent", cap)
            assert decision == "always", f"cap {cap} not granted"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_setup_mcp_with_key_stores_credential(tmp_data_dir: Path) -> None:
    """Setting up a keyed MCP recipe stores the credential under recipe.id."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-setup-mcp-keyed"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        recipe = get_recipe("brave-search")
        assert recipe is not None

        with patch("click.prompt", return_value="BSA-test-token"), \
             patch("click.confirm", return_value=False):  # skip test step
            _setup_mcp(app, recipe)

        stored = app.credentials.get_credential("brave-search")
        assert stored is not None
        assert stored["api_key"] == "BSA-test-token"
        assert stored["env_var"] == "BRAVE_API_KEY"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_setup_mcp_registers_in_connector_registry(tmp_data_dir: Path) -> None:
    """After setup, connector_registry.get returns the connector."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-setup-mcp-registry"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        recipe = get_recipe("fetch")
        assert recipe is not None
        _setup_mcp(app, recipe)

        row = app.connector_registry.get("fetch")
        assert row is not None
        assert row["name"] == recipe.name
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_connector_setup.py -v`
Expected: FAIL (import of `_setup_mcp` fails because it doesn't exist yet).

- [ ] **Step 3: Implement `_setup_mcp`**

Add this function to `src/mycelos/cli/connector_cmd.py` right above `_setup_connector` (around line 265). Import `MCPRecipe` at the top of the file:

```python
from mycelos.connectors.mcp_recipes import MCPRecipe
```

Then the new function:

```python
def _setup_mcp(app: App, recipe: MCPRecipe) -> None:
    """Set up an MCP-kind recipe — credential prompt, policy grant, registry row."""
    console.print(f"\n[bold]{t('connector.setup_title', name=recipe.name)}[/bold]")
    console.print(f"[dim]{recipe.description}[/dim]\n")

    app.connector_registry.register(
        connector_id=recipe.id,
        name=recipe.name,
        connector_type=recipe.kind,          # "mcp" — shown in `Type` column
        capabilities=list(recipe.capabilities_preview),
        description=recipe.description,
        setup_type=recipe.setup_flow or ("key" if recipe.credentials else "none"),
    )

    if not recipe.credentials:
        console.print(f"[green]{t('connector.no_key_needed')}[/green]")
        for cap in recipe.capabilities_preview:
            app.policy_engine.set_policy("default", None, cap, "always")
        app.audit.log(
            "connector.setup",
            details={"connector": recipe.id, "capabilities": list(recipe.capabilities_preview)},
        )
        app.config.apply_from_state(
            state_manager=app.state_manager,
            description=f"Connector '{recipe.name}' eingerichtet",
            trigger="connector_setup",
        )
        console.print(f"\n[green]{t('connector.ready', name=recipe.name)}[/green]")
        return

    # Credentialed recipe — for oauth_http, defer to web UI (CLI cannot do OAuth redirect)
    if recipe.setup_flow == "oauth_http":
        console.print(
            f"[yellow]{recipe.name} uses OAuth. Open the Connectors page "
            f"in the web UI and click Setup.[/yellow]"
        )
        return

    cred = recipe.credentials[0]
    help_text = cred.get("help", "")
    if help_text:
        console.print(f"[yellow]{help_text}[/yellow]\n")

    existing = app.credentials.get_credential(recipe.id)
    if existing:
        console.print(f"[green]{t('connector.already_configured')}[/green]")
        if not click.confirm(t("connector.reconfigure"), default=False):
            return

    api_key = click.prompt(f"Enter your {cred['name']}", hide_input=True)

    app.credentials.store_credential(
        recipe.id,
        {
            "api_key": api_key,
            "env_var": cred["env_var"],
            "connector": recipe.id,
        },
    )

    for cap in recipe.capabilities_preview:
        app.policy_engine.set_policy("default", None, cap, "always")

    app.audit.log(
        "connector.setup",
        details={"connector": recipe.id, "capabilities": list(recipe.capabilities_preview)},
    )

    console.print(f"\n[green]{t('connector.configured', name=recipe.name)}[/green]")
    console.print(f"[dim]{t('connector.key_encrypted')}[/dim]")

    app.config.apply_from_state(
        state_manager=app.state_manager,
        description=f"Connector '{recipe.name}' eingerichtet",
        trigger="connector_setup",
    )

    if click.confirm(f"\n{t('connector.test_prompt')}", default=True):
        _test_connector_by_recipe(app, recipe, api_key)


def _test_connector_by_recipe(app: App, recipe: MCPRecipe, api_key: str) -> None:
    """Lightweight connectivity test dispatched by recipe.id."""
    if recipe.id == "brave-search":
        _test_brave(api_key)
    elif recipe.id == "github":
        _test_github(api_key)
    else:
        console.print(
            f"[yellow]No quick test available for '{recipe.id}'. "
            f"Connector registered.[/yellow]"
        )


def _test_brave(api_key: str) -> None:
    import httpx
    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": "hello world", "count": 1},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            console.print(f"[green]{t('connector.success')}[/green] {t('connector.api_responded')}")
        elif resp.status_code == 401:
            console.print(f"[red]{t('connector.api_key_invalid')}[/red]")
        else:
            console.print(f"[yellow]{t('connector.api_status', status=resp.status_code)}[/yellow]")
    except Exception as e:
        console.print(f"[red]{t('connector.test_failed', error=e)}[/red]")


def _test_github(api_key: str) -> None:
    import httpx
    try:
        resp = httpx.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            console.print(
                f"[green]{t('connector.success')}[/green] "
                f"Authenticated as [bold]{data.get('login', '?')}[/bold]"
            )
        elif resp.status_code == 401:
            console.print("[red]Token invalid or expired.[/red]")
        else:
            console.print(f"[yellow]GitHub returned status {resp.status_code}[/yellow]")
    except Exception as e:
        console.print(f"[red]{t('connector.test_failed', error=e)}[/red]")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/test_connector_setup.py -v`
Expected: all tests in that file pass.

- [ ] **Step 5: Run baseline**

Run: `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/cli/connector_cmd.py tests/test_connector_setup.py
git commit -m "feat(connectors): add _setup_mcp recipe-driven setup helper"
```

---

## Task 3: New helper `_setup_channel` that wraps the existing Telegram setup

**Files:**
- Modify: `src/mycelos/cli/connector_cmd.py`
- Test: `tests/test_telegram_channel.py`

- [ ] **Step 1: Rewrite the schema test**

Open `tests/test_telegram_channel.py` and find the block that currently asserts `CONNECTORS["telegram"]`. Replace with:

```python
def test_telegram_recipe_schema() -> None:
    """Telegram recipe has the right shape for channel setup."""
    from mycelos.connectors.mcp_recipes import get_recipe

    recipe = get_recipe("telegram")
    assert recipe is not None
    assert recipe.kind == "channel"
    assert recipe.credentials, "telegram recipe must declare a credential"
    cred = recipe.credentials[0]
    assert cred["env_var"] == "TELEGRAM_BOT_TOKEN"
    assert cred.get("help")
```

Leave all other tests in that file alone.

- [ ] **Step 2: Run the rewritten test to verify it passes against existing data**

Run: `PYTHONPATH=src pytest tests/test_telegram_channel.py::test_telegram_recipe_schema -v`
Expected: PASS (Telegram is already in RECIPES with a credential — Task 1 added `kind="channel"`).

- [ ] **Step 3: Implement `_setup_channel` as a thin wrapper**

Add to `src/mycelos/cli/connector_cmd.py`, above `_setup_telegram_connector`:

```python
def _setup_channel(app: App, recipe: MCPRecipe) -> None:
    """Set up a channel-kind recipe. Today only Telegram; other channels
    plug in here by branching on recipe.id."""
    if recipe.id == "telegram":
        # Wrap the legacy dict-shape expected by _setup_telegram_connector.
        info = {
            "name": recipe.name,
            "description": recipe.description,
            "key_help": recipe.credentials[0]["help"] if recipe.credentials else "",
        }
        _setup_telegram_connector(app, recipe.id, info)
        return
    console.print(f"[red]No channel setup handler for '{recipe.id}'.[/red]")
    raise SystemExit(1)
```

- [ ] **Step 4: Quick smoke test — import succeeds and function exists**

Run: `PYTHONPATH=src python3 -c "from mycelos.cli.connector_cmd import _setup_channel; print(_setup_channel)"`
Expected: prints `<function _setup_channel at 0x...>`.

- [ ] **Step 5: Run baseline**

Run: `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/cli/connector_cmd.py tests/test_telegram_channel.py
git commit -m "feat(connectors): add _setup_channel wrapper around legacy Telegram setup"
```

---

## Task 4: Route `setup_cmd` to the new helpers and remove `CONNECTORS` dict

**Files:**
- Modify: `src/mycelos/cli/connector_cmd.py`
- Test: `tests/test_connector_setup.py` (add two route tests)

- [ ] **Step 1: Add route tests**

Append to `tests/test_connector_setup.py`:

```python
def test_setup_cmd_routes_mcp_recipe(tmp_data_dir: Path) -> None:
    """`connector setup fetch` routes to _setup_mcp (no credential, auto-configures)."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-route-mcp"
    try:
        App(tmp_data_dir).initialize()
        runner = CliRunner()
        result = runner.invoke(
            connector_cmd,
            ["setup", "fetch", "--data-dir", str(tmp_data_dir)],
        )
        assert result.exit_code == 0, result.output
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_setup_cmd_rejects_unknown_id(tmp_data_dir: Path) -> None:
    """Unknown id exits non-zero with a pointer to `connector list`."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-route-unknown"
    try:
        App(tmp_data_dir).initialize()
        runner = CliRunner()
        result = runner.invoke(
            connector_cmd,
            ["setup", "does-not-exist", "--data-dir", str(tmp_data_dir)],
        )
        assert result.exit_code == 1
        assert "does-not-exist" in result.output
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_connector_setup.py::test_setup_cmd_routes_mcp_recipe tests/test_connector_setup.py::test_setup_cmd_rejects_unknown_id -v`
Expected: FAIL (`connector_name not in CONNECTORS` check blocks "fetch" — it's only in RECIPES).

- [ ] **Step 3: Rewrite `setup_cmd` to route on `get_recipe`**

In `src/mycelos/cli/connector_cmd.py`, replace the body of `setup_cmd` (around lines 96–125) with:

```python
@connector_cmd.command("setup")
@click.argument("connector_name", required=False)
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=default_data_dir,
)
def setup_cmd(connector_name: str | None, data_dir: Path) -> None:
    """Set up a connector.

    Without argument, shows available connectors to choose from.
    """
    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        console.print(
            f"[red]{t('common.error')}:[/red] {t('connector.not_initialized')}"
        )
        raise SystemExit(1)

    if not os.environ.get("MYCELOS_MASTER_KEY"):
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()

    app = App(data_dir)

    if connector_name:
        from mycelos.connectors.mcp_recipes import get_recipe, RECIPES
        recipe = get_recipe(connector_name)
        if recipe is None:
            console.print(
                f"[red]Unknown connector: {connector_name}[/red]\n"
                f"Run [bold]mycelos connector list[/bold] to see available connectors."
            )
            raise SystemExit(1)
        if recipe.kind == "channel":
            _setup_channel(app, recipe)
        else:
            _setup_mcp(app, recipe)
    else:
        _show_connector_menu(app)
```

- [ ] **Step 4: Rewrite `_show_connector_menu` to iterate `RECIPES`**

Replace the body of `_show_connector_menu` (around lines 221–262) with:

```python
def _show_connector_menu(app: App) -> None:
    """Show interactive connector selection menu grouped by kind."""
    from mycelos.connectors.mcp_recipes import RECIPES

    console.print(f"\n[bold]{t('connector.available_title')}[/bold]\n")

    configured_services = app.credentials.list_services()
    entries: list[MCPRecipe] = sorted(
        RECIPES.values(),
        key=lambda r: (0 if r.kind == "channel" else 1, r.category, r.id),
    )

    last_kind: str | None = None
    numbered: list[MCPRecipe] = []
    for recipe in entries:
        if recipe.kind != last_kind:
            header = "Channels" if recipe.kind == "channel" else "MCP Connectors"
            console.print(f"\n[bold cyan]{header}[/bold cyan]")
            last_kind = recipe.kind
        numbered.append(recipe)
        idx = len(numbered)
        if recipe.id in configured_services or recipe.id in {
            c["id"] for c in app.connector_registry.list_connectors()
        }:
            status = "[green](configured)[/green]"
        elif not recipe.credentials:
            status = "[green](ready, no key needed)[/green]"
        else:
            status = "[yellow](not configured)[/yellow]"
        console.print(f"  ({idx}) {recipe.name}  {status}")
        console.print(f"      [dim]{recipe.description}[/dim]")

    console.print()
    choice = click.prompt(
        "Which connector to set up? (number or 'q' to quit)",
        default="q",
    )
    if choice.lower() == "q":
        return
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(numbered):
            recipe = numbered[idx]
            if recipe.kind == "channel":
                _setup_channel(app, recipe)
            else:
                _setup_mcp(app, recipe)
        else:
            console.print(f"[red]{t('connector.invalid_selection')}[/red]")
    except ValueError:
        console.print(f"[red]{t('connector.invalid_input')}[/red]")
```

- [ ] **Step 5: Delete the `CONNECTORS` dict and the old `_setup_connector` + `_test_connector`**

In `src/mycelos/cli/connector_cmd.py`:

- Delete the entire `CONNECTORS: dict[str, dict[str, Any]] = { ... }` block (lines ~26–80).
- Delete the old `_setup_connector(app, key, info)` function (around lines 265–362).
- Delete the old `_test_connector(app, key, info, api_key)` function (around lines 365–450).

Keep `_setup_telegram_connector`, `_collect_telegram_allowlist`, `_add_manual_ids` — they're still used by `_setup_channel`.

- [ ] **Step 6: Run the route tests**

Run: `PYTHONPATH=src pytest tests/test_connector_setup.py -v`
Expected: all pass.

- [ ] **Step 7: Run baseline**

Run: `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q`
Expected: all pass. If any test imports `CONNECTORS`, that test will fail — fix it to import from `mcp_recipes` instead.

- [ ] **Step 8: Commit**

```bash
git add src/mycelos/cli/connector_cmd.py tests/test_connector_setup.py
git commit -m "refactor(connectors): remove CONNECTORS dict; route setup via recipe.kind"
```

---

## Task 5: Rewrite `list_cmd` to render three sections

**Files:**
- Modify: `src/mycelos/cli/connector_cmd.py`
- Test: `tests/test_connector_list_two_sections.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_connector_list_two_sections.py`:

```python
"""`mycelos connector list` renders Installed / Channels / MCP Connectors sections."""

from __future__ import annotations

import os
from pathlib import Path

from click.testing import CliRunner

from mycelos.cli.connector_cmd import connector_cmd


def test_list_empty_shows_channels_and_mcp_sections(tmp_data_dir: Path) -> None:
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-list-sections"
    try:
        App(tmp_data_dir).initialize()
        runner = CliRunner()
        result = runner.invoke(connector_cmd, ["list", "--data-dir", str(tmp_data_dir)])
        assert result.exit_code == 0
        assert "Channels" in result.output
        assert "MCP Connectors" in result.output
        # Telegram must be under Channels, github under MCP Connectors.
        channels_idx = result.output.find("Channels")
        mcp_idx = result.output.find("MCP Connectors")
        assert channels_idx >= 0 and mcp_idx > channels_idx
        tg_idx = result.output.find("telegram")
        gh_idx = result.output.find("github")
        assert channels_idx < tg_idx < mcp_idx, "telegram must appear under Channels"
        assert mcp_idx < gh_idx, "github must appear under MCP Connectors"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_list_drops_kind_column(tmp_data_dir: Path) -> None:
    """The available-recipes tables no longer have a Kind column (sections do that job)."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-list-nokind"
    try:
        App(tmp_data_dir).initialize()
        runner = CliRunner()
        result = runner.invoke(connector_cmd, ["list", "--data-dir", str(tmp_data_dir)])
        # The word "Kind" appears only in the Installed table header, not in the
        # available-recipes tables. When no connectors are configured, the
        # Installed table isn't shown — so the string shouldn't be present at all.
        # (This assertion only holds on a fresh install with no configured connectors.)
        assert "Kind" not in result.output, (
            "Kind column should not appear in available-recipes tables; "
            "sections replace it."
        )
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_connector_list_two_sections.py -v`
Expected: FAIL (current output has a single "Available recipes" table with a Kind column).

- [ ] **Step 3: Rewrite `list_cmd`**

Replace the body of `list_cmd` in `src/mycelos/cli/connector_cmd.py` (around lines 128–218) with:

```python
@connector_cmd.command("list")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=default_data_dir,
)
def list_cmd(data_dir: Path) -> None:
    """List configured connectors plus available recipes, split by kind."""
    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        console.print(
            f"[red]{t('common.error')}:[/red] {t('connector.not_initialized_short')}"
        )
        raise SystemExit(1)

    if not os.environ.get("MYCELOS_MASTER_KEY"):
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()

    app = App(data_dir)
    from mycelos.connectors.mcp_recipes import RECIPES
    registered = app.connector_registry.list_connectors()
    registered_ids = {c["id"] for c in registered}

    state_styles = {
        "healthy": "[green]● healthy[/green]",
        "ready": "[cyan]● ready[/cyan]",
        "failing": "[red]● failing[/red]",
        "setup_incomplete": "[yellow]● setup incomplete[/yellow]",
    }

    # ── Installed ─────────────────────────────────────────────────
    if registered:
        installed = Table(title="Installed connectors")
        installed.add_column("Connector", style="bold")
        installed.add_column("Type", style="dim")
        installed.add_column("State")
        installed.add_column("Capabilities", overflow="fold")
        for c in registered:
            state = state_styles.get(
                c.get("operational_state"), c.get("operational_state") or "?"
            )
            caps = ", ".join(c.get("capabilities") or []) or "[dim]—[/dim]"
            installed.add_row(
                c.get("name") or c["id"],
                c.get("connector_type") or "?",
                state,
                caps,
            )
        console.print(installed)
    else:
        console.print("[dim]No connectors configured yet.[/dim]")

    # ── Available channels ────────────────────────────────────────
    available_channels = [
        r for r in RECIPES.values()
        if r.kind == "channel" and r.id not in registered_ids
    ]
    if available_channels:
        tbl = Table(title="Channels (not yet configured)")
        tbl.add_column("Recipe", style="bold")
        tbl.add_column("Setup", style="dim")
        tbl.add_column("Description", overflow="fold")
        for r in sorted(available_channels, key=lambda x: x.id):
            desc = (r.description or "").splitlines()[0] if r.description else ""
            tbl.add_row(r.id, r.setup_flow or "secret", desc)
        console.print()
        console.print(tbl)

    # ── Available MCP connectors ──────────────────────────────────
    available_mcp = [
        r for r in RECIPES.values()
        if r.kind == "mcp" and r.id not in registered_ids
    ]
    if available_mcp:
        tbl = Table(title="MCP Connectors (not yet configured)")
        tbl.add_column("Recipe", style="bold")
        tbl.add_column("Category", style="dim")
        tbl.add_column("Setup", style="dim")
        tbl.add_column("Description", overflow="fold")
        for r in sorted(available_mcp, key=lambda x: (x.category, x.id)):
            desc = (r.description or "").splitlines()[0] if r.description else ""
            tbl.add_row(r.id, r.category, r.setup_flow or "secret", desc)
        console.print()
        console.print(tbl)

    if available_channels or available_mcp:
        console.print(
            "\n[dim]Use [/dim][bold]mycelos connector setup <id>[/bold]"
            "[dim] to configure one, or open the Connectors page in the web UI.[/dim]"
        )
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_connector_list_two_sections.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Run baseline**

Run: `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/cli/connector_cmd.py tests/test_connector_list_two_sections.py
git commit -m "feat(connectors): CLI list splits Channels and MCP Connectors into sections"
```

---

## Task 6: Trim `/connector` slash command to read-only verbs

**Files:**
- Modify: `src/mycelos/chat/slash_commands.py`
- Modify: `src/mycelos/cli/completer.py`
- Test: `tests/test_slash_commands.py` (rewrite `/connector` cases)

- [ ] **Step 1: Write the failing test**

In `tests/test_slash_commands.py` (or whichever file asserts `/connector add` behavior — run `PYTHONPATH=src pytest tests/ -q -k connector` to find it), add:

```python
def test_connector_add_is_deprecated(dummy_app) -> None:
    """`/connector add` now returns a pointer to CLI / Web UI, not a setup flow."""
    from mycelos.chat.slash_commands import handle_slash_command

    result = handle_slash_command(dummy_app, "connector", ["add", "github"])
    # result may be a string or list of events — normalize:
    text = result if isinstance(result, str) else "".join(
        ev.data.get("content", "") for ev in result if hasattr(ev, "data")
    )
    assert "not supported in chat" in text.lower() or "use the web ui" in text.lower() \
           or "use the cli" in text.lower()


def test_connector_list_still_works(dummy_app) -> None:
    from mycelos.chat.slash_commands import handle_slash_command
    result = handle_slash_command(dummy_app, "connector", ["list"])
    text = result if isinstance(result, str) else "".join(
        ev.data.get("content", "") for ev in result if hasattr(ev, "data")
    )
    assert "Available Connectors" in text or "connector" in text.lower()
```

If `dummy_app` fixture doesn't exist in that test file, find how existing `/connector list` tests construct their app and copy the same setup.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_slash_commands.py -v -k connector`
Expected: FAIL (the deprecation test fails because `add` still runs `_connector_add_smart`).

- [ ] **Step 3: Implement — strip setup verbs in `slash_commands.py`**

In `src/mycelos/chat/slash_commands.py`, find the `/connector` dispatcher (around line 678, the function after `"""Handle /connector commands — list, add, remove, test."""`). Replace it with:

```python
def _connector(app: Any, args: list[str]) -> Any:
    """Handle /connector — read-only verbs only (list, search)."""
    if not args:
        return _connector_list(app)

    action = args[0].lower()

    if action == "list":
        return _connector_list(app)
    if action == "search" and len(args) >= 2:
        return _connector_search(" ".join(args[1:]))
    if action in {"add", "setup", "remove", "test"}:
        return (
            f"`/connector {action}` is not supported in chat.\n\n"
            f"To set up or remove a connector, use one of:\n"
            f"  - **Web UI**: open the Connectors page\n"
            f"  - **CLI**: `mycelos connector setup <id>` / `mycelos connector remove <id>` / `mycelos connector test <id>`\n\n"
            f"Credentials stay out of the chat transcript this way."
        )
    return (
        "Usage:\n"
        "  `/connector list` — Show available and active connectors\n"
        "  `/connector search <query>` — Search the MCP registry for community servers\n\n"
        "Setup happens in the Web UI or CLI (`mycelos connector setup <id>`)."
    )
```

If the dispatcher is defined under a different name than `_connector`, keep its existing name — just replace the body.

Delete the now-unused functions: `_connector_add_smart`, `_connector_add`, `_connector_add_with_key`, `_connector_add_custom`, `_connector_remove`, `_connector_test`. Keep `_connector_list`, `_connector_search`, and `_connector_list`'s helpers.

- [ ] **Step 4: Update autocomplete in `completer.py`**

Open `src/mycelos/cli/completer.py` and find the `/connector` entry in `SLASH_COMMANDS`. Replace its `subcommands` list to show only the supported verbs. Grep for `"connector"` in that file and reduce to:

```python
"connector": {
    "description": "Manage external service connectors",
    "subcommands": {
        "list": "Show available and active connectors",
        "search": "Search the MCP registry for community servers",
    },
},
```

Preserve the surrounding structure — don't reformat the whole dict.

- [ ] **Step 5: Run slash tests**

Run: `PYTHONPATH=src pytest tests/test_slash_commands.py -v -k connector`
Expected: all pass.

- [ ] **Step 6: Run baseline**

Run: `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q`
Expected: all pass. If any test still imports the deleted `_connector_add*` functions, delete those tests — they're obsolete.

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/chat/slash_commands.py src/mycelos/cli/completer.py tests/test_slash_commands.py
git commit -m "refactor(chat): trim /connector to read-only verbs; setup moves to CLI/Web UI"
```

---

## Task 7: Update gateway API to return channels + mcp split

**Files:**
- Modify: `src/mycelos/gateway/routes.py`
- Test: `tests/test_frontend_connectors_api.py` (new)

- [ ] **Step 1: Find the endpoint**

Run: `PYTHONPATH=src grep -n "/api/connectors/recipes" src/mycelos/gateway/routes.py | head -20`

Note the line numbers where the recipes-list endpoint is defined (probably near the OAuth endpoints added in the previous Google MCP work).

- [ ] **Step 2: Write the failing test**

Create `tests/test_frontend_connectors_api.py`:

```python
"""/api/connectors/recipes returns {channels, mcp} split by recipe.kind."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-recipes-api-key"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        App(Path(tmp)).initialize()
        fastapi_app = create_app(Path(tmp), no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


def test_recipes_endpoint_returns_channels_and_mcp(client: TestClient) -> None:
    resp = client.get("/api/connectors/recipes")
    assert resp.status_code == 200
    data = resp.json()
    assert "channels" in data
    assert "mcp" in data
    assert isinstance(data["channels"], list)
    assert isinstance(data["mcp"], list)


def test_telegram_appears_under_channels(client: TestClient) -> None:
    resp = client.get("/api/connectors/recipes")
    assert resp.status_code == 200
    channel_ids = {r["id"] for r in resp.json()["channels"]}
    mcp_ids = {r["id"] for r in resp.json()["mcp"]}
    assert "telegram" in channel_ids
    assert "telegram" not in mcp_ids


def test_github_appears_under_mcp(client: TestClient) -> None:
    resp = client.get("/api/connectors/recipes")
    assert resp.status_code == 200
    mcp_ids = {r["id"] for r in resp.json()["mcp"]}
    channel_ids = {r["id"] for r in resp.json()["channels"]}
    assert "github" in mcp_ids
    assert "github" not in channel_ids


def test_each_recipe_has_kind_field(client: TestClient) -> None:
    resp = client.get("/api/connectors/recipes")
    for r in resp.json()["channels"]:
        assert r.get("kind") == "channel"
    for r in resp.json()["mcp"]:
        assert r.get("kind") == "mcp"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_frontend_connectors_api.py -v`
Expected: FAIL (endpoint currently returns a flat list, not a dict).

- [ ] **Step 4: Update the endpoint**

Find the recipes list endpoint in `src/mycelos/gateway/routes.py`. Locate it by grepping for `list_recipes` or `RECIPES` usage inside route handlers. Update the handler so it returns:

```python
@router.get("/api/connectors/recipes")
def list_recipes_grouped() -> dict:
    from mycelos.connectors.mcp_recipes import RECIPES
    channels = []
    mcp = []
    for recipe in RECIPES.values():
        payload = {
            "id": recipe.id,
            "name": recipe.name,
            "description": recipe.description,
            "kind": recipe.kind,
            "category": recipe.category,
            "setup_flow": recipe.setup_flow,
            "capabilities_preview": list(recipe.capabilities_preview),
            "credentials": list(recipe.credentials),
            "requires_node": recipe.requires_node,
        }
        if recipe.kind == "channel":
            channels.append(payload)
        else:
            mcp.append(payload)
    return {"channels": channels, "mcp": mcp}
```

If the current endpoint is named differently (e.g. `get_recipes_list`), keep the function name and decorator path; only swap the body.

If a route at the same path already exists that returns a flat list, replace that route's body entirely. Do not add a second route at the same path (FastAPI raises on duplicates).

- [ ] **Step 5: Run the new test**

Run: `PYTHONPATH=src pytest tests/test_frontend_connectors_api.py -v`
Expected: 4 PASS.

- [ ] **Step 6: Run baseline**

Run: `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q`
Expected: all pass. If any test asserts the old flat-list shape of `/api/connectors/recipes`, update it to read from `data["mcp"]` or `data["channels"]`.

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/gateway/routes.py tests/test_frontend_connectors_api.py
git commit -m "feat(gateway): /api/connectors/recipes returns channels + mcp grouped"
```

---

## Task 8: Web UI — render two sections on the Connectors page

**Files:**
- Modify: `src/mycelos/frontend/pages/connectors.html` (and its JS)

This task has no pytest — verification is manual via browser. The baseline rule still holds (other tests must stay green).

- [ ] **Step 1: Locate the recipe-rendering code**

Run: `PYTHONPATH=src grep -n "recipes" src/mycelos/frontend/pages/connectors.html | head -20`
Also search for the JS that calls `/api/connectors/recipes` — likely in a `<script>` tag in the same file or a companion `.js` file.

- [ ] **Step 2: Update the fetch + render**

Where the JS today does `data.forEach(recipe => ...)` (treating `data` as a flat array), change it to:

```javascript
async function loadRecipes() {
  const resp = await fetch('/api/connectors/recipes');
  const data = await resp.json();
  renderSection('channels-container', 'Channels', data.channels);
  renderSection('mcp-container', 'MCP Connectors', data.mcp);
}

function renderSection(containerId, title, recipes) {
  const el = document.getElementById(containerId);
  if (!recipes || recipes.length === 0) { el.innerHTML = ''; return; }
  el.innerHTML = `
    <h3 class="section-title">${title}</h3>
    <div class="recipe-grid">
      ${recipes.map(r => renderRecipeCard(r)).join('')}
    </div>`;
}
```

Add two container divs in the HTML where the old flat list used to render:

```html
<section id="available-connectors">
  <div id="channels-container"></div>
  <div id="mcp-container"></div>
</section>
```

Keep `renderRecipeCard(recipe)` as-is — it already expects the fields from the API (name, description, id, setup_flow, etc.).

- [ ] **Step 3: Manual verification**

Start the gateway:

```bash
PYTHONPATH=src MYCELOS_MASTER_KEY=dev-key python3 -m mycelos serve --data-dir /tmp/mycelos-spec1-smoke &
sleep 3
open http://localhost:9100/connectors
```

Visually confirm:
- Two labeled sections appear: "Channels" (contains Telegram) and "MCP Connectors" (contains GitHub, Gmail, etc.)
- Clicking Setup on any card opens the existing setup dialog (OAuth flow still works for Gmail).

Stop the server after verification (`kill %1`).

- [ ] **Step 4: Run baseline**

Run: `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/frontend/pages/connectors.html
git commit -m "feat(web): Connectors page splits Channels and MCP Connectors into sections"
```

---

## Task 9: CHANGELOG entry + final verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add CHANGELOG entry**

Open `CHANGELOG.md` and add under the current week's heading (check the top of the file for the convention — calendar weeks per CLAUDE.md):

```markdown
- Unified the connector registry: `MCPRecipe.kind` ("channel" or "mcp")
  replaces the parallel `CONNECTORS` dict in `cli/connector_cmd.py`.
  Telegram is now the sole `kind="channel"` recipe; all others are `kind="mcp"`.
- `mycelos connector list` renders three sections: Installed / Channels /
  MCP Connectors. No more mixed-kind tables.
- Chat `/connector add`, `/connector setup`, `/connector remove`, and
  `/connector test` were removed. Setup happens in the Web UI or via
  `mycelos connector setup <id>`. `/connector list` and `/connector search`
  remain available in chat.
- `/api/connectors/recipes` now returns `{channels: [...], mcp: [...]}`.
- The legacy HTTP and DuckDuckGo "connectors" are no longer shown in
  `connector list` — they are plain in-process tools and are registered
  at startup like Knowledge-Base.
```

- [ ] **Step 2: Full baseline**

Run: `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q`
Expected: all pass, zero failures.

- [ ] **Step 3: Manual end-to-end**

Run each of these and visually confirm:

```bash
PYTHONPATH=src mycelos connector list --data-dir /tmp/mycelos-spec1-check
PYTHONPATH=src mycelos connector setup fetch --data-dir /tmp/mycelos-spec1-check
PYTHONPATH=src mycelos connector list --data-dir /tmp/mycelos-spec1-check
```

Expected:
- First `list`: "No connectors configured yet." + "Channels" and "MCP Connectors" sections listing telegram and the MCP recipes.
- `setup fetch`: completes without prompts (no credentials needed) and prints "Connector 'HTTP Fetch' ready!".
- Second `list`: "Installed connectors" table now contains `fetch` with `mcp` in the Type column.

- [ ] **Step 4: Commit changelog**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): connector registry unification (Spec 1)"
```

- [ ] **Step 5: Push**

```bash
git push origin main
```

---

## Self-review notes

All spec success criteria map to tasks:

1. `CONNECTORS` dict removed → Task 4.
2. `MCPRecipe.kind` + `telegram.kind == "channel"` → Task 1.
3. `setup telegram` / `setup github` work → Tasks 2, 3, 4 (routing).
4. `list` shows three sections → Task 5.
5. Web UI matches CLI structure → Tasks 7 (API), 8 (HTML/JS).
6. `/connector` chat read-only → Task 6.
7. Autocomplete updated → Task 6.
8. Baseline green → checked after every task.
9. CHANGELOG updated → Task 9.

Non-goals explicitly deferred: `ui.open_page` (Spec 1.5), capability discovery (Spec 2).
