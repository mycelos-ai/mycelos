# Agent Routing — Announced Delegation with Summarized Context

**Date:** 2026-03-24
**Status:** PLAN (accepted pattern)
**Research:** OpenAI Agents SDK, Anthropic Multi-Agent, AutoGen, CrewAI, LangGraph

---

## Pattern: "Announced Delegation with Summarized Context"

Hybrid of OpenAI Agents SDK (user visibility, explicit routing) and
Anthropic Research System (context summarization, structured task specs).

### Core Rules

1. **User always talks to Mycelos** — specialists are visible workers, not conversation partners
2. **Deterministic routing via Orchestrator** — not LLM-decided agent-to-agent handoffs
3. **Context is summarized, not passed in full** — saves tokens, focuses specialists
4. **Explicit return contract** — specialists return structured results, Mycelos presents them

---

## User Experience

```
You> Erstell mir einen Agent der meine GitHub PRs prueft

Mycelos> Ich uebergebe das an den Creator-Agent.

  [Creator-Agent]
  Was genau soll der Agent pruefen? Nur offene PRs, oder auch gemergete?

You> Nur offene PRs, taeglich um 9 Uhr

  [Creator-Agent]
  Verstanden. Hier ist die Zusammenfassung:
  - Name: pr-reviewer
  - Prueft: offene PRs in deinen Repos
  - Schedule: taeglich 09:00
  - Capabilities: github.list_pull_requests, github.get_pull_request

  Soll ich das so umsetzen?

You> Ja

  [Creator-Agent → Pipeline laeuft...]

Mycelos> Der Creator-Agent hat den PR-Reviewer gebaut:
  - 3 Gherkin Szenarien
  - Tests: bestanden
  - Audit: bestanden
  - Status: aktiv
  - Schedule: taeglich 09:00

  Du bekommst die PR-Uebersicht ab morgen per Telegram.
```

---

## Data Model

### HandoffEnvelope (Mycelos → Specialist)

```python
@dataclass(frozen=True)
class HandoffEnvelope:
    delegating_agent: str       # "mycelos"
    target_agent: str           # "creator", "planner", "evaluator"
    user_request: str           # original user message
    context_summary: str        # 2-3 sentence summary of relevant context
    task_inputs: dict[str, Any] # structured data the specialist needs
    session_id: str             # for returning to the right conversation
    user_id: str                # for policy evaluation
    constraints: dict[str, Any] # cost limits, time limits
```

### HandoffResult (Specialist → Mycelos)

```python
@dataclass(frozen=True)
class HandoffResult:
    source_agent: str           # "creator"
    success: bool
    result_summary: str         # human-readable summary
    result_data: dict[str, Any] # structured output
    artifacts: list[str]        # file paths, agent IDs created
    needs_user_action: bool     # does user need to confirm?
    suggested_response: str     # what Mycelos should tell the user
```

---

## Routing Flow

```
User message
    |
    v
Orchestrator.route()
    |
    ├── CONVERSATION → Mycelos handles directly (LLM chat)
    |
    ├── CREATE_AGENT → HandoffEnvelope to Creator
    |   ├── Creator runs InterviewEngine (multi-turn)
    |   ├── User confirms → Creator Pipeline runs
    |   └── HandoffResult back to Mycelos → Mycelos presents result
    |
    ├── TASK_REQUEST → HandoffEnvelope to Planner
    |   ├── Planner creates plan (single LLM call)
    |   ├── Mycelos presents plan to user
    |   ├── User confirms → Workflow created + scheduled
    |   └── HandoffResult with workflow_id
    |
    └── SYSTEM_COMMAND → direct slash command handling
```

---

## Context Summarization

Instead of passing full conversation history to specialists:

```python
# Mycelos generates a context summary before handoff
summary = llm.complete([
    {"role": "system", "content": "Summarize the relevant context for a specialist agent in 2-3 sentences."},
    {"role": "user", "content": f"Conversation so far:\n{last_5_messages}\n\nUser's request: {message}"},
])

envelope = HandoffEnvelope(
    context_summary=summary.content,
    user_request=message,
    ...
)
```

This keeps specialist context windows small (~500 tokens instead of ~5000).

---

## Agent Visibility in UI

### Terminal (Rich)
```
Mycelos> I'll have the Creator work on this.

[Creator-Agent] What should the agent check?
```

### Telegram
```
[Creator-Agent]
Was genau soll der Agent pruefen?
```

### Web UI (SSE Events)
```json
{"event": "agent", "data": {"agent": "Creator-Agent"}}
{"event": "text", "data": {"content": "Was genau soll..."}}
```

The `agent_event("Creator-Agent")` already exists in our event system.
The frontend already renders it (chat-message.tsx shows agent name).

---

## What NOT To Do (from research)

1. **No free agent-to-agent handoffs** — keep routing in Orchestrator
2. **No full conversation history passing** — summarize, don't forward
3. **No implicit delegation** (CrewAI-style) — use structured HandoffEnvelope
4. **No group chat** (AutoGen-style) — overkill, user doesn't need to see agents talking
5. **No specialist talking directly to user** — Mycelos always frames the output

---

## Implementation Plan

### Phase 1: HandoffEnvelope + HandoffResult (this session)
- Create dataclasses in src/mycelos/agents/handoff.py
- Update Orchestrator to create HandoffEnvelope
- Update Creator InterviewEngine to accept HandoffEnvelope
- Update Creator to return HandoffResult
- Mycelos presents HandoffResult to user

### Phase 2: Planner Handoff (next session)
- Planner accepts HandoffEnvelope
- Planner returns HandoffResult with plan
- Mycelos presents plan, user confirms
- On confirm: workflow created + scheduled automatically

### Phase 3: Context Summarization
- Mycelos generates summary before handoff (Haiku — cheap)
- Specialists get focused context instead of full history

### Phase 4: Multi-Step Delegation
- Creator needs Planner for workflow creation
- Planner needs Creator for missing agents
- Chained delegation: Creator → Planner → back to Creator → Mycelos

---

## Research Sources

- OpenAI Agents SDK: handoffs as tool calls, nest_handoff_history
- Anthropic Multi-Agent Research: lead agent + parallel subagents + external memory
- AutoGen/Microsoft: supervisor pattern with group chat
- CrewAI: hierarchical delegation (fragile in production)
- LangGraph: state machine with conditional edges
- Vercel AI SDK: tool-call-based delegation
