---
title: API Reference
description: RESTful HTTP API exposed by the Gateway on port 9100.
order: 10
icon: api
---

The Gateway exposes a RESTful HTTP API on port `9100`. All endpoints return JSON unless noted otherwise.

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
