---
title: Slash Commands
description: Quick-action commands available in the chat input — type / to see autocomplete suggestions.
order: 9
icon: code
---

Use slash commands in the chat input for quick actions. Type `/` to see autocomplete suggestions. Tab cycles through matches.

## Reference

| Command | What it does |
|---|---|
| `/help` | List all available commands with short descriptions. |
| `/memory [list\|summary\|search\|set\|delete\|clear]` | Inspect or edit stored preferences. Defaults to `summary`. |
| `/sessions [list\|show]` | Browse and inspect past chat sessions. |
| `/cost [today\|week\|month]` | LLM token usage and cost breakdown. |
| `/config [list\|show\|rollback]` | View config generations or roll back. |
| `/agent [list\|show\|handoff\|rename]` | Manage agents and hand off the conversation. |
| `/connector [add\|list\|remove\|search\|test]` | Manage MCP connectors and integrations. |
| `/credential [store\|list\|delete]` | Encrypt and store API keys / tokens. |
| `/schedule [list\|add\|delete\|pause\|resume]` | Cron-based workflow scheduling. |
| `/workflow [list\|show\|run]` | Browse and execute registered workflows. |
| `/model [list]` | Registered LLM models and their assignments. |
| `/mount [list\|add\|remove]` | File-system mount points agents are allowed to read or write. |
| `/run <workflow-id>` | Execute a workflow immediately. |
| `/bg [list\|status\|cancel]` | Inspect and cancel background tasks. |
| `/inbox` | Open the task + reminder inbox. |
| `/demo` | Run the 60-second non-interactive demo. |
| `/reload` | Reload config and registered agents without restarting the gateway. |
| `/restart` | Restart the gateway service (leaves the proxy container untouched). |

All slash commands execute in the **gateway container** and are subject to the same capability and policy checks as agent tool calls. Commands that need a credential (e.g. `/connector add telegram …`) route through the SecurityProxy — the gateway never sees plaintext.

## Host-side CLI shortcuts

The `~/.local/bin/mycelos` wrapper (installed by `scripts/install.sh`) also recognizes a few commands that run **on the host** rather than in the chat: `mycelos update`, `restart`, `logs`, `shell`, `stop`. Those are documented in the [CLI reference](/docs/cli-reference), not here — the chat has no access to them.
