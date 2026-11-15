---
title: API Reference
description: RESTful HTTP API exposed by the Gateway on port 9100.
order: 10
icon: api
---

The Gateway exposes a RESTful HTTP API on port `9100`. All endpoints return JSON unless noted otherwise.

## Authentication

When `MYCELOS_PASSWORD` is set in `.env`, every endpoint requires authentication: a session cookie from `POST /api/auth/login` (`{"password": "...", "remember": false}`) or **HTTP Basic Auth**. Username is ignored; the password is compared in constant time. After 10 wrong attempts from one address within five minutes, login and Basic Auth answer `429` with a `Retry-After` header. Set a password as soon as you expose the gateway beyond `127.0.0.1` — there is no other authentication layer in v0.3 (passkey ships in Phase 2).

Without a password, the gateway only answers for known host names (loopback, the bind address, detected LAN names, `MYCELOS_ALLOWED_ORIGINS`, `MYCELOS_ALLOWED_HOSTS`). A request with any other `Host` header gets `421 Host not allowed` — this blocks DNS-rebinding attacks from web pages. Scripts that call `http://localhost:9100` are unaffected.

`/api/health` and `/healthz` remain unauthenticated so Docker healthchecks and external probes can verify the service is up without a password.

## Chat

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/chat` | Send a message and receive a streamed response via Server-Sent Events (SSE). Body: `{ "message": "...", "session_id": "..." }` |

## Sessions

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/sessions` | List all chat sessions. |
| `GET` | `/api/sessions/{id}/messages` | Retrieve all messages for a specific session. |

## Knowledge

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/knowledge/notes` | List all notes in the knowledge base. |
| `POST` | `/api/knowledge/search` | Full-text search across notes. Body: `{ "query": "..." }` |

## Connectors

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/connectors` | List all configured MCP connectors. |
| `POST` | `/api/connectors` | Add a new MCP connector. Body: `{ "name": "...", "command": "...", "secrets": [...] }` |
| `DELETE` | `/api/connectors/{name}` | Remove a connector by name. |

## Agents

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/agents` | List all registered agents. |
| `GET` | `/api/agents/{id}` | Get details for a specific agent. |

## Models

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/models` | List all available LLM models from configured providers. |

## Cost

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/cost?period=today\|week\|month` | Get token usage and cost breakdown for the specified period. |

## Config

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/config` | Get the current active configuration. |
| `GET` | `/api/config/generations` | List all config generations with timestamps. |
| `POST` | `/api/config/rollback` | Roll back to a specific config generation. Body: `{ "generation": N }` |

## Health

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Health check endpoint. Returns `{ "status": "ok" }` when the Gateway is running. |
