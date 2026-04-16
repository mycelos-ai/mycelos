---
title: CLI Reference
description: Complete reference for all mycelos command-line commands.
order: 8
icon: terminal
---

| Command | Description |
|---|---|
| `mycelos init` | Initialize the database, config directory, and master encryption key. |
| `mycelos demo` | Interactive demo walkthrough — Mycelos in 60 seconds, no API key needed. |
| `mycelos serve` | Start the Gateway (HTTP API + SecurityProxy + Scheduler + Web UI). |
| `mycelos chat` | Interactive chat session. Uses Gateway if running, otherwise direct LLM mode. |
| `mycelos credential store\|list\|delete\|export\|import` | Manage encrypted credentials (API keys, tokens, passwords). |
| `mycelos connector add\|list\|remove` | Manage MCP connector servers. |
| `mycelos config list\|rollback [N]` | View config generations or roll back to a previous generation. |
| `mycelos model list` | List available LLM models from all configured providers. |
