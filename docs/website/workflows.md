---
title: Workflows
description: Reusable, multi-step plans that agents execute — versioned, audited, and schedulable.
order: 5
icon: account_tree
---

## What is a Workflow?

A workflow is a reusable, multi-step plan that agents execute. Each step can use different tools, models, and connectors. Workflows are versioned and audited.

## Built-in Workflows

- **research-summary** — search the web, extract key points, produce a summary
- **daily-briefing** — aggregate news, calendar, and tasks into a morning report
- **brainstorming** — structured ideation with multiple perspectives

## Creating Workflows

Ask the Builder agent to create a workflow from a natural language description, or use the slash command:

```bash
/workflow create "Research a topic and write a blog post draft"
```

Every new workflow must pass a mandatory dry-run before activation.

## Scheduling

Workflows can be scheduled with cron expressions:

```bash
# Run daily-briefing every morning at 8:00
/workflow schedule daily-briefing "0 8 * * *"
```
