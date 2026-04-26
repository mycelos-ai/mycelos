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
    "type": "function",
    "function": {
        "name": "ui.open_page",
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
