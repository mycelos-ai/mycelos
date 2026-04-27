"""ui_open_page — let the agent send the user to a specific admin page.

Returns a suggested-actions event with a single link the user can click
to navigate. Used when the user asks to set up / configure / inspect
something that lives in the Web UI.
"""

from __future__ import annotations

from typing import Any

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
    "type": "function",
    "function": {
        "name": "ui_open_page",
        "description": (
            "Send the user directly to a Web-UI admin page. Use this when "
            "the user asks to set up / configure / inspect something that "
            "requires the Web UI (connector setup, model assignments, "
            "rollback, diagnostics). Don't explain the steps — give them "
            "the link instead."
        ),
        "parameters": {
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
    },
}


def execute_open_page(args: dict[str, Any], context: dict) -> dict:
    """Build a clickable-link tool result.

    Returns a JSON-serializable dict. The chat service detects the
    `__suggested_actions__` marker and emits a `suggested-actions`
    ChatEvent so the frontend renders a clickable card. The LLM gets
    a short confirmation string back as the tool result it can read.
    """
    target = args.get("target", "")
    if target not in _URL_TARGETS:
        return {
            "error": (
                f"Unknown UI target: {target!r}. "
                f"Allowed: {', '.join(sorted(_URL_TARGETS))}."
            )
        }

    url = _URL_TARGETS[target]
    anchor = (args.get("anchor") or "").strip().lstrip("#")
    if anchor:
        # Recipes on the Connectors page are rendered with id="recipe-<id>"
        # to avoid collisions with other section ids on the page. We add
        # the prefix here so the LLM can pass the bare recipe id (its
        # natural mental model) and we still hit the right element.
        if target == "connectors" and not anchor.startswith("recipe-"):
            anchor = f"recipe-{anchor}"
        # Replace any existing default anchor with the explicit one so the
        # caller can target arbitrary sub-sections, not just the default.
        base, _, _ = url.partition("#")
        url = f"{base}#{anchor}"

    label = (args.get("label") or "").strip() or _DEFAULT_LABELS[target]

    return {
        "__suggested_actions__": [
            {"label": label, "url": url, "kind": "link"},
        ],
        "status": "link_shown",
        "url": url,
    }


def register(registry: type) -> None:
    """Register ui_open_page with the tool registry."""
    registry.register(
        "ui_open_page",
        OPEN_PAGE_SCHEMA,
        execute_open_page,
        ToolPermission.STANDARD,
        concurrent_safe=True,
        category="core",  # always-loaded; without this the budget-aware
                          # session loader skips low-call-count tools and
                          # the LLM never sees ui_open_page
    )
