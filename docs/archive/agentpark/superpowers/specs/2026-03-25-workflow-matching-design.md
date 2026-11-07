# Workflow Matching — Design Specification

**Date:** 2026-03-25
**Status:** DRAFT
**Scope:** Planner finds and executes matching workflows automatically. Built-in workflow templates ship with Mycelos.

---

## 1. Overview

When a user makes a complex request, the Planner should automatically find
a matching workflow from the database and execute it — instead of creating
an ad-hoc plan every time.

### The Problem Today

```
User: "Sammle Ideen für mein Steuerberater-System"
  → Classifier: CREATE_AGENT (wrong!)
  → Creator Agent starts interview for agent creation
  → User wanted structured brainstorming, not an agent
```

### The Solution

```
User: "Sammle Ideen für mein Steuerberater-System"
  → Classifier: TASK_REQUEST (complex, needs structure)
  → Planner: searches workflows → finds "brainstorming-interview"
  → Executes workflow: structured questions → KB note
```

### Core Principle

Simple requests → CONVERSATION (Mycelos handles directly with tools).
Complex multi-step requests → TASK_REQUEST → Planner → Workflow match.
Agent creation → CREATE_AGENT (only when user explicitly says "build agent").

---

## 2. Classifier Prompt Update

The classifier needs better examples to distinguish:
- "Merke dir X" → CONVERSATION (simple note_write)
- "Sammle Ideen zu X" → TASK_REQUEST (needs structured workflow)
- "Recherchiere X und fasse zusammen" → TASK_REQUEST (multi-step)
- "Erstelle einen Agent der X macht" → CREATE_AGENT

Updated classifier prompt additions:

```
- "Remember that we decided X" → conversation (simple note_write tool)
- "Note: meeting with Alex on Friday" → conversation (simple note_write)
- "Collect ideas for my project" → task_request (needs structured interview)
- "Research AI trends and summarize" → task_request (multi-step: search + summarize)
- "Brainstorm features for X" → task_request (needs structured approach)
- "Give me a daily briefing" → task_request (needs workflow)
- "What tasks are due today?" → conversation (simple note_list tool)
```

---

## 3. Planner Workflow Matching

### Current Planner Flow

```
User: "Summarize 50 emails"
  → Planner LLM call with user message + context
  → Returns: {action: "execute_workflow" or "needs_new_agent", steps: [...]}
```

### New Flow

```
User: "Sammle Ideen für mein Steuerberater-System"
  → Planner receives:
    1. User message
    2. Available workflows (from DB, with descriptions)
    3. Existing agents (already there)
  → Planner decides:
    a) Match: "brainstorming-interview" workflow fits → execute it
    b) No match: create ad-hoc plan with steps
    c) Needs agent: "needs_new_agent" → Creator
```

### Workflow Context for Planner

The Planner prompt receives a summary of available workflows:

```
Available Workflows:
- brainstorming-interview: Structured idea collection through follow-up
  questions. Creates a knowledge base note with organized ideas.
  Use for: collecting ideas, exploring concepts, planning features.

- research-summary: Search the web for a topic, collect sources,
  and create a summarized knowledge base note.
  Use for: research, trend analysis, competitive analysis.

- daily-briefing: Check open tasks, overdue items, and search for
  relevant news. Summarize as a briefing.
  Use for: morning overview, status check, daily planning.
```

This context is built from the `workflows` table: `name`, `description`,
and `tags`. No embedding search needed for Day 1 — the workflow list is
small enough to include in full. Embedding-based matching is a future
optimization when there are 20+ workflows.

### Planner Response Format

```json
{
  "action": "execute_workflow",
  "workflow_id": "brainstorming-interview",
  "inputs": {
    "topic": "Steuerberater-System",
    "context": "User wants to collect ideas for a tax advisor system"
  }
}
```

Or if no workflow matches:

```json
{
  "action": "execute_workflow",
  "workflow_id": null,
  "steps": [
    {"action": "note_write", "description": "Create initial note"},
    {"action": "search_web", "description": "Research the topic"}
  ]
}
```

---

## 4. Built-in Workflow Templates

### 4.1 brainstorming-interview

```yaml
id: brainstorming-interview
name: Brainstorming Interview
description: >
  Structured idea collection through follow-up questions.
  Asks about vision, target audience, core features, UI/UX ideas,
  technical approach, and open questions. Creates a knowledge base
  note with organized results.
goal: Collect and organize ideas through conversation
steps:
  - id: interview
    action: conversation
    description: >
      Ask the user structured questions one at a time:
      1. Vision — what problem does this solve?
      2. Target audience — who is this for?
      3. Core features — what are the must-haves?
      4. UI/UX — how should it look/feel?
      5. Technical approach — any constraints?
      6. Open questions — what's unclear?
      Collect all answers.
  - id: create_note
    action: note_write
    description: >
      Create a structured KB note from the interview results.
      Type: note, tags based on topic.
scope: [knowledge.write, search.web]
tags: [brainstorming, ideas, interview, planning]
```

### 4.2 research-summary

```yaml
id: research-summary
name: Research & Summary
description: >
  Search the web for a topic, collect key findings from multiple
  sources, and create a summarized knowledge base note with sources.
goal: Research a topic and create a summary note
steps:
  - id: search
    action: search_web
    description: Search for the topic, collect top results
  - id: deep_read
    action: http_get
    description: Read the most relevant pages for details
  - id: summarize
    action: note_write
    description: >
      Create a KB note with findings, organized by subtopic.
      Include source links.
scope: [search.web, http.get, knowledge.write]
tags: [research, summary, analysis]
```

### 4.3 daily-briefing

```yaml
id: daily-briefing
name: Daily Briefing
description: >
  Morning overview: check open and overdue tasks from knowledge base,
  optionally search for relevant news, and present as a briefing.
goal: Give the user a daily status overview
steps:
  - id: tasks
    action: note_list
    description: Get open and overdue tasks
  - id: summarize
    action: conversation
    description: >
      Present tasks and any news as a concise briefing.
      Highlight overdue items.
scope: [knowledge.read]
tags: [briefing, daily, tasks, overview]
```

---

## 5. Workflow Execution

### How Workflows Run

When the Planner selects a workflow:

1. ChatService receives `{action: "execute_workflow", workflow_id: "brainstorming-interview"}`
2. Load workflow from DB
3. For conversation steps: the LLM conducts the conversation naturally,
   following the step description as guidance
4. For tool steps: execute the tool with inputs from previous steps
5. Result: KB note created, user informed

### Conversation Steps

The key insight: most workflow steps are **conversation-driven**, not
automated tool calls. The LLM reads the step description and acts
accordingly. For "brainstorming-interview", the LLM:
- Asks question 1 (vision)
- Waits for user answer
- Asks question 2 (target audience)
- ... continues through all questions
- Creates the summary note

This means the workflow is **multi-turn** — it spans multiple
`handle_message()` calls. The workflow state (current step, collected
answers) is tracked in the session.

### Workflow Session State

```python
# In ChatService, similar to _interviews dict:
_active_workflows: dict[str, dict] = {}
# session_id → {workflow_id, current_step, collected_data, ...}
```

When a workflow is active for a session, messages are routed to the
workflow handler instead of the normal conversation flow (same pattern
as the Creator interview).

---

## 6. Implementation Changes

### Modified Files

| File | Change |
|------|--------|
| `src/mycelos/orchestrator.py` | Update classifier prompt with new examples |
| `src/mycelos/agents/planner.py` | Add workflow list to planner context |
| `src/mycelos/agents/planner_context.py` | Include workflows in context |
| `src/mycelos/chat/service.py` | Add workflow execution handler (multi-turn) |
| `src/mycelos/cli/init_cmd.py` | Seed built-in workflows on init |

### New Files

| File | Purpose |
|------|---------|
| `src/mycelos/workflows/templates.py` | Built-in workflow definitions |
| `tests/test_workflow_matching.py` | Tests for matching + execution |

---

## 7. Seed Workflows on Init

During `mycelos init`, register the 3 built-in workflows:

```python
# In init_cmd.py or app.initialize():
from mycelos.workflows.templates import BUILTIN_WORKFLOWS

for wf in BUILTIN_WORKFLOWS:
    existing = app.workflow_registry.get(wf["id"])
    if not existing:
        app.workflow_registry.register(wf)
```

Existing user workflows are never overwritten. Built-in workflows have
`created_by: "system"` and `tags: ["builtin"]`.

---

## 8. Planner Prompt Update

Add to the planner prompt:

```
Before creating a new plan, check if an existing workflow matches
the user's request. If a workflow matches, use it:

{workflow_list}

If a workflow matches:
  {"action": "execute_workflow", "workflow_id": "<id>", "inputs": {...}}

If no workflow matches but existing agents can handle it:
  {"action": "execute_workflow", "steps": [...]}

If a new agent is needed:
  {"action": "needs_new_agent", "reason": "..."}
```

---

## 9. Testing Strategy

### Unit Tests

- Classifier: "Sammle Ideen" → task_request, "Merke dir X" → conversation
- Planner: receives workflow list, matches brainstorming → execute_workflow
- Workflow templates: all 3 built-in workflows are valid
- Seed: init registers workflows without overwriting existing

### Integration Tests

- Full flow: "Brainstorm features for X" → Planner → workflow match → execution starts
- No match: "Do something unusual" → Planner → ad-hoc plan

---

## 10. Scope Boundary

### In Scope

- Classifier prompt update (better routing for notes vs workflows)
- Planner workflow matching (workflow list in context)
- 3 built-in workflow templates
- Seed workflows on init
- Multi-turn workflow execution in ChatService
- Workflow session state tracking

### Phase 2: Workflow Auto-Discovery (next iteration)

When the user makes a similar complex request for the second time, Mycelos
should recognize the pattern and suggest creating a workflow:

```
User: "Check my Git repos, who committed most this week"
  → Mycelos executes (github stats, aggregation, response)
  → Session summary saved to KB

[One week later]
User: "Same thing again with the Git stats"
  → Embedding search finds: 95% similar to last week's request
  → Mycelos: "You've asked this before. Want me to create a
    'weekly-git-stats' workflow? I can run it every Monday."
  → User: "Yes"
  → Workflow auto-created from the previous execution steps
  → Optionally scheduled (cron)
```

This requires:
- Session summaries in KB (journal entries with executed tool calls)
- Embedding similarity detection across sessions
- Workflow generation from execution history
- User confirmation before creating (Constitution: user decides)

### Out of Scope (Future, beyond Phase 2)

- Workflow editor UI
- Workflow sharing/marketplace
- Generalized Interview Engine (reusable state machine)
