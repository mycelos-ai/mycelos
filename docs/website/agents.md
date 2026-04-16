---
title: Agents
description: Built-in agents, agent handoff, and the Builder pipeline for creating custom agents.
order: 3
icon: smart_toy
---

## Built-in Agents

- **Mycelos** — the primary chat agent. Handles conversations, answers questions, and routes complex tasks to the Builder.
- **Builder** — a specialist agent that creates new workflows and agents automatically through a structured pipeline.

## Agent Handoff

When you ask Mycelos to create something complex (a new agent, a multi-step workflow), it hands off to the Builder agent. Once the Builder finishes, control returns to Mycelos. You will see the active agent indicator change in the chat panel.

## Custom Agents

Custom agents are built through the Builder pipeline:

1. **Gherkin scenarios** — define acceptance criteria
2. **Tests** — generated from scenarios
3. **Code** — implementation to pass the tests
4. **Audit** — security review of the generated code
5. **Register** — human confirmation required before activation

Agents are not just LLM wrappers. Deterministic programs, parsers, and converters are first-class agents in Mycelos.
