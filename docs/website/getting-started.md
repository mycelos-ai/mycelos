---
title: Getting Started
description: Install Mycelos in under a minute with the Docker installer, then open the web UI.
order: 1
icon: rocket_launch
---

## Prerequisites

- **Docker** with Compose v2 (`docker compose` — not the legacy `docker-compose`). Install from [docs.docker.com/get-docker](https://docs.docker.com/get-docker/).
- **API key** from any supported LLM provider (Anthropic, OpenAI, Google), or a local Ollama endpoint.

Nothing else needs to be on the host — Python, Node.js, and every runtime dependency ship inside the container image.

## Install

One command, no questions:

```bash
curl -fsSL https://raw.githubusercontent.com/mycelos-ai/mycelos/main/scripts/install.sh | bash
```

The installer:

- creates `./data/` with a freshly generated `.master_key` (or keeps yours if one already exists)
- writes `.env` with a random `MYCELOS_PROXY_TOKEN`
- fetches the latest `docker-compose.yml`
- pulls `ghcr.io/mycelos-ai/mycelos:main`
- launches the two-container stack (`mycelos-gateway` + `mycelos-proxy`)
- installs a `~/.local/bin/mycelos` CLI shortcut with tab-completion

Windows: use `scripts/install.ps1` (same flags).

## First run

Open **http://localhost:9100** in your browser. You'll land on a setup wizard that asks for your LLM API key — nothing is stored until you submit it, and the key is encrypted with the master key that lives only in the proxy container.

## Using the CLI

The installer dropped a wrapper into `~/.local/bin/mycelos` that targets your install's stack. Most subcommands forward into the gateway container; a handful of host-level operations run locally:

```bash
mycelos doctor              # health check
mycelos config list         # show config generations
mycelos update              # pull latest image + recreate containers
mycelos restart             # docker compose restart
mycelos logs -f             # follow gateway + proxy logs
mycelos shell               # drop into a bash shell inside the gateway
```

If `~/.local/bin` isn't on your `$PATH`, the installer prints the one-line fix for your shell. Tab-completion for bash, zsh, and fish is installed as well — reopen your shell to activate it.

## Update

Re-run the same install command any time you want to pull a new release:

```bash
curl -fsSL https://raw.githubusercontent.com/mycelos-ai/mycelos/main/scripts/install.sh | bash
```

It's idempotent: `.master_key` and `.env` are preserved, `docker-compose.yml` is refreshed (old version backed up if it changed), and the latest image is pulled.

## Exposing Mycelos beyond localhost

The gateway binds to `127.0.0.1` by default. v0.3 has HTTP Basic Auth (`MYCELOS_PASSWORD` in `.env`) but not yet passkey authentication — that's Phase 2.

If you need LAN or internet access in the meantime:

1. Set `MYCELOS_PASSWORD=…` in `.env` (a long random string).
2. Set `MYCELOS_BIND=0.0.0.0` in `.env`.
3. Put a TLS-terminating reverse proxy in front (Caddy, nginx, Traefik, Tailscale serve, Cloudflare tunnel, …) — Mycelos itself does not terminate TLS.
4. `mycelos restart`.

If you're not sure, leave it on localhost.

## Development (source install)

Only needed if you're contributing or want to run without Docker:

```bash
git clone https://github.com/mycelos-ai/mycelos.git
cd mycelos
pip install -e ".[dev]"
mycelos init
mycelos serve
```

This is single-process mode: no gateway/proxy split, `.master_key` sits on disk under `~/.mycelos/`, everything runs in one Python process. It's useful for development but does not give you the v0.3 isolation model — use the Docker install for anything resembling production.
