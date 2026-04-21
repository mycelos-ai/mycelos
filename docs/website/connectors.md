---
title: Connectors (MCP)
description: Connect external services to Mycelos using the Model Context Protocol.
order: 4
icon: hub
---

## What are MCP servers?

MCP (Model Context Protocol) servers are external tool providers that give agents capabilities like web browsing, email access, or database queries. Mycelos uses MCP as the standard protocol for all external integrations — no vendor lock-in.

## How credentials flow

A connector that needs a secret (e.g. a GitHub token) never gives that secret to the agent or to the gateway container. Flow:

1. You store the token via `/connector add github …` or the Connectors page. It's encrypted by the proxy.
2. The MCP server is launched **inside the proxy container** with the token injected into its environment — the gateway only sees a session ID.
3. When the agent calls a tool, the gateway forwards the call through the proxy over an authenticated internal channel.

For HTTP-based integrations (non-MCP) the proxy supports three credential injection modes — bearer, custom header, or URL-path substitution — documented in [`docs/security/two-container-deployment.md`](https://github.com/mycelos-ai/mycelos/blob/main/docs/security/two-container-deployment.md).

## Adding a connector

From the web UI: **Settings → Connectors → Add**. It walks you through token capture, validation, and allowlist.

From chat:

```text
/connector add telegram <bot-token>
/connector add github npx -y @modelcontextprotocol/server-github
/connector add slack npx -y @modelcontextprotocol/server-slack
```

When the MCP server needs an env var (like `GITHUB_TOKEN`), the proxy looks it up from the encrypted credential store at launch time — you don't pass it on the command line.

## Built-in connectors

- **DuckDuckGo** — web search, available by default
- **HTTP** — `http_get` for URL fetches (Markdown-rendered)
- **Filesystem** — read / write files in explicitly mounted directories
- **Git** — repository operations

Every outbound request from every connector is SSRF-guarded at the proxy: no loopback, no RFC 1918, no metadata endpoints.

## MCP registry

Search community-published MCP servers from chat:

```text
/connector search email
/connector search database postgres
```

The registry is cached and refreshed through the proxy — the gateway never hits the registry API directly.
