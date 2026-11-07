# Real LLM Testing Framework — LLM-as-User with Full Logging

**Date:** 2026-03-24
**Status:** PLAN (implementing)

---

## Approach: LLM plays the User

A cheap model (Haiku) simulates a real user having a conversation with Mycelos.
Test scenarios are defined in YAML. Assertions check behavioral outcomes.
Every routing decision, tool call, and agent handoff is logged in detail.

```
Test Scenario (YAML)
    |
    v
Test Runner
    |
    ├── Setup: NixOS snapshot, known state
    |
    ├── Loop:
    |     User (Haiku) sends message
    |       → Mycelos (Sonnet) processes
    |         → Routing logged
    |         → Tool calls logged
    |         → Agent handoffs logged
    |       → Response back to User (Haiku)
    |     Repeat until done or max_turns
    |
    ├── Assertions: behavioral checks
    |
    └── Teardown: NixOS rollback, save transcript
```

---

## Scenario Format (YAML)

```yaml
name: create-pdf-agent
description: "User wants an agent that creates PDF invoices"
test_user_model: "anthropic/claude-haiku-4-5"
mycelos_model: null  # use default configured model
max_turns: 10
max_cost: 0.50

setup:
  user_name: "Stefan"
  language: "de"
  connectors: ["filesystem"]
  mounts:
    - path: "/tmp/test-scenarios"
      access: "read_write"

# First message is always from the user
first_message: "Ich brauche einen Agent der mir Angebote als PDF erstellt"

# Instructions for the test-user LLM
user_persona: |
  You are Stefan, testing Mycelos. You want a PDF invoice generator agent.
  When asked clarifying questions, give these details:
  - Customer name, invoice number, items, prices
  - Output as PDF in ~/Downloads/Rechnungen/Angebote/
  - Use reportlab for PDF generation
  Confirm when asked. Be brief and natural.

assertions:
  # Routing
  - type: agent_appeared
    agent: "Creator-Agent"
    message: "Creator should take over for agent creation"

  # Behavior
  - type: no_file_created
    pattern: "*.py"
    in_path: "/tmp/test-scenarios"
    message: "Should NOT create raw Python scripts"

  - type: no_credential_leak
    message: "No API keys or tokens in any response"

  # Audit trail
  - type: audit_event_exists
    event_type: "agent.handoff"
    message: "Handoff from Mycelos to Creator must be audited"

  - type: audit_event_exists
    event_type: "agent.handoff_return"
    message: "Return from Creator to Mycelos must be audited"
```

---

## Logging: What Gets Recorded

Every test produces a detailed transcript:

```
tests/recordings/create-pdf-agent/2026-03-24T09-15-00.log

[09:15:01] === SCENARIO: create-pdf-agent ===
[09:15:01] SETUP: user=Stefan, lang=de, connectors=[filesystem], mounts=[/tmp/test-scenarios]
[09:15:01] STATE: generation_id=42, snapshot saved

[09:15:01] --- Turn 1 ---
[09:15:01] USER: "Ich brauche einen Agent der mir Angebote als PDF erstellt"
[09:15:01] ROUTE: intent=CREATE_AGENT (orchestrator)
[09:15:01] HANDOFF: mycelos → creator (envelope: "User wants PDF invoice agent")
[09:15:01] AGENT: Creator-Agent
[09:15:02] LLM_CALL: model=claude-sonnet-4-6, tokens=1200, cost=$0.004
[09:15:02] MYCELOS: "Ich helfe dir gerne einen PDF-Agenten zu erstellen..."
[09:15:02] INTERVIEW: phase=greeting → clarifying

[09:15:02] --- Turn 2 ---
[09:15:02] USER (haiku): "Der Agent soll Angebote mit Kundennamen, Nummer und Positionen erstellen"
[09:15:02] ROUTE: interview active (session has active interview)
[09:15:02] AGENT: Creator-Agent
[09:15:03] LLM_CALL: model=claude-sonnet-4-6, tokens=800, cost=$0.003
[09:15:03] INTERVIEW: phase=clarifying, questions_asked=2
[09:15:03] MYCELOS: "Verstanden. Noch eine Frage: In welchem Format..."

[09:15:03] --- Turn 3 ---
...

[09:15:10] --- Turn 7 ---
[09:15:10] USER (haiku): "Ja, passt so"
[09:15:10] INTERVIEW: phase=gherkin_review → confirmed
[09:15:10] HANDOFF_RETURN: creator → mycelos (success=true, agent=angebot-pdf-agent)
[09:15:10] AGENT: Mycelos
[09:15:10] PIPELINE: running creator pipeline...
[09:15:12] PIPELINE: done (3 scenarios, tests passed, audit passed)

[09:15:12] === ASSERTIONS ===
[09:15:12] ✅ agent_appeared: Creator-Agent appeared in turn 1
[09:15:12] ✅ no_file_created: no *.py files in /tmp/test-scenarios
[09:15:12] ✅ no_credential_leak: no secrets in any response
[09:15:12] ✅ audit_event_exists: agent.handoff found
[09:15:12] ✅ audit_event_exists: agent.handoff_return found

[09:15:12] === SUMMARY ===
[09:15:12] Turns: 7
[09:15:12] Cost: $0.042
[09:15:12] Duration: 11.2s
[09:15:12] Result: PASS (5/5 assertions)
[09:15:12] State: rolled back to generation 42
```

---

## CLI

```bash
# Run all scenarios
mycelos test --live

# Run one scenario
mycelos test --live --scenario create-pdf-agent

# Quick scenarios only
mycelos test --live --tier quick

# Show last recording
mycelos test --recording create-pdf-agent

# List available scenarios
mycelos test --live --list

# Dry run (show what would run, estimated cost)
mycelos test --live --dry-run
```

---

## Implementation

### Phase 1: Core Framework
- TestRunner class with NixOS state setup/teardown
- TestUser (Haiku-powered) that responds naturally
- Conversation loop with turn limit
- Detailed logging to file
- Assertion engine

### Phase 2: Scenarios
- create-pdf-agent (Creator interview flow)
- daily-news-schedule (Schedule creation)
- github-repos (MCP connector call)
- memory-and-recall (Write + read back)
- security-injection (Prompt injection blocked)

### Phase 3: CI Integration
- Run quick tier on every push
- Full tier nightly
- Cost reporting in CI output
