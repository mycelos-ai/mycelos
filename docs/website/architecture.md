---
title: Architecture
description: Two-container deployment with strict separation between the user-facing gateway and the credential-holding proxy.
order: 2
icon: layers
---

Mycelos follows a layered architecture with strict separation of concerns. Security is a cross-cutting layer, not an afterthought.

## Layer overview

```
Channel Layer       Terminal | Telegram | Web UI
Control Layer       Mycelos (primary chat) | Builder | Evaluator | Optimizer
Execution Layer     Sandbox | CLI Runtime | MCP subprocess manager
Security Layer      Credentials | Capabilities | Policies | Guardian | SSRF guard
Storage Layer       SQLite WAL | Filesystem | Object store
```

## Two-container deployment (the default since v0.3)

`docker compose up -d` launches two containers on an isolated internal Docker network:

```
┌────────────────────┐           ┌────────────────────┐
│  mycelos-gateway   │           │   mycelos-proxy    │
│                    │  bearer   │                    │
│ • Web UI + REST    │──────────▶│ • holds master key │
│ • Chat + SSE       │  shared   │ • LLM brokerage    │
│ • Scheduler        │  secret   │ • MCP subprocess   │
│ • Audit writes     │           │   manager          │
│                    │           │ • SSRF-safe HTTP   │
│ NO internet route  │           │ • Credential store │
└────────────────────┘           └────────────────────┘
       │                                  │
       └──────── mycelos-internal ────────┘
                (Docker bridge, no egress)
                         │
                   host 127.0.0.1
                    (published port)
```

- **`mycelos-gateway`** serves the web UI, chat, and REST API. It has **no direct internet route** (Phase 1b removed the default bridge network). It cannot decrypt the credentials table.
- **`mycelos-proxy`** holds `.master_key` (read-only bind-mount), encrypts / decrypts credentials, brokers every outbound request, and runs MCP subprocesses in its own PID namespace.
- The gateway talks to the proxy over HTTP on the internal network, authenticated with a shared bearer token written to `.env` at install time.

This is the model documented in [`docs/security/two-container-deployment.md`](https://github.com/mycelos-ai/mycelos/blob/main/docs/security/two-container-deployment.md). It is what `scripts/install.sh` sets up and what the default `docker-compose.yml` describes.

## Single-process (source-install) mode

For development, `mycelos serve` without Docker runs gateway + proxy in the same process (`--role all`). The master key is loaded directly into the server; the isolation guarantees above do not apply. This is fine for contributing to Mycelos; it is not the recommended way to run it for real use.

## Multi-channel

Terminal, Telegram, and the web UI all connect to the same backend. Sessions, memory, credentials, and the audit trail are shared across channels. A reminder set from Telegram fires through the gateway's scheduler and is visible in the web UI's inbox.

## CLI mode

`mycelos chat` inside the gateway container talks to the same chat service as the web UI — same session store, same memory scopes, same tools. The CLI is useful for quick debugging without a browser, not a separate code path.
