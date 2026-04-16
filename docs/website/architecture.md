---
title: Architecture
description: Layered architecture with strict separation of concerns and a cross-cutting security layer.
order: 2
icon: layers
---

Mycelos follows a layered architecture with strict separation of concerns. Security is a cross-cutting layer, not an afterthought.

## Layer Overview

```
Channel Layer (Terminal, Telegram, Web UI)
Control Layer (Planner, Creator, Evaluator, Optimizer)
Execution Layer (Sandbox, CLI Runtime)
Security Layer (Credentials, Capabilities, Policies, Guardian)
Storage Layer (SQLite WAL + Filesystem)
```

## Gateway Mode

Running `mycelos serve` starts the full Gateway stack:

- **HTTP API** — RESTful endpoints with SSE streaming for chat
- **SecurityProxy** — separate process handling all external network requests
- **Scheduler** — cron-based workflow execution
- **Web UI** — the frontend you are looking at right now

## CLI Mode

Running `mycelos chat` bypasses the Gateway and talks directly to the LLM broker. This is useful for quick interactions without starting the full server.

## Multi-Channel

Terminal, Telegram, and the Web UI all connect to the same backend. Sessions, memory, and audit trails are shared across channels.
