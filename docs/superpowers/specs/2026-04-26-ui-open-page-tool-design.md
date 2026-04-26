# `ui.open_page` Tool — Design

**Date:** 2026-04-26
**Status:** Draft
**Scope:** Give the chat agent a way to send the user directly to a Web-UI admin page instead of explaining setup steps in prose.

## Problem

Today when a user asks "how do I set up Gmail?" the agent has to:

- write a multi-step explanation
- mention vague navigation hints ("open the Connectors page, click Setup on Gmail, …")

In Telegram this is even worse — the user can't see the Mycelos UI from their phone, so the agent ends up describing things that aren't there.

The previous spec series surfaced this pattern repeatedly: chat-side connector setup was removed because credentials in transcripts violate Constitution Rule 4, but we replaced it with prose explanations rather than active routing. Time to fix.

## Goal

The agent calls one tool — `ui.open_page` — and the user gets a clickable card / link straight into the right admin page, with optional anchor for sub-targets (e.g. "the Gmail card on the Connectors page").

## Decisions

### D1: Capability-gated tool, not built-in

`ui.open_page` is a regular tool registered in the tool-registry with `ToolPermission(capability="ui.open_page")`. At init time, the Mycelos system agent gets the policy `policy_engine.set_policy("default", "mycelos", "ui.open_page", "always")`. Custom agents and persona agents don't get the capability by default — they can request it later if useful (Constitution Rule 5: capability scoping).

### D2: Targets — explicit allow-list, not free-form path

The tool accepts a `target: str` from a fixed enum. No free-form `path` escape-hatch — YAGNI for now and prevents the agent from inventing URLs that don't exist or pointing at endpoints we never intended.

Allowed targets:

| `target`                 | URL                                  | Use case                                                    |
| ------------------------ | ------------------------------------ | ----------------------------------------------------------- |
| `connectors`             | `/pages/connectors.html`             | Connector setup (most common — credentials, OAuth, etc.)    |
| `settings_models`        | `/pages/settings.html#models`        | LLM model assignments per agent / system defaults           |
| `settings_generations`   | `/pages/settings.html#generations`   | Config generations / rollback UI                            |
| `doctor`                 | `/pages/doctor.html`                 | Diagnostic page when something doesn't work                 |

Memory, Knowledge, Workflows, Agents pages are intentionally NOT targets — they're either content views (memory, knowledge) or chat-driven flows (workflows, agents). Linking to them adds noise without action value.

### D3: Optional `anchor` for sub-targets

The tool also accepts `anchor: str | None`. If set, it's appended to the URL with `#`, replacing any anchor already in the URL mapping. Example:

```
ui.open_page(target="connectors", anchor="gmail")
→ "/pages/connectors.html#gmail"

ui.open_page(target="settings_models", anchor="provider-anthropic")
→ "/pages/settings.html#provider-anthropic"  (overrides the default #models anchor)
```

The Connectors page already supports anchor-based highlighting (see `jumpToInstalled` style — though we removed that specific function in a prior cleanup, the page still uses `id="connector-card-<id>"` for installed cards). For un-installed recipes, the page would need an `id` per recipe card if we want anchor-jumping to recipe cards too — but this is a frontend nicety we can add lazily; even an unmatched anchor just leaves the user on the right page.

### D4: Optional `label` for the link text

`label: str | None`. If absent, a sensible default per target. The agent overrides when it has more context:

```
ui.open_page(target="connectors", anchor="gmail", label="Set up Gmail")
→ button text: "Set up Gmail"

ui.open_page(target="connectors")
→ button text: "Open Connectors page"
```

Defaults:

| `target`               | Default label              |
| ---------------------- | -------------------------- |
| `connectors`           | "Open Connectors page"     |
| `settings_models`      | "Open Model settings"      |
| `settings_generations` | "Open Config Generations"  |
| `doctor`               | "Open Doctor"              |

### D5: Output is a `suggested_actions_event` with `kind: "link"`

The tool returns the existing event shape used by the chat layer, with one new discriminator:

```python
suggested_actions_event([
    {"label": "Set up Gmail", "url": "/pages/connectors.html#gmail", "kind": "link"}
])
```

Existing event consumers that handle `suggested_actions_event` keep working — they just need to learn the new `kind`. Today's Actions are typed as slash-command suggestions (`{label, command}`); the new shape uses `{label, url, kind: "link"}`.

The agent typically pairs the tool call with a short text response like "Sure — open this:" so the user sees both the answer and the action.

### D6: Frontend rendering — Web UI

The chat-page renderer (`src/mycelos/frontend/pages/chat.html`) currently treats `suggested_actions_event` items as slash-commands: clicking the button submits the `command`. For the new `kind: "link"` items, the click instead does `window.location.href = action.url` (same window — we want navigation, not popup).

The button visual stays the same (existing card style); only the click handler branches.

### D7: Telegram rendering

The Telegram channel renderer detects `suggested_actions_event` items with `kind: "link"`. Instead of an inline-keyboard slash-command button (which would call back into Mycelos), it renders a Markdown link plus a hint:

```
Klar, öffne diese Seite:
[Set up Gmail](http://192.168.1.42:9100/pages/connectors.html#gmail)

(Du musst diese Seite auf dem Computer öffnen, wo Mycelos läuft.)
```

The absolute URL is built from a `mycelos_base_url()` helper that reads `MYCELOS_PUBLIC_URL` env var (if set) or falls back to `http://localhost:9100`. The hint line is always added because `localhost` URLs are not reachable from the user's phone — even with `MYCELOS_PUBLIC_URL` set to a LAN IP, the user might not be on the same network.

The hint text is i18n'd (`telegram.web_link_hint`). Multi-language support comes for free via the existing `t()` mechanism.

### D8: CLI rendering

The CLI chat (`mycelos chat`) renders `kind: "link"` items as ANSI hyperlinks (modern terminals like iTerm2 honor them) with the URL as a fallback for terminals that don't:

```
Open this:
  → Set up Gmail (http://localhost:9100/pages/connectors.html#gmail)
```

Most users on the CLI are on the same machine where Mycelos runs, so localhost URLs work fine.

### D9: Dead-code sweep — remove prose-routing strings

After `ui.open_page` ships, the agent shouldn't need to spell out "open the Web UI or run `mycelos connector setup`" in plain text anymore. We remove (or rewrite to call the tool) every place that does so today:

- `src/mycelos/chat/slash_commands.py:101` — `_Setup: open the Web UI or run …_` help-text line in the `/connector list` output. Replace the text with a short pointer; the agent itself uses `ui.open_page` for actual setup requests.
- `src/mycelos/chat/slash_commands.py:693-694, 701` — the deprecation message returned for `/connector add|setup|remove|test`. Keep the deprecation but trim the prose to just "Use the Connectors page in the Web UI." Without the duplicate `mycelos connector setup` mention. The user who hits this in chat is by definition not in the CLI; pointing them at the CLI is noise.
- `src/mycelos/chat/slash_commands.py:741` — same pattern in `_connector_list`'s footer line. Trim.
- `src/mycelos/chat/context.py:32, 92` — "No connectors configured. Set one up with: `mycelos connector setup`" — drop the CLI suggestion. The agent receiving this context can call `ui.open_page` itself if it wants to surface a setup link to the user.
- Mycelos system prompt — any existing line that tells the agent to "explain how to set up X via the Web UI" gets replaced by the new `ui.open_page` usage section (D6 / Components).

This is not about removing the legitimate CLI commands (they stay — Stefan uses them). It's about removing the prose hints in the chat layer that the agent no longer needs because it has a tool now.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ src/mycelos/tools/ui.py (NEW, ~80 lines)                │
│   ui.open_page tool definition                          │
│   register() with ToolPermission(capability="ui.open_page") │
│   URL_TARGETS mapping, DEFAULT_LABELS                   │
│   builds suggested_actions_event with kind: "link"      │
└─────────────────────────────────────────────────────────┘
                           ▲
                           │ imported by
┌─────────────────────────────────────────────────────────┐
│ src/mycelos/tools/registry.py                           │
│   _ensure_initialized() imports `from mycelos.tools import ui` │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ src/mycelos/cli/init_cmd.py                             │
│   policy_engine.set_policy("default", "mycelos",        │
│                             "ui.open_page", "always")   │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ src/mycelos/agents/handlers/mycelos.py (or system prompt) │
│   prompt addendum: "use ui.open_page when …"            │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ Frontend / Telegram / CLI renderers                     │
│   handle kind: "link" in suggested_actions_event        │
└─────────────────────────────────────────────────────────┘
```

## Components

### `src/mycelos/tools/ui.py`

```python
"""ui.open_page — let the agent send the user to a specific admin page.

Returns a suggested-actions event with a single link the user can click
to navigate. Used when the user asks to set up / configure / inspect
something that lives in the Web UI.
"""

from __future__ import annotations

from typing import Any

from mycelos.chat.events import suggested_actions_event, system_response_event
from mycelos.tools.permissions import ToolPermission

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


SCHEMA = {
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


def execute(args: dict[str, Any], app: Any) -> list:
    """Build the link event. Pure function — no side effects."""
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
        # Replace any existing anchor with the explicit one.
        base, _, _ = url.partition("#")
        url = f"{base}#{anchor}"

    label = (args.get("label") or "").strip() or _DEFAULT_LABELS[target]

    return [
        suggested_actions_event([
            {"label": label, "url": url, "kind": "link"},
        ])
    ]


def register(registry: Any) -> None:
    """Register ui.open_page with the tool registry."""
    registry.register(
        SCHEMA,
        execute,
        permission=ToolPermission(capability="ui.open_page"),
    )
```

### `src/mycelos/tools/registry.py`

In `_ensure_initialized()`, add:

```python
    from mycelos.tools import ui as _ui_tools
    _ui_tools.register(_REGISTRY)
```

### `src/mycelos/cli/init_cmd.py`

After Mycelos system agent is registered, set the policy:

```python
app.policy_engine.set_policy(
    user_id="default",
    agent_id="mycelos",
    capability="ui.open_page",
    decision="always",
)
```

(The exact location depends on existing init_cmd structure — find where other Mycelos-specific policies are set and add this line alongside.)

### Mycelos system prompt

Wherever the Mycelos handler's prompt is composed (likely `src/mycelos/agents/handlers/mycelos.py` or a prompt-template file), add a section:

```
## Sending the user to the Web UI

When the user asks to set up, configure, or inspect something that lives in the Web UI, use the `ui.open_page` tool to give them a clickable link instead of explaining the steps in prose. Targets:

- `connectors` (with optional `anchor` like `gmail`, `github`) — connector setup, OAuth, credentials per service
- `settings_models` — LLM model assignments per agent or system defaults
- `settings_generations` — config rollback UI
- `doctor` — diagnostic page when something isn't working

Pair the tool call with a short text response so the user sees both the answer and the action card. Don't enumerate setup steps yourself — the page does it better.
```

### Frontend — `chat.html`

Find the existing `suggested_actions_event` renderer (Alpine x-for over actions). Today it likely does something like:

```javascript
@click="submitMessage(action.command)"
```

Branch on `action.kind`:

```javascript
@click="action.kind === 'link' ? window.location.assign(action.url) : submitMessage(action.command)"
```

Or factor the handler into a method `handleAction(action)` for readability. Button visual stays the same.

### Telegram — channel renderer

Find the place where `suggested_actions_event` items are converted into Telegram messages. Branch on `action.kind`:

- `kind === "link"`: render as Markdown `[label](absolute-url)`, append a hint line (i18n'd) about needing to open this on the host computer.
- otherwise: existing inline-keyboard button behavior.

Absolute URL via a small helper:

```python
def mycelos_base_url() -> str:
    return os.environ.get("MYCELOS_PUBLIC_URL", "http://localhost:9100")
```

Hint string i18n key: `telegram.web_link_hint` → "Open this page on the computer where Mycelos is running."

### CLI — `mycelos chat`

The CLI renderer for `suggested_actions_event` (in `mycelos/chat/cli_renderer.py` or similar) detects `kind === "link"` and prints with ANSI hyperlinks:

```
\x1b]8;;<url>\x1b\\<label>\x1b]8;;\x1b\\
```

Plus the URL in plain text in parentheses for terminals that don't honor escape codes.

## Data Flow

```
User (in Telegram): "wie richte ich Gmail ein?"
  ↓
Mycelos agent processes the message
  ↓
LLM decides to call ui.open_page(target="connectors", anchor="gmail",
                                  label="Gmail einrichten")
  ↓
tools/ui.py execute() returns:
  [suggested_actions_event([
     {"label": "Gmail einrichten",
      "url": "/pages/connectors.html#gmail",
      "kind": "link"}
   ])]
  ↓
ChatService dispatches the events:
  - The LLM's text response goes out as system_response_event
  - The action event goes out as suggested_actions_event
  ↓
Telegram renderer sees kind: "link", builds:
  Klar, öffne diese Seite:
  [Gmail einrichten](http://localhost:9100/pages/connectors.html#gmail)
  (Diese Seite musst du auf deinem Computer öffnen.)
  ↓
User clicks (on phone) → either localhost works (he's on the host) or
  not (he's on his phone). The hint warned him.
```

## Error Handling

- **Unknown target**: Tool returns a `system_response_event` with the allowed list. Better than crashing.
- **Empty/missing target**: Same — schema validation should catch it (`required: ["target"]`), but defensive code returns the error event too.
- **Capability missing**: The capability check is upstream of `execute` — agent without `ui.open_page` capability never gets here. If somehow it does, the tool framework's error path takes over.
- **Telegram MYCELOS_PUBLIC_URL unset**: Fall back to `http://localhost:9100`. Hint text covers the gap.

## Testing

Unit tests in `tests/test_ui_open_page.py`:

- `target` "connectors" without anchor → URL is `/pages/connectors.html`
- `target` "connectors" with anchor "gmail" → URL is `/pages/connectors.html#gmail`
- `target` "settings_models" with no anchor → URL keeps the default `#models`
- `target` "settings_models" with anchor "provider-anthropic" → URL is `/pages/settings.html#provider-anthropic` (overrides default)
- Unknown `target` → returns a system_response_event with the allowed list
- Custom `label` → respected
- No `label` → uses default per target
- Returned event has `kind: "link"` discriminator

Capability test in `tests/security/test_ui_open_page_capability.py`:

- Mycelos agent has `ui.open_page` capability after init
- Custom agent without explicit grant cannot call the tool

Manual verification (after merge):

1. In the Web UI chat, ask "where do I set up connectors?" — agent calls the tool, button appears, click navigates to the page.
2. In Telegram, ask the same — Markdown link with hint appears.
3. In CLI chat, same — ANSI hyperlink with URL fallback.

## Success Criteria

1. `src/mycelos/tools/ui.py` exists with the `ui.open_page` tool.
2. Tool registered via `tools/registry.py`.
3. `init_cmd.py` grants `ui.open_page` to Mycelos agent.
4. Mycelos system prompt mentions the tool with usage guidance.
5. Frontend chat renderer handles `kind: "link"` actions (navigate, not submit).
6. Telegram renderer handles `kind: "link"` actions (Markdown link + hint).
7. CLI renderer handles `kind: "link"` actions (ANSI hyperlink + plain URL).
8. Unit tests cover all 4 targets + edge cases.
9. Capability test pins Mycelos-only access.
10. Dead-code sweep complete (D9): no remaining prose-routing strings in `slash_commands.py` and `context.py` that point users at the Web UI in plain text.
11. CHANGELOG entry under Week 17 (or 18 if we cross the boundary).

## Non-Goals

- Free-form `path` parameter — explicit enum only.
- Adding new admin pages (Memory / Knowledge / Workflows / Agents as targets) — those are content / chat-driven, not config destinations.
- Capability granting for non-Mycelos agents — they can request later if a use case emerges.
- Custom URL host detection (LAN IP, mDNS, …) — `MYCELOS_PUBLIC_URL` env var is enough for now.
- Anchor-jumping for un-installed recipe cards on the Connectors page — even unmatched anchor lands the user on the right page; per-recipe-card `id` attributes are a frontend follow-up if needed.
