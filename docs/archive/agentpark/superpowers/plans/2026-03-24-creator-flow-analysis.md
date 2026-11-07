# Creator Agent Flow Analysis — What Goes Wrong

**Date:** 2026-03-24
**Status:** ANALYSIS (must fix before next feature)

---

## The Optimal Flow (how it SHOULD work)

```
User: "I need an agent that reads PPTX files"
  |
  v
[1] Orchestrator classifies → CREATE_AGENT
  |
  v
[2] ChatService._handle_create_agent()
  |  → Creates HandoffEnvelope with context summary
  |  → Starts InterviewEngine
  |  → Shows [Creator-Agent] attribution
  |
  v
[3] InterviewEngine Phase: GREETING
  |  → LLM asks one clarifying question
  |  → Response shown to user as [Creator-Agent]
  |
  v
[4] User answers question
  |
  v
[5] InterviewEngine Phase: CLARIFYING (max 3 rounds)
  |  → LLM either asks another question or declares spec complete
  |  → When complete: builds AgentSpec from JSON
  |
  v
[6] InterviewEngine Phase: SCOPE_CHECK
  |  → classify_effort() → trivial/small/large/unrealistic
  |  → If OK → auto-advance to SUMMARY
  |
  v
[7] InterviewEngine Phase: SUMMARY
  |  → Shows human-readable spec summary
  |  → User confirms ("ja", "looks good")
  |
  v
[8] InterviewEngine Phase: GHERKIN_REVIEW
  |  → LLM generates Gherkin acceptance scenarios
  |  → Shows to user for confirmation
  |  → User confirms
  |
  v
[9] InterviewEngine Phase: CONFIRMED
  |  → result.confirmed = True, result.spec = AgentSpec
  |
  v
[10] ChatService._handle_interview_message()
  |  → Detects confirmed = True
  |  → Calls _run_creator_pipeline(spec)
  |
  v
[11] CreatorPipeline.run(spec)
  |  → Step 1: Generate Gherkin scenarios
  |  → Step 2: Generate tests (TDD)
  |  → Step 3: Generate code (passes tests)
  |  → Step 4: Run tests in sandbox
  |  → Step 5: Security audit
  |  → Step 6: Register agent (DB + Object Store)
  |
  v
[12] HandoffResult → Mycelos
  |  → "Agent pptx-reader created. 3 scenarios, tests passed, audit passed."
  |
  v
[13] Mycelos presents result to user
  |  → "Der Agent ist fertig! Du kannst jetzt fragen: 'Lies die Datei X.pptx'"
```

---

## What Actually Happens (current bugs)

### Problem 1: Interview never reaches CONFIRMED

The LLM in CLARIFYING phase returns JSON wrapped in markdown code blocks.
`json.loads()` fails, the response is treated as a follow-up question.
The interview loops in CLARIFYING forever.

**Status: FIXED** (added _extract_json helper)

### Problem 2: Mycelos resumes after failed handoff

When the Creator returns with `success: false`, Mycelos takes over and
tries to solve the problem itself — including writing .py scripts.

**Root cause:** The ChatService doesn't properly handle the case where
the interview ends without reaching CONFIRMED. When the user says "ja"
but the InterviewEngine is still in CLARIFYING (because JSON parsing failed),
the next message routes through the Orchestrator again (not the interview).

**What should happen:** If the interview fails/stalls, Mycelos should tell
the user "The Creator couldn't complete the interview. Want to try again?"
Not silently take over and write scripts.

### Problem 3: Interview state is lost between turns

The `_interviews` dict maps session_id → interview data. But when the
user continues in a new gateway request, the ChatService might be a
different instance (if the gateway recreates it).

**Check:** Is `_interviews` persistent across requests? In gateway mode,
`ChatService` is created once in `create_app()` and stored on `api.state`.
So it SHOULD persist. But need to verify.

### Problem 4: "create_workflow" tool is used as workaround

Mycelos has a `create_workflow` tool which lets it create workflows directly.
When the Creator fails, Mycelos uses this to create a dummy workflow.
This is NOT the same as the Creator Pipeline building and testing an agent.

**Fix:** Remove `create_workflow` from chat tools, or make it require
confirmation. Workflow creation should go through the Planner.

### Problem 5: No background execution

The Creator Pipeline can take 30-60 seconds (multiple LLM calls).
Currently this blocks the entire request. The user sees nothing while
the pipeline runs. In the gateway, the SSE connection stays open but
no progress events are streamed.

**Need:** Either:
a) Stream progress events during pipeline execution
b) Run pipeline in background thread, notify on completion

---

## Fix Priority

### P0: Make the interview reach CONFIRMED reliably
1. JSON parsing fixed ✅
2. Max clarifying rounds ✅
3. Need to verify: does the CONFIRMED state actually trigger _run_creator_pipeline?
4. Need live test: `mycelos test --live create-pdf-agent`

### P1: Prevent Mycelos from workarounds when Creator fails
1. Remove or restrict `create_workflow` tool
2. Better error handling: if Creator returns success=false, explain to user
3. Don't let Mycelos write scripts (guard exists but needs testing)

### P2: Pipeline progress
1. Stream step events during pipeline execution
2. Or background thread with status polling

---

## Verification Test

Create a live test scenario that verifies the FULL flow:

```yaml
name: creator-full-flow
description: "Full Creator flow: interview → confirm → pipeline → agent registered"
max_turns: 15
max_cost: 2.00

assertions:
  - type: agent_appeared
    agent: "Creator-Agent"
  - type: response_contains
    text: "Gherkin"
  - type: audit_event_exists
    event_type: "agent.handoff"
  - type: audit_event_exists
    event_type: "agent.handoff_return"
    # AND success: true
```
