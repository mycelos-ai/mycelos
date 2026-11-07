---
title: About Mycelos
description: A security-first agent operating system that runs on your machine
---

# Mycelos

A security-first agent operating system that grows with you.

## What is Mycelos?

Mycelos is your personal AI assistant that runs entirely on **your machine**, with **your data**, using **your API keys**. It is not a SaaS product. There is no cloud service between you and your agents.

At its core, Mycelos is an agent operating system. It manages agents the way an OS manages processes — with scheduling, resource control, security boundaries, and inter-process communication. Agents can be LLM-powered conversationalists, deterministic data processors, or anything in between.

Mycelos starts small — a single chat agent that knows you — and grows organically. As you use it, you can create new agents, connect external tools, build reusable workflows, and accumulate a knowledge base. The system learns your preferences and adapts, but never without your awareness.

## Core Principles

The Mycelos Constitution — non-negotiable rules that every component follows.

### Audit Everything

Every state-changing operation is logged. If it changes data, it gets audited. No exceptions, no silent mutations.

### Security Fail-Closed

When a security check fails or encounters an error, the action is denied. Never allow on uncertainty.

### Credentials Never Visible

No credential appears in logs, LLM prompts, error messages, or tracebacks. Encrypted at rest, decrypted only at the point of use.

### MCP for External Services

All external integrations use the Model Context Protocol. Standard protocol, open ecosystem, no vendor lock-in.

### Cost-Optimized

Always use the cheapest execution method first. Deterministic logic before Haiku, Haiku before Sonnet, Sonnet before Opus.

### Open Source

MIT licensed, community-driven. You can read, audit, and modify every line of code that runs on your machine.

## Why Open Source?

### Trust

You can verify exactly what the code does. Every security claim is auditable. No black boxes, no hidden telemetry.

### Privacy

Your data stays on your machine. Conversations, credentials, and knowledge never leave your control. The only external calls are to the LLM providers you choose.

### Extensibility

Build your own agents, connectors, and workflows. Every component follows a Protocol interface that you can implement and swap.

### Community

Contributions are welcome. Whether it is a new MCP connector, a bug fix, or a documentation improvement — the project grows through collaboration.

## Technology

Mycelos is built on proven, stable foundations with minimal dependencies.

**Tech stack:** Python 3.12+, SQLite (WAL mode), LiteLLM, MCP Protocol, AES-256-GCM, NixOS-style Config

- **SQLite with WAL mode** — fast, single-file database with concurrent read access. No database server to manage.
- **LiteLLM** — unified interface to multiple LLM providers. Switch between Anthropic, OpenAI, Google Gemini, and Ollama (local models) without code changes.
- **NixOS-style config generations** — every configuration change creates an immutable snapshot. Roll back to any previous state with a single command.
- **MCP (Model Context Protocol)** — the emerging standard for connecting AI agents to external tools. Built for interoperability.

### Supported LLM Providers

| Provider | Models |
|---|---|
| Anthropic | Claude Opus, Sonnet, Haiku |
| OpenAI | GPT-4o, GPT-4o-mini, o1, o3 |
| Google | Gemini 2.5 Pro, Flash |
| Ollama (Local) | Llama, Mistral, Qwen, and more |

## Getting Involved

Mycelos is built in the open. Whether you want to use it, extend it, or contribute back — you are welcome.

- **GitHub Repository** — Source code, issues, and releases
- **MIT License** — Free to use, modify, and distribute
- **Contributing** — Pull requests welcome — all skill levels
- **Community** — Coming soon — Discord and forum

---

*Like mycelium connecting a forest, Mycelos connects your tools, knowledge, and workflows into a living network that grows with you.*
