"""Slash-command autocomplete for the Mycelos chat REPL.

Uses prompt_toolkit to provide tab-completion for slash commands
and their subcommands, with descriptions shown inline.

IMPORTANT: When adding new slash commands or subcommands in
slash_commands.py, update SLASH_COMMANDS below to keep autocomplete
in sync. This is also noted in CLAUDE.md.
"""

from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

# ---------------------------------------------------------------------------
# Static command registry — keep in sync with slash_commands.py
# ---------------------------------------------------------------------------

SLASH_COMMANDS: dict[str, dict] = {
    "/help": {
        "description": "Show available commands",
        "subs": {},
    },
    "/memory": {
        "description": "Manage persistent memory",
        "subs": {
            "list": "Show all stored entries",
            "search": "Search entries by query",
            "delete": "Delete an entry by key",
            "clear": "Clear all entries",
        },
    },
    "/cost": {
        "description": "Usage & cost tracking",
        "subs": {
            "week": "This week's costs",
            "month": "This month's costs",
            "all": "All-time costs",
        },
    },
    "/sessions": {
        "description": "Session management",
        "subs": {
            "resume": "Resume a previous session by ID",
        },
    },
    "/mount": {
        "description": "Filesystem access",
        "subs": {
            "list": "Show mounted directories",
            "add": "Grant directory access",
            "revoke": "Revoke access by ID",
        },
    },
    "/config": {
        "description": "System configuration",
        "subs": {
            "show": "Current state",
            "rollback": "Rollback to a generation",
        },
    },
    "/agent": {
        "description": "Agent management",
        "subs": {
            "list": "Show all agents",
        },
    },
    "/connector": {
        "description": "Manage external service connectors",
        "subs": {
            "list": "Show available and active connectors",
            "search": "Search the MCP registry for community servers",
        },
    },
    "/schedule": {
        "description": "Cron jobs",
        "subs": {
            "list": "Show scheduled tasks",
            "add": "Add a scheduled task",
        },
    },
    "/workflow": {
        "description": "Workflow management",
        "subs": {
            "list": "Show workflows",
            "runs": "Show active/paused runs",
            "show": "Show workflow details",
            "delete": "Delete a workflow",
        },
    },
    "/model": {
        "description": "LLM models",
        "subs": {
            "list": "Show configured models",
        },
    },
    "/reload": {
        "description": "Reload MCP connectors",
        "subs": {},
    },
    "/demo": {
        "description": "Feature demonstrations",
        "subs": {
            "widget": "Show all widget types",
        },
    },
    "/bg": {
        "description": "Background tasks",
        "subs": {
            "list": "List background tasks",
            "cancel": "Cancel a task",
            "approve": "Approve a waiting task",
            "detail": "Show task details",
        },
    },
    "/inbox": {
        "description": "File inbox",
        "subs": {
            "clear": "Remove all inbox files",
        },
    },
}


class SlashCommandCompleter(Completer):
    """Tab-completion for slash commands and subcommands."""

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Completion:
        text = document.text_before_cursor

        # Only complete when input starts with /
        if not text.startswith("/"):
            return

        parts = text.split()

        if len(parts) == 1 and not text.endswith(" "):
            # Completing the command itself: /de → /demo
            prefix = parts[0].lower()
            for cmd, info in SLASH_COMMANDS.items():
                if cmd.startswith(prefix):
                    yield Completion(
                        cmd,
                        start_position=-len(prefix),
                        display_meta=info["description"],
                    )

        elif len(parts) >= 1:
            # Completing a subcommand: /demo w → widget
            cmd = parts[0].lower()
            info = SLASH_COMMANDS.get(cmd)
            if not info or not info["subs"]:
                return

            sub_prefix = parts[1].lower() if len(parts) > 1 and not text.endswith(" ") else ""
            for sub, desc in info["subs"].items():
                if sub.startswith(sub_prefix):
                    yield Completion(
                        sub,
                        start_position=-len(sub_prefix),
                        display_meta=desc,
                    )
