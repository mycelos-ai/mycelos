# MCP Protocol — Technical Notes

## What is MCP?
Model Context Protocol (MCP) is an open protocol for connecting AI models to external tools and data sources. It provides a standardized way for agents to discover and use tools without custom integration code.

## Architecture
```
Agent → MCP Client → MCP Server → External Service
                  ↕
            Tool Discovery
            Schema Validation
            Auth Handling
```

## Key Concepts

### Tools
- Defined via JSON Schema
- Each tool has a name, description, and input schema
- Server declares available tools on connection
- Client validates inputs before sending

### Resources
- Read-only data sources (files, DB rows, API responses)
- URI-based addressing
- Can be subscribed to for changes

### Prompts
- Reusable prompt templates
- Server can offer pre-built prompts for common tasks

## Integration Patterns for Mycelos

### Connector = MCP Server
Each Mycelos connector wraps an MCP server:
- DuckDuckGo Search → `@anthropic/duckduckgo-mcp`
- GitHub → `@anthropic/github-mcp`
- Filesystem → `@anthropic/filesystem-mcp`

### Security Layer
- All MCP calls go through SecurityProxy
- Credential injection happens at proxy level
- Agent never sees raw API keys
- SSRF protection on HTTP-based tools

## Available MCP Servers (curated)
| Server | Tools | Status |
|--------|-------|--------|
| DuckDuckGo | search | Active |
| Brave Search | search, summarize | Available |
| GitHub | repos, issues, PRs | Available |
| PostgreSQL | query, schema | Available |
| Filesystem | read, write, list | Available |
| Puppeteer | navigate, screenshot | Available |
