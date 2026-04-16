---
title: Connectors (MCP)
description: Connect external services to Mycelos using the Model Context Protocol.
order: 4
icon: hub
---

## What are MCP Servers?

MCP (Model Context Protocol) servers are external tool providers that give agents capabilities like web browsing, email access, or database queries. Mycelos uses MCP as the standard protocol for all external integrations — no vendor lock-in.

## Adding a Connector

Use the slash command in chat or the Connectors page:

```bash
/connector add duckduckgo npx @anthropic/duckduckgo-mcp
/connector add github npx @anthropic/github-mcp --secret GITHUB_TOKEN
```

## Built-in Connectors

- **DuckDuckGo** — web search
- **HTTP** — fetch URLs and APIs
- **Filesystem** — read/write files in sandboxed directories
- **Git** — repository operations

## MCP Registry

Search for community-published MCP servers:

```bash
/connector search email
/connector search database postgres
```
