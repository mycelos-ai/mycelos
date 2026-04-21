---
title: CLI Reference
description: Complete reference for the mycelos command-line.
order: 8
icon: terminal
---

In the Docker deployment (the default since v0.3), the `~/.local/bin/mycelos` wrapper installed by `scripts/install.sh` is the CLI entry point. Most subcommands forward into the gateway container; a handful of stack-level operations run on the host.

## Host-side subcommands

These run outside the container. They manage the stack itself and have no equivalent inside the gateway process.

| Command | Description |
|---|---|
| `mycelos update` | `docker compose pull` + `up -d`. Pulls the latest image and restarts the stack. Idempotent. |
| `mycelos restart [service]` | Restart both containers, or just `gateway` / `proxy`. |
| `mycelos logs [service] [-f]` | Follow the gateway + proxy logs (or a single service). |
| `mycelos shell` | Drop into a bash shell inside the gateway container — useful for debugging. |
| `mycelos stop` | Stop the stack (`docker compose stop`). |

## In-container subcommands

These forward to `mycelos` inside the gateway, which resolves `--data-dir` from `MYCELOS_DATA_DIR=/data` — no flag needed from you.

| Command | Description |
|---|---|
| `mycelos doctor` | Health check across storage, credentials, channels, schedules, and available updates. |
| `mycelos doctor --why` | LLM-powered interactive diagnosis. |
| `mycelos doctor --check <area>` | Focused check: `storage`, `credentials`, `telegram`, `reminders`, `schedules`, `update`, `organizer`. |
| `mycelos config list` | List all NixOS-style config generations. |
| `mycelos config show [N]` | Inspect a generation. |
| `mycelos config diff A B` | Diff two generations. |
| `mycelos config rollback [N]` | Roll back to generation N. |
| `mycelos credential list` | List encrypted credentials (metadata only — never plaintext). |
| `mycelos credential store <service> <key>` | Encrypt and store a credential via the proxy. |
| `mycelos credential delete <service>` | Remove a credential. |
| `mycelos connector add <name>` | Register an MCP connector. |
| `mycelos connector list` | Show active connectors. |
| `mycelos model list` | List registered LLM models. |
| `mycelos model test <id>` | Verify a model responds. |
| `mycelos schedule list` | Show scheduled workflow tasks (cron). |
| `mycelos sessions list` | List chat sessions. |
| `mycelos chat` | Interactive chat session in the terminal. |

`mycelos demo`, `mycelos init`, and `mycelos serve` exist but are primarily for the dev/source-install path; in the Docker deployment the containers call them for you during startup.

## Tab completion

`scripts/install.sh` installs static tab-completion for bash, zsh, and fish at install time — no runtime overhead. Completion covers the full command tree including the host-side shortcuts above. Refresh happens automatically the next time you re-run the installer.

## Running the CLI outside the wrapper

If you need to call the CLI from a script without the wrapper, use the compose exec form:

```bash
docker compose -f ~/mycelos-new/docker-compose.yml exec gateway mycelos doctor
```

The `mycelos` wrapper is just a thin shell script around that command with host-side shortcuts bolted on; you can read it at `~/.local/bin/mycelos`.

## Source-install mode

When you install from source (`pip install -e .`), there is no wrapper and no container: `mycelos <subcommand>` invokes the Click entry point directly, and `--data-dir` falls back to `~/.mycelos/`. The in-container subcommands above still apply, but `update` / `restart` / `logs` / `shell` / `stop` are meaningless and not defined.
