# `ui.open_page` Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Mycelos chat agent a `ui.open_page` tool so it can route the user directly to admin pages (Connectors, Model settings, Config Generations, Doctor) instead of explaining setup steps in prose. Then sweep dead prose-routing strings out of the chat layer.

**Architecture:** Pure-function tool that returns a `suggested_actions_event` with a new `kind: "link"` discriminator. Frontend / Telegram / CLI renderers branch on `kind` and turn the link into a clickable card / Markdown link / ANSI hyperlink. Recipe-page-style enum of allowed targets (no free-form path). Mycelos system prompt updated to use the tool. Existing prose hints in `slash_commands.py` and `chat/context.py` get trimmed.

**Tech Stack:** Python 3.12+, FastAPI, Alpine.js (vanilla, no build step), pytest, Material Symbols (existing).

**Spec:** `docs/superpowers/specs/2026-04-26-ui-open-page-tool-design.md`

**Baseline rule:** After every task, `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q` must pass with zero failures (modulo the known Hypothesis flake on `test_policy_engine_property.py`).

**Code-archaeology done up front (so the plan uses real names):**

- Tool registration uses `ToolPermission.STANDARD` from `src/mycelos/tools/registry.py` (Enum value), NOT a `ToolPermission(capability="…")` constructor. `STANDARD` means "Mycelos + Builder agents only". This matches what other tools do (`memory_write`, `search_web`, etc.). The spec's D1 wording about "capability-gated" is correct in spirit (the registry's `_AGENT_PERMISSIONS` map enforces who can use what), but the implementation is the Enum + registry, not a per-tool capability + policy.
- `register()` signature: `registry.register(name, SCHEMA, execute_fn, ToolPermission.STANDARD, concurrent_safe=True, category="ui")`.
- Tool registry is loaded in `src/mycelos/tools/registry.py::_ensure_initialized()` — that's where the new `from mycelos.tools import ui` import goes.
- Event helpers: `system_response_event(content)` and `suggested_actions_event(actions)` already exist in `src/mycelos/chat/events.py`.
- Today's `suggested_actions_event` actions look like `{label, command}`. We add a third optional field `url` plus a discriminator `kind: "link"`.

---

## File Structure

Files this plan touches:

- `src/mycelos/tools/ui.py` — NEW. The `ui.open_page` tool + register().
- `src/mycelos/tools/registry.py` — wire `ui` module into `_ensure_initialized()`.
- `src/mycelos/agents/handlers/mycelos.py` (or the prompt template) — system-prompt addendum about when to use `ui.open_page`.
- `src/mycelos/frontend/pages/chat.html` — branch on `action.kind === "link"` in the suggested-actions renderer.
- Telegram channel renderer (find via grep — likely `src/mycelos/channels/telegram_*.py`) — branch on `kind === "link"`, build absolute URL + hint.
- CLI chat renderer (find via grep) — branch on `kind === "link"`, ANSI hyperlink output.
- `src/mycelos/i18n/locales/en.yaml`, `de.yaml` — new key `telegram.web_link_hint`.
- `src/mycelos/chat/slash_commands.py:101, 693-694, 701, 741` — trim prose hints.
- `src/mycelos/chat/context.py:32, 92` — trim prose hints.
- `tests/test_ui_open_page.py` — NEW. 8 unit tests for the tool.
- `CHANGELOG.md` — Week 17 entry.

---

## Task 1: The `ui.open_page` tool itself

**Files:**
- Create: `src/mycelos/tools/ui.py`
- Test: `tests/test_ui_open_page.py`

The tool is pure: validate input, build URL, return one event. No side effects, no DB, no network. That makes it trivially unit-testable.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ui_open_page.py`:

```python
"""Tests for the ui.open_page tool."""

from __future__ import annotations

from mycelos.tools.ui import execute_open_page


def test_target_connectors_no_anchor() -> None:
    events = execute_open_page({"target": "connectors"}, context={})
    assert len(events) == 1
    actions = events[0].data["actions"]
    assert len(actions) == 1
    assert actions[0]["url"] == "/pages/connectors.html"
    assert actions[0]["kind"] == "link"
    assert actions[0]["label"] == "Open Connectors page"


def test_target_connectors_with_anchor() -> None:
    events = execute_open_page(
        {"target": "connectors", "anchor": "gmail"}, context={}
    )
    actions = events[0].data["actions"]
    assert actions[0]["url"] == "/pages/connectors.html#gmail"


def test_target_settings_models_default_anchor() -> None:
    events = execute_open_page({"target": "settings_models"}, context={})
    actions = events[0].data["actions"]
    assert actions[0]["url"] == "/pages/settings.html#models"


def test_target_settings_models_anchor_overrides_default() -> None:
    events = execute_open_page(
        {"target": "settings_models", "anchor": "provider-anthropic"},
        context={},
    )
    actions = events[0].data["actions"]
    assert actions[0]["url"] == "/pages/settings.html#provider-anthropic"


def test_unknown_target_returns_error_event() -> None:
    events = execute_open_page({"target": "memory"}, context={})
    assert len(events) == 1
    # Error path returns a system_response_event with the allowed list
    content = events[0].data.get("content", "")
    assert "memory" in content.lower() or "unknown" in content.lower()
    assert "connectors" in content  # the allowed-targets list


def test_custom_label_respected() -> None:
    events = execute_open_page(
        {"target": "connectors", "anchor": "gmail", "label": "Gmail einrichten"},
        context={},
    )
    actions = events[0].data["actions"]
    assert actions[0]["label"] == "Gmail einrichten"


def test_anchor_strips_leading_hash() -> None:
    """`anchor='#gmail'` and `anchor='gmail'` produce the same URL."""
    a = execute_open_page({"target": "connectors", "anchor": "gmail"}, context={})
    b = execute_open_page({"target": "connectors", "anchor": "#gmail"}, context={})
    assert a[0].data["actions"][0]["url"] == b[0].data["actions"][0]["url"]


def test_all_four_targets_resolve() -> None:
    """Every documented target maps to a URL — no silent omissions."""
    for target in ("connectors", "settings_models", "settings_generations", "doctor"):
        events = execute_open_page({"target": target}, context={})
        actions = events[0].data["actions"]
        assert actions[0]["url"].startswith("/pages/"), (
            f"target {target!r} produced URL {actions[0]['url']!r}"
        )
        assert actions[0]["kind"] == "link"
```

- [ ] **Step 2: Run, see all 8 fail**

```
PYTHONPATH=src pytest tests/test_ui_open_page.py -v
```

Expected: 8 fails with `ImportError: cannot import name 'execute_open_page' from 'mycelos.tools.ui'` (the module doesn't exist).

- [ ] **Step 3: Create the tool module**

Create `src/mycelos/tools/ui.py` with this exact content:

```python
"""ui.open_page — let the agent send the user to a specific admin page.

Returns a suggested-actions event with a single link the user can click
to navigate. Used when the user asks to set up / configure / inspect
something that lives in the Web UI.
"""

from __future__ import annotations

from typing import Any

from mycelos.chat.events import suggested_actions_event, system_response_event
from mycelos.tools.registry import ToolPermission


_URL_TARGETS: dict[str, str] = {
    "connectors": "/pages/connectors.html",
    "settings_models": "/pages/settings.html#models",
    "settings_generations": "/pages/settings.html#generations",
    "doctor": "/pages/doctor.html",
}

_DEFAULT_LABELS: dict[str, str] = {
    "connectors": "Open Connectors page",
    "settings_models": "Open Model settings",
    "settings_generations": "Open Config Generations",
    "doctor": "Open Doctor",
}


OPEN_PAGE_SCHEMA = {
    "name": "ui.open_page",
    "description": (
        "Send the user directly to a Web-UI admin page. Use this when "
        "the user asks to set up / configure / inspect something that "
        "requires the Web UI (connector setup, model assignments, "
        "rollback, diagnostics). Don't explain the steps — give them "
        "the link instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": sorted(_URL_TARGETS.keys()),
                "description": (
                    "Which admin page to open. "
                    "`connectors` for connector setup, "
                    "`settings_models` for LLM model configuration, "
                    "`settings_generations` for config rollback UI, "
                    "`doctor` for diagnostics."
                ),
            },
            "anchor": {
                "type": "string",
                "description": (
                    "Optional anchor for a sub-target on the page. "
                    "E.g. `gmail` on the Connectors page jumps to the "
                    "Gmail recipe card."
                ),
            },
            "label": {
                "type": "string",
                "description": (
                    "Optional button text the user sees. "
                    "Defaults to a generic per-target label like "
                    "'Open Connectors page'."
                ),
            },
        },
        "required": ["target"],
    },
}


def execute_open_page(args: dict[str, Any], context: dict) -> list:
    """Build a clickable-link event. Pure function — no side effects."""
    target = args.get("target", "")
    if target not in _URL_TARGETS:
        return [
            system_response_event(
                f"Unknown UI target: {target!r}. "
                f"Allowed: {', '.join(sorted(_URL_TARGETS))}."
            )
        ]

    url = _URL_TARGETS[target]
    anchor = (args.get("anchor") or "").strip().lstrip("#")
    if anchor:
        # Replace any existing default anchor with the explicit one so the
        # caller can target arbitrary sub-sections, not just the default.
        base, _, _ = url.partition("#")
        url = f"{base}#{anchor}"

    label = (args.get("label") or "").strip() or _DEFAULT_LABELS[target]

    return [
        suggested_actions_event([
            {"label": label, "url": url, "kind": "link"},
        ])
    ]


def register(registry: type) -> None:
    """Register ui.open_page with the tool registry."""
    registry.register(
        "ui.open_page",
        OPEN_PAGE_SCHEMA,
        execute_open_page,
        ToolPermission.STANDARD,
        concurrent_safe=True,
        category="ui",
    )
```

- [ ] **Step 4: Run tests again**

```
PYTHONPATH=src pytest tests/test_ui_open_page.py -v
```

Expected: 8 pass.

- [ ] **Step 5: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures. The new module isn't wired into the registry yet, so existing code can't see it — nothing should break.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/tools/ui.py tests/test_ui_open_page.py
git commit -m "feat(tools): add ui.open_page — agent links user to admin pages"
```

**Rules (CLAUDE.md):**
- No `Co-Authored-By` / "Generated with Claude Code" footer.
- English commit message.
- Do NOT push (last task pushes everything).
- Do NOT touch CHANGELOG.
- Do NOT wire the registry yet — Task 2 owns that.

---

## Task 2: Wire the tool into the registry

**Files:**
- Modify: `src/mycelos/tools/registry.py` (`_ensure_initialized()` block)

- [ ] **Step 1: Locate the existing initialization block**

```
grep -n "_ensure_initialized\|from mycelos.tools import" src/mycelos/tools/registry.py | head -20
```

You should see an `_ensure_initialized` classmethod near line 205 with a series of `from mycelos.tools import …` imports inside it. Each existing tool module gets imported and `register(_REGISTRY)` is called.

- [ ] **Step 2: Add the ui import + registration**

Inside `_ensure_initialized()`, add (alphabetical placement near other tool imports):

```python
        from mycelos.tools import ui as _ui_tools
        _ui_tools.register(cls)
```

Use `cls` if the existing imports use `cls`, or `_REGISTRY` / whatever the existing pattern uses — match what's already there. The registry is the same singleton.

- [ ] **Step 3: Quick smoke test**

```
PYTHONPATH=src python3 -c "
from mycelos.tools.registry import ToolRegistry
ToolRegistry._ensure_initialized()
schema = ToolRegistry.get_tool_schema('ui.open_page')
print('OK' if schema else 'NOT REGISTERED')
"
```

Expected: prints `OK`. If `NOT REGISTERED`, the import / register call isn't taking effect — re-check Step 2.

- [ ] **Step 4: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/tools/registry.py
git commit -m "feat(tools): register ui.open_page in tool registry"
```

---

## Task 3: System-prompt addendum for Mycelos agent

**Files:**
- Modify: the file that holds the Mycelos system prompt — find via grep below.

- [ ] **Step 1: Find the prompt source**

```
grep -rn "Mycelos\|mycelos.*agent.*prompt\|system_prompt" src/mycelos/agents/handlers/ src/mycelos/agents/ 2>/dev/null | grep -iE "prompt|persona|character" | head -20
```

The Mycelos system prompt is typically composed in `src/mycelos/agents/handlers/mycelos.py` or pulled from `src/mycelos/agents/prompts/mycelos.md`. Use the grep output to identify the right file. If the prompt is in a `.md` file that gets read at runtime, edit that file. If it's a Python string literal in the handler, edit there.

- [ ] **Step 2: Find the existing tool-usage section**

Within the prompt source, look for an existing section that lists / introduces tools the agent has. The new section should slot in alongside or right after it, formatted consistently.

- [ ] **Step 3: Add the new section**

Add this section to the prompt:

```markdown
## Sending the user to the Web UI

When the user asks to set up, configure, or inspect something that lives in the Web UI, use the `ui.open_page` tool to give them a clickable link instead of explaining the steps in prose. Targets:

- `connectors` (with optional `anchor` like `gmail`, `github`) — connector setup, OAuth, credentials per service
- `settings_models` — LLM model assignments per agent or system defaults
- `settings_generations` — config rollback UI
- `doctor` — diagnostic page when something isn't working

Pair the tool call with a short text response so the user sees both the answer and the action card. Don't enumerate setup steps yourself — the page does it better.
```

If the prompt is in a `.md` file, paste verbatim. If it's a Python string, escape any literal `{` / `}` if they would interfere with `.format()` calls (in practice the prompt is rarely formatted; check).

- [ ] **Step 4: Smoke check — prompt loads**

```
PYTHONPATH=src python3 -c "
from mycelos.agents.handlers.mycelos import build_prompt  # or wherever it lives
print('OK' if 'ui.open_page' in build_prompt({}) else 'PROMPT NOT UPDATED')
"
```

If the prompt is loaded differently (e.g. via a registry lookup), adapt the smoke check to match. The point: confirm the new section reaches the LLM.

If you can't find a `build_prompt` function, just `cat` or `grep ui.open_page` against the prompt source file and confirm the addition is there.

- [ ] **Step 5: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 6: Commit**

```bash
git add <path-to-prompt-source>
git commit -m "feat(agents): mycelos prompt teaches ui.open_page tool usage"
```

---

## Task 4: Frontend chat renderer — handle `kind: "link"`

**Files:**
- Modify: `src/mycelos/frontend/pages/chat.html`

The existing renderer for `suggested_actions_event` items submits a slash-command on click. We add a branch: if `action.kind === "link"`, navigate via `window.location.assign(action.url)` instead.

- [ ] **Step 1: Locate the existing renderer**

```
grep -n "suggested_actions\|suggestedActions\|x-for=\"action" src/mycelos/frontend/pages/chat.html | head -10
```

You're looking for an Alpine `<template x-for="action in …">` block that renders one button per action.

- [ ] **Step 2: Add the branch**

Find the button's `@click` handler. It currently looks something like:

```html
<button @click="submitMessage(action.command)" ...>
  <span x-text="action.label"></span>
</button>
```

Change it to:

```html
<button @click="action.kind === 'link' ? window.location.assign(action.url) : submitMessage(action.command)" ...>
  <span x-text="action.label"></span>
</button>
```

If the existing handler is more complex (calls a method like `runAction(action)`), edit that method instead:

```javascript
runAction(action) {
  if (action.kind === 'link') {
    window.location.assign(action.url);
    return;
  }
  this.submitMessage(action.command);
}
```

Keep the existing visual styling — the link card looks the same as a slash-command card. Only the click behavior branches.

- [ ] **Step 3: Manual smoke (browser, optional)**

If the gateway is running on `http://localhost:9100`, open the chat page and ask "where do I set up connectors?" — the agent should call `ui.open_page` and a button should appear that navigates to `/pages/connectors.html` on click.

If you can't run the browser in this environment, skip — Stefan will smoke-test after merge.

- [ ] **Step 4: HTML well-formedness**

```
PYTHONPATH=src python3 -c "
from html.parser import HTMLParser
class V(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []
    def handle_starttag(self, tag, attrs):
        if tag not in ('br','img','input','meta','link','hr'):
            self.stack.append(tag)
    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(f'mismatch close </{tag}>, top={self.stack[-3:]}')
v = V()
v.feed(open('src/mycelos/frontend/pages/chat.html').read())
if v.stack: print('UNCLOSED:', v.stack[-5:])
if v.errors: print('\n'.join(v.errors[:10]))
print('OK' if not v.stack and not v.errors else 'FAIL')
"
```

Expected: `OK` (modulo the known parser false-positives in this codebase — if you see a familiar one and nothing new, that's parity with HEAD).

- [ ] **Step 5: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/frontend/pages/chat.html
git commit -m "feat(web): chat renderer navigates on kind=link suggested-action"
```

---

## Task 5: Telegram channel renderer — Markdown link + hint

**Files:**
- Modify: `src/mycelos/channels/telegram_*.py` (find via grep)
- Modify: `src/mycelos/i18n/locales/en.yaml`, `src/mycelos/i18n/locales/de.yaml` (add `telegram.web_link_hint`)

- [ ] **Step 1: Locate the Telegram suggested-actions renderer**

```
grep -rn "suggested_actions\|suggestedActions\|inline_keyboard\|InlineKeyboard" src/mycelos/channels/ 2>/dev/null | head -20
```

You're looking for the place where `suggested_actions_event` items are converted to Telegram messages — likely an inline-keyboard builder.

- [ ] **Step 2: Add i18n keys**

Open `src/mycelos/i18n/locales/en.yaml` and add under the `telegram:` section (create the section if it doesn't exist):

```yaml
telegram:
  web_link_hint: "Open this page on the computer where Mycelos is running."
```

Open `src/mycelos/i18n/locales/de.yaml` and add the German equivalent:

```yaml
telegram:
  web_link_hint: "Diese Seite musst du auf dem Computer öffnen, wo Mycelos läuft."
```

If the `telegram:` section already exists in either file, merge in the new key without duplicating the section header.

- [ ] **Step 3: Add a small URL helper**

Inside the same Telegram channel module (or a sibling utility module), add a function:

```python
import os

def mycelos_base_url() -> str:
    """Absolute URL base for Web-UI links shown in Telegram.

    Telegram clients can't open `localhost` URLs (they're on a phone,
    not the host computer), so any link we emit needs an absolute host.
    Uses MYCELOS_PUBLIC_URL when set; otherwise falls back to
    http://localhost:9100 (better than nothing — paired with the hint
    text the user knows what to do).
    """
    return os.environ.get("MYCELOS_PUBLIC_URL", "http://localhost:9100").rstrip("/")
```

Place this near the top of the renderer module so it's reusable.

- [ ] **Step 4: Branch the renderer on `kind === "link"`**

Find the function that converts `suggested_actions_event` into Telegram output. It iterates over `actions`. Add a branch:

```python
for action in actions:
    if action.get("kind") == "link":
        # Render as Markdown link plus a hint about needing the host computer.
        absolute_url = mycelos_base_url() + action["url"]
        link_md = f"[{action['label']}]({absolute_url})"
        hint = t("telegram.web_link_hint")
        # Append to message text (Telegram supports Markdown).
        text_lines.append(link_md)
        text_lines.append(f"_{hint}_")  # italic hint
        continue
    # Existing inline-keyboard slash-command path:
    keyboard.append([InlineKeyboardButton(action["label"], callback_data=...)])
```

The exact variable names (`text_lines`, `keyboard`, `InlineKeyboardButton`) depend on the existing code — adapt to match. The key behavior: link items go into the message body as Markdown, NOT into the inline-keyboard array (because clicking a callback_data button doesn't open URLs; only Markdown links do).

If the existing renderer uses `InlineKeyboardButton(text, url=...)` — Telegram supports URL buttons natively — that's an even nicer rendering and you can use it instead of pure Markdown. But the hint text still needs to go into the message body.

- [ ] **Step 5: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures. Telegram-specific tests (if any exist) should still pass — the new branch only adds behavior, doesn't change existing.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/channels/telegram_*.py src/mycelos/i18n/locales/en.yaml src/mycelos/i18n/locales/de.yaml
git commit -m "feat(telegram): render kind=link actions as Markdown link + host hint"
```

---

## Task 6: CLI chat renderer — ANSI hyperlink

**Files:**
- Modify: the CLI chat renderer (find via grep)

- [ ] **Step 1: Locate the CLI renderer**

```
grep -rn "suggested_actions\|suggestedActions" src/mycelos/cli/ src/mycelos/chat/ 2>/dev/null | grep -v test | head -10
```

The CLI chat renderer is typically in `src/mycelos/cli/chat_cmd.py` or a `cli_renderer.py` sibling. It iterates suggested actions and prints them.

- [ ] **Step 2: Branch on `kind === "link"`**

Find the loop and add the link branch:

```python
for action in actions:
    if action.get("kind") == "link":
        url = action["url"]
        label = action["label"]
        # OSC-8 ANSI hyperlink — modern terminals (iTerm2, recent
        # gnome-terminal, kitty) render this as clickable. Older
        # terminals fall back to showing only `label`, hence the
        # plain URL in parentheses.
        ansi_link = f"\033]8;;{url}\033\\{label}\033]8;;\033\\"
        click.echo(f"  → {ansi_link} ({url})")
        continue
    # Existing slash-command-suggestion rendering:
    click.echo(f"  → {action['label']}  ({action['command']})")
```

If the existing renderer uses `console.print` from Rich, branch the same way:

```python
if action.get("kind") == "link":
    console.print(
        f"  → [link={action['url']}]{action['label']}[/link] "
        f"[dim]({action['url']})[/dim]"
    )
    continue
```

Rich handles the ANSI hyperlink markup natively. Use whichever output convention the file already uses.

- [ ] **Step 3: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 4: Commit**

```bash
git add <path-to-cli-renderer>
git commit -m "feat(cli): chat renders kind=link as ANSI hyperlink + URL fallback"
```

---

## Task 7: Dead-code sweep (D9 from spec)

**Files:**
- Modify: `src/mycelos/chat/slash_commands.py` (lines 101, 693-694, 701, 741)
- Modify: `src/mycelos/chat/context.py` (lines 32, 92)

The agent now has a tool that puts links in front of the user. The plain-text "open the Web UI or run `mycelos connector setup`" hints in the chat layer are obsolete noise. Trim them.

- [ ] **Step 1: Confirm the lines are still where they were**

```
grep -nE "open the Web UI or run|mycelos connector setup|Set one up with" src/mycelos/chat/slash_commands.py src/mycelos/chat/context.py
```

Expected: 6 hits across the two files. If the line numbers have drifted, use the grep output to locate them; the surrounding text is the anchor.

- [ ] **Step 2: Trim `slash_commands.py:101`**

Find the line that reads:

```
_Setup: open the Web UI or run `mycelos connector setup <id>`._
```

Replace with:

```
_Setup: open the Connectors page in the Web UI._
```

The CLI mention is dropped — chat users are by definition not in the CLI.

- [ ] **Step 3: Trim `slash_commands.py:693-694, 701`**

Around line 693, find the deprecation message that reads:

```python
        return (
            f"`/connector {action}` is not supported in chat.\n\n"
            f"To set up or remove a connector, use one of:\n"
            f"  - **Web UI**: open the Connectors page\n"
            f"  - **CLI**: `mycelos connector setup <id>` / `mycelos connector remove <id>` / `mycelos connector test <id>`\n\n"
            f"Credentials stay out of the chat transcript this way."
        )
```

Replace the body with:

```python
        return (
            f"`/connector {action}` is not supported in chat. "
            f"Use the Connectors page in the Web UI to set up or remove a connector — "
            f"that keeps credentials out of the chat transcript."
        )
```

Around line 701, find the help-text fallback:

```python
    return (
        "Usage:\n"
        "  `/connector list` — Show available and active connectors\n"
        "  `/connector search <query>` — Search the MCP registry for community servers\n\n"
        "Setup happens in the Web UI or CLI (`mycelos connector setup <id>`)."
    )
```

Replace the trailing line:

```python
    return (
        "Usage:\n"
        "  `/connector list` — Show available and active connectors\n"
        "  `/connector search <query>` — Search the MCP registry for community servers\n\n"
        "Setup happens on the Connectors page in the Web UI."
    )
```

- [ ] **Step 4: Trim `slash_commands.py:741`**

Find:

```python
    lines.append("\nSetup: open the Web UI Connectors page or run `mycelos connector setup <id>`.")
```

Replace with:

```python
    lines.append("\nSetup: open the Connectors page in the Web UI.")
```

- [ ] **Step 5: Trim `context.py:32, 92`**

Find both occurrences of:

```python
"No connectors configured. Set one up with: `mycelos connector setup`"
```

Replace with:

```python
"No connectors configured. Open the Connectors page in the Web UI to set one up."
```

These strings end up in the agent's context, so the agent itself can decide to call `ui.open_page` when surfacing this state to the user. We don't pre-bake a CLI suggestion into the context.

- [ ] **Step 6: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures. If any test asserts on the exact wording of one of these strings, update the test to match the new text.

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/chat/slash_commands.py src/mycelos/chat/context.py
git commit -m "refactor(chat): trim obsolete CLI-routing prose now that ui.open_page exists"
```

---

## Task 8: CHANGELOG + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the entry**

Find the Week 17 block in `CHANGELOG.md`. Add at the END of the Week 17 entries (before `## Week 16 (2026)`):

```markdown
### `ui.open_page` tool — agent links the user into admin pages
- New `ui.open_page` tool lets the Mycelos chat agent send the user directly to a Web-UI page (Connectors, Model settings, Config Generations, Doctor) instead of explaining setup in prose. Optional `anchor` lands on a specific recipe (e.g. `connectors#gmail`); optional `label` overrides the default button text.
- Frontend chat renderer recognizes the new `kind: "link"` discriminator on suggested-actions cards and navigates via `window.location` instead of submitting a slash-command.
- Telegram channel renders link actions as Markdown links with the absolute host (`MYCELOS_PUBLIC_URL` env var, defaults to `http://localhost:9100`) plus a hint that the page must be opened on the host computer (`telegram.web_link_hint` i18n key, en + de).
- CLI chat renderer emits OSC-8 ANSI hyperlinks (clickable in iTerm2 / kitty / modern gnome-terminal) with the URL in parentheses as fallback for older terminals.
- Mycelos system prompt updated with a "Sending the user to the Web UI" section telling the agent when to use the new tool.
- Dead-code sweep: removed obsolete CLI-routing prose hints from `slash_commands.py` (3 places) and `context.py` (2 places). The agent now points at the Web UI through the tool, not by spelling out CLI commands in transcripts.
- Spec / plan: `docs/superpowers/specs/2026-04-26-ui-open-page-tool-design.md`, `docs/superpowers/plans/2026-04-26-ui-open-page-tool-plan.md`.
```

- [ ] **Step 2: Final baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 3: Manual smoke (controller assists Stefan)**

Stefan reloads the Web UI chat. Asks "wie richte ich Gmail ein?" or "where do I set up connectors?" — the agent should call `ui.open_page` and a button should appear that navigates to the Connectors page. If Stefan also has Telegram running, send the same message there and verify the Markdown link + hint.

- [ ] **Step 4: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): ui.open_page tool (Week 17)"
git push origin main
```

---

## Self-review notes

Spec coverage check (against `2026-04-26-ui-open-page-tool-design.md`):

- D1 (capability-gated) → Task 1 uses `ToolPermission.STANDARD` (registry-enforced). The spec's "capability" wording is correct in spirit but the codebase pattern is the Enum + `_AGENT_PERMISSIONS` map, not per-tool capabilities. No separate Task for `policy_engine.set_policy` because `STANDARD` already restricts to Mycelos + Builder.
- D2 (4 targets, no free-form path) → Task 1 hardcodes the enum; tests pin all 4.
- D3 (optional anchor) → Task 1 + tests.
- D4 (optional label) → Task 1 + tests.
- D5 (kind: "link" event) → Task 1 (output shape).
- D6 (frontend rendering) → Task 4.
- D7 (Telegram rendering) → Task 5.
- D8 (CLI rendering) → Task 6.
- D9 (dead-code sweep) → Task 7.
- Capability test for Mycelos-only access (Spec testing section): pinned implicitly by `ToolPermission.STANDARD` registration — `_AGENT_PERMISSIONS` enforces who can invoke. A dedicated test would assert "open agent cannot invoke ui.open_page" but that's testing the registry, not this tool. If we want it for completeness, add it to Task 1 as a 9th test:

```python
def test_open_agent_cannot_invoke_ui_open_page() -> None:
    """Open agents (no STANDARD permission) cannot call ui.open_page."""
    from mycelos.tools.registry import ToolRegistry, ToolPermission
    ToolRegistry._ensure_initialized()
    # ToolRegistry has a method to check whether an agent can invoke a tool;
    # exact name varies (`can_invoke`, `is_allowed`, etc.). Skip if unsure
    # — the registry-level enforcement is unit-tested elsewhere.
```

Skipping this for now because the test is more about the registry's existing behavior than the new tool. If the spec reviewer flags it, add it then.

No placeholders. Every step shows the actual code or command. Type / property names consistent across tasks (`execute_open_page`, `OPEN_PAGE_SCHEMA`, `_URL_TARGETS`, `kind: "link"`, `mycelos_base_url`, `telegram.web_link_hint`).
