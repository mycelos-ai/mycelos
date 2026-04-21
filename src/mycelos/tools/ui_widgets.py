"""UI Widget tools — agent renders forms that bypass the LLM.

These tools emit ChatEvent widgets that the Web UI renders as interactive
forms. Form submissions go directly to API endpoints, never through the LLM.
On CLI/Telegram these fall back to text instructions.
"""

from __future__ import annotations

from typing import Any

from mycelos.tools.registry import ToolPermission

# --- Schemas ---

CONNECTOR_SETUP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "show_connector_setup",
        "description": (
            "Show a connector setup form to the user. The form posts directly to the API, "
            "bypassing the LLM. Use this when the user wants to add a connector (Docker, "
            "Playwright, Email, etc.). Available connectors: email, telegram, github, "
            "playwright, postgres, notion, docker, slack, sentry, linear, chrome-devtools, "
            "puppeteer, brave-search, sqlite, git, google-drive."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "connector_id": {
                    "type": "string",
                    "description": "Connector recipe ID (e.g., 'docker', 'playwright', 'email').",
                },
            },
            "required": ["connector_id"],
        },
    },
}

CREDENTIAL_INPUT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "show_credential_input",
        "description": (
            "Show a secure credential input form. The form posts directly to the API — "
            "the credential value NEVER passes through the LLM. Use this when the user "
            "wants to add an API key for an LLM provider or service."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service name (e.g., 'anthropic', 'openai', 'github').",
                },
                "label": {
                    "type": "string",
                    "description": "Description shown to user (e.g., 'Anthropic API Key').",
                },
            },
            "required": ["service"],
        },
    },
}

# --- Execution ---


def execute_show_connector_setup(args: dict, context: dict) -> Any:
    """Return a widget event for connector setup."""
    from mycelos.connectors.mcp_recipes import get_recipe

    connector_id = args.get("connector_id", "")
    recipe = get_recipe(connector_id)

    if recipe:
        needs_key = bool(recipe.credentials)
        return {
            "__widget__": "connector_setup",
            "connector_id": connector_id,
            "name": recipe.name,
            "description": recipe.description,
            "command": recipe.command,
            "needs_credential": needs_key,
            "credential_env_var": recipe.credentials[0]["env_var"] if needs_key else None,
            "credential_help": recipe.credentials[0].get("help", "") if needs_key else None,
            "endpoint": "/api/connectors",
        }
    else:
        return {
            "__widget__": "connector_setup",
            "connector_id": connector_id,
            "name": connector_id,
            "description": f"Custom MCP connector: {connector_id}",
            "command": "",
            "needs_credential": False,
            "endpoint": "/api/connectors",
            "custom": True,
        }


def execute_show_credential_input(args: dict, context: dict) -> Any:
    """Return a widget event for secure credential input."""
    service = args.get("service", "")
    label = args.get("label", f"{service.title()} API Key")

    return {
        "__widget__": "credential_input",
        "service": service,
        "label": label,
        "endpoint": "/api/credentials",
        "placeholder": "Paste your API key here (never sent to AI)",
    }


def register(registry: type) -> None:
    """Register UI widget tools."""
    registry.register(
        "show_connector_setup",
        CONNECTOR_SETUP_SCHEMA,
        execute_show_connector_setup,
        ToolPermission.STANDARD,
        category="core",
    )
    registry.register(
        "show_credential_input",
        CREDENTIAL_INPUT_SCHEMA,
        execute_show_credential_input,
        ToolPermission.STANDARD,
        category="core",
    )
