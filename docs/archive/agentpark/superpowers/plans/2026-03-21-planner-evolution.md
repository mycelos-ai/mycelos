# Planner Evolution: Context-Aware Planning + Auto-Create Missing Agents

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Der Planner bekommt den vollen System-Kontext (Agents, Workflows, Capabilities, Connectors) und kann fehlende Agents erkennen und deren Erstellung vorschlagen.

---

## Aktuelles Problem

```python
# orchestrator.py Zeile 193:
plan = self._planner.plan(message, context={})  # LEER!
```

Der Planner weiss nicht was verfuegbar ist. Er plant blind.

## Ziel

```python
context = build_planner_context(app)
# context = {
#     "available_agents": [
#         {"id": "creator-agent", "type": "full_model", "capabilities": []},
#         {"id": "planner-agent", "type": "light_model", "capabilities": []},
#     ],
#     "available_workflows": [
#         {"id": "news-summary", "steps": 3, "scope": ["search.web"]},
#         {"id": "create-agent", "steps": 7, "scope": ["agent.register"]},
#     ],
#     "available_capabilities": ["search.web", "search.news", "http.get", ...],
#     "available_connectors": ["DuckDuckGo", "HTTP"],
# }
plan = self._planner.plan(message, context=context)
```

Der Planner sieht alles und entscheidet:
1. **Existierender Workflow passt** → ausfuehren
2. **Existierende Agents reichen** → neuen Workflow zusammenbauen
3. **Agent fehlt** → `needs_new_agent` mit Beschreibung was gebraucht wird

---

## Neuer Planner-Prompt

```
You are the PlannerAgent in Mycelos. You decompose user requests into executable plans.

## Available System Resources

### Agents (registered and active):
{agents_list}

### Workflows (defined and ready):
{workflows_list}

### Capabilities (tools available via connectors):
{capabilities_list}

## Decision Framework
1. Can an EXISTING WORKFLOW handle this? → action: "execute_workflow"
2. Can EXISTING AGENTS be combined into steps? → action: "execute_workflow" with new steps
3. Is a capability available but no agent uses it? → action: "execute_workflow", assign capability to step
4. Is something MISSING entirely? → action: "needs_new_agent" with description

## Output Format
Respond ONLY with valid JSON:
{
  "action": "execute_workflow" | "needs_new_agent" | "needs_multiple_agents",
  "workflow_id": "existing workflow name or null for ad-hoc",
  "steps": [
    {
      "id": "unique-step-id",
      "agent": "agent-id or 'new:description'",
      "action": "what this step does",
      "capabilities_needed": ["cap1", "cap2"],
      "mounts_needed": [{"path": "~/...", "access": "read"}]
    }
  ],
  "missing_agents": [
    {
      "name": "suggested-name",
      "description": "what it should do",
      "capabilities": ["needed-caps"],
      "effort": "trivial|small|medium"
    }
  ],
  "estimated_cost": "low" | "medium" | "high",
  "explanation": "kurze Erklaerung fuer den User"
}

## Rules
- Minimize steps
- Prefer existing agents and workflows
- Prefer deterministic agents (cheapest)
- If something is missing, describe EXACTLY what kind of agent is needed
- Always include estimated_cost
- If mounts (filesystem access) are needed, specify them in steps
```

---

## Context Builder

```python
def build_planner_context(app) -> dict:
    """Build rich context for the Planner from live DB state."""
    # Active agents with capabilities
    agents = app.agent_registry.list_agents(status="active")
    agents_info = [
        {
            "id": a["id"],
            "name": a["name"],
            "type": a["agent_type"],
            "capabilities": a["capabilities"],
        }
        for a in agents
    ]

    # Active workflows
    workflows = app.workflow_registry.list_workflows(status="active")
    workflows_info = [
        {
            "id": w["id"],
            "name": w["name"],
            "description": w.get("description", ""),
            "steps": len(w.get("steps", [])),
            "scope": w.get("scope", []),
        }
        for w in workflows
    ]

    # All available capabilities (from connectors)
    connectors = app.connector_registry.list_connectors(status="active")
    capabilities = set()
    connector_names = []
    for c in connectors:
        connector_names.append(c["name"])
        for cap in c.get("capabilities", []):
            capabilities.add(cap)

    return {
        "available_agents": agents_info,
        "available_workflows": workflows_info,
        "available_capabilities": sorted(capabilities),
        "available_connectors": connector_names,
    }
```

---

## Handling "needs_new_agent"

Wenn der Planner `needs_new_agent` zurueckgibt:

```
Mycelos> Um das umzusetzen, brauche ich einen neuen Agent:

  Name: pdf-processor
  Aufgabe: PDFs herunterladen und Text extrahieren
  Capabilities: http.get
  Aufwand: klein

  Soll ich ihn erstellen?

User> Ja

→ Creator Pipeline startet mit der Spec aus dem Plan
→ Nach Erstellung: Planner wird erneut aufgerufen
→ Jetzt hat er den neuen Agent und kann den Workflow planen
```

---

## Tasks

### Task 1: Planner Context Builder

**Files:**
- Create: `src/mycelos/agents/planner_context.py`
- Create: `tests/test_planner_context.py`

**Was:**
- `build_planner_context(app) -> dict` — liest Agents, Workflows, Capabilities
- Formatiert als kompakten String fuer den Prompt
- Tests: mit registrierten Agents/Workflows/Connectors

---

### Task 2: Planner Prompt + Response Handling V2

**Files:**
- Modify: `src/mycelos/agents/planner.py`
- Modify: `tests/test_planner.py`

**Was:**
- Neuer System-Prompt mit Agent/Workflow/Capability Kontext
- `plan()` akzeptiert den reichen Context
- Response-Parsing: `needs_new_agent`, `needs_multiple_agents`, `missing_agents`
- `steps[].capabilities_needed`, `steps[].mounts_needed`
- Fallback bei invalid JSON bleibt

---

### Task 3: Orchestrator Integration — Context durchreichen

**Files:**
- Modify: `src/mycelos/orchestrator.py`
- Modify: `src/mycelos/app.py` (inject context builder)
- Modify: `tests/test_orchestrator.py`

**Was:**
- `_handle_task_request()` baut Context via `build_planner_context()`
- Orchestrator bekommt `app` Reference (oder context builder callable)
- Plan mit `needs_new_agent` → RouteResult mit neuer Action

---

### Task 4: ChatService — "needs_new_agent" Flow

**Files:**
- Modify: `src/mycelos/chat/service.py`
- Create: `tests/test_planner_create_flow.py`

**Was:**
- Wenn Plan `needs_new_agent` enthaelt:
  - User informieren welche Agents fehlen
  - Bei Bestaetigung: Creator Pipeline fuer jeden fehlenden Agent
  - Nach Erstellung: Planner erneut aufrufen mit neuem Context
  - Ergebnis: vollstaendiger Plan mit allen Agents
- Events: step-progress fuer "Agent wird erstellt..."

---

### Task 5: E2E Tests

**Files:**
- Create: `tests/test_planner_evolution_e2e.py`

**Was:**
- Planner erkennt existierenden Workflow → execute
- Planner kombiniert existierende Agents → ad-hoc Workflow
- Planner erkennt fehlenden Agent → needs_new_agent
- Planner mit leerem System → alles fehlt
- Planner mit vollem System → optimaler Plan
- Context enthaelt korrekte Agents/Workflows/Capabilities

---

## Reihenfolge

```
Task 1 (Context Builder)
    ↓
Task 2 (Planner V2)
    ↓
Task 3 (Orchestrator Integration)
    ↓
Task 4 (ChatService Flow)
    ↓
Task 5 (E2E Tests)
```
