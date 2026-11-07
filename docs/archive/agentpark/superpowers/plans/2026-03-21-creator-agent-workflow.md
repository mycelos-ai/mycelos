# Creator-Agent Workflow: Interview → Gherkin → TDD → Audit → Register

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Der Creator-Agent wird ein TDD-Workflow der nicht-technische User durch ein Interview fuehrt, daraus Gherkin-Scenarios ableitet (die der User versteht und bestaetigt), dann Tests + Code generiert, die Tests ausfuehrt, und den Agent erst nach bestandenem Audit registriert.

**Key Insight:** Gherkin ist die gemeinsame Sprache. Der User bestaetigt Scenarios, nicht Code.

---

## Der Flow

```
PHASE 1: INTERVIEW (Chat, iterativ)
  Mycelos fragt gezielt:
  - Was soll passieren? (konkreter Use Case)
  - Welche Daten/Services? (Email, Web, Dateien?)
  - Ausloeser? (On-Demand, regelmaessig, bei Event?)
  - Besonderheiten? (Format, Sprache, Sicherheit?)
  → Ergebnis: Strukturierte Agent-Spec

PHASE 2: FEASIBILITY (Planner, Haiku)
  - Aufwand: TRIVIAL / KLEIN / MITTEL / GROSS / UNREALISTISCH
  - Benoetigte Capabilities
  - Geschaetzte Kosten
  - Bei GROSS: Aufteilen vorschlagen
  - Bei UNREALISTISCH: Ehrlich ablehnen
  → User bestaetigt: "Ja, mach das"

PHASE 3: GHERKIN SCENARIOS (Creator, Sonnet)
  - Aus der Spec werden Gherkin-Scenarios generiert
  - In der Sprache des Users (Deutsch wenn User Deutsch spricht)
  - Klar, verstaendlich, keine technischen Details
  → User bestaetigt: "Ja, genau so soll es funktionieren"
  → Das ist der Vertrag!

PHASE 4: TESTS (Creator, Sonnet) — TDD: Tests ZUERST
  - Aus den Gherkin-Scenarios werden pytest-Tests abgeleitet
  - Tests definieren das erwartete Verhalten
  - Tests sind ausfuehrbar aber scheitern (noch kein Code)

PHASE 5: CODE (Creator, Sonnet/Opus mit Failover)
  - Agent-Code der die Tests erfuellen muss
  - Folgt dem Standard-Agent-Template (AgentInput → AgentOutput)
  - Nutzt nur erlaubte Tools (run(), progress())

PHASE 6: TEST EXECUTION (Sandbox, deterministisch)
  - pytest in isolierter Umgebung (LocalSandbox)
  - Timeout: 30s
  - Gruen → weiter zu Audit
  - Rot → zurueck zu Phase 5 (Code fixen, max 3 Retries)
  - Immer noch rot → zurueck zu Phase 4 (Tests ueberpruefen)

PHASE 7: AUDIT (Auditor, Sonnet)
  - NUR wenn Tests gruen
  - Security: keine gefaehrlichen Imports, keine Credential-Leaks
  - Capabilities stimmen mit Spec ueberein
  - Approved → weiter
  - Rejected → zurueck zu Phase 5 mit Audit-Feedback

PHASE 8: REGISTER (deterministisch)
  - Code → Object Store
  - Agent → AgentRegistry + Capabilities
  - LLM Models → agent_llm_models
  - Prompt → Object Store
  - Config Generation erstellen
  → Mycelos: "Agent 'pdf-summarizer' ist registriert und bereit!"
```

---

## Gherkin als gemeinsame Sprache

### Beispiel: PDF-Email-Summarizer

User sagt: "Ich will dass neue PDFs in meinem Gmail zusammengefasst werden"

Creator generiert:

```gherkin
Feature: PDF Email Summarizer
  Als Benutzer moechte ich, dass PDF-Anhaenge in meinen Emails
  automatisch zusammengefasst werden.

  Scenario: Email mit PDF-Anhang erkennen
    Given eine neue Email mit einem PDF-Anhang ist eingetroffen
    When der Agent die Email verarbeitet
    Then soll er den PDF-Anhang erkennen

  Scenario: PDF zusammenfassen
    Given ein PDF-Dokument wurde erkannt
    When der Agent das PDF verarbeitet
    Then soll eine Zusammenfassung erstellt werden
    And die Zusammenfassung soll maximal 500 Woerter lang sein

  Scenario: Zusammenfassung speichern
    Given eine Zusammenfassung wurde erstellt
    When der Agent die Zusammenfassung speichert
    Then soll sie im Ordner "Summaries" abgelegt werden
    And der Dateiname soll das Datum und den Betreff enthalten

  Scenario: Email ohne PDF ignorieren
    Given eine neue Email ohne PDF-Anhang ist eingetroffen
    When der Agent die Email verarbeitet
    Then soll er sie ignorieren
```

Mycelos zeigt das dem User:
```
Mycelos> Ich habe folgende Szenarien fuer deinen Agent erstellt:

  1. Email mit PDF-Anhang erkennen
  2. PDF zusammenfassen (max 500 Woerter)
  3. Zusammenfassung in "Summaries" speichern
  4. Emails ohne PDF ignorieren

  Stimmt das so? Soll ich etwas aendern?
```

User: "Ja, aber die Zusammenfassung soll auch den Absender enthalten"

→ Scenario 2 wird aktualisiert, User bestaetigt erneut.

---

## Agent-Spec Datenmodell

```python
@dataclass
class AgentSpec:
    """Structured specification for a new agent."""
    name: str                          # "pdf-summarizer"
    description: str                   # User-Beschreibung
    use_case: str                      # Konkreter Use Case
    capabilities_needed: list[str]     # ["google.gmail.read", "http.get"]
    trigger: str                       # "on_demand" | "scheduled" | "event"
    effort: str                        # "trivial" | "small" | "medium" | "large" | "unrealistic"
    model_tier: str                    # "haiku" | "sonnet" | "opus"
    estimated_cost: float              # Geschaetzte Generierungskosten
    gherkin_scenarios: str             # Die bestatigten Gherkin-Scenarios
    user_language: str                 # "de" | "en" — fuer Gherkin-Sprache
```

---

## Prompts pro Phase

### Phase 1: Interview-Prompt (im ChatService, bei CREATE_AGENT)

```
Der User moechte einen neuen Agenten erstellen.

Fuehre ein kurzes, freundliches Interview:
1. Was genau soll der Agent tun? (konkreter Use Case)
2. Welche Daten/Services braucht er?
3. Wie oft soll er laufen?
4. Gibt es besondere Anforderungen?

Frage NUR was du wirklich wissen musst.
Wenn der User ungeduldig ist, fang mit dem Minimum an.
Fasse am Ende die Anforderungen zusammen und frage ob das stimmt.
```

### Phase 3: Gherkin-Prompt

```
Erstelle Gherkin-Scenarios fuer folgenden Agent:

{agent_spec}

Regeln:
- Schreibe in der Sprache des Users ({user_language})
- Maximal 5-7 Scenarios
- Jedes Scenario beschreibt EINEN konkreten Fall
- Vermeide technische Details — der User muss es verstehen
- Decke auch Fehlerfaelle ab (z.B. "Email ohne PDF")
- Nutze das Format: Feature / Scenario / Given-When-Then

Antworte NUR mit dem Gherkin-Text, kein anderer Output.
```

### Phase 4: Test-Prompt (TDD)

```
Erstelle pytest-Tests basierend auf diesen Gherkin-Scenarios:

{gherkin_scenarios}

Agent-Spec:
{agent_spec}

Regeln:
- Ein Test pro Scenario
- Tests muessen ohne den eigentlichen Agent-Code lauffaehig sein
  (mocke externe Aufrufe mit unittest.mock)
- Teste das Verhalten, nicht die Implementation
- Importiere den Agent als: from agent_code import {AgentClass}
- Nutze das Standard-Template: AgentInput → execute() → AgentOutput

Antworte NUR mit Python-Test-Code.
```

### Phase 5: Code-Prompt

```
Schreibe den Agent-Code der diese Tests erfuellt:

{test_code}

Agent-Spec:
{agent_spec}

Template:
from mycelos.sdk import run, progress
from mycelos.agents.models import AgentInput, AgentOutput

class {AgentName}:
    agent_id = "{agent_id}"
    agent_type = "{agent_type}"
    capabilities_required = [{capabilities}]

    def execute(self, input: AgentInput) -> AgentOutput:
        ...

Regeln:
- Nur import von mycelos.sdk und Python stdlib
- Externe Aufrufe NUR ueber run(tool="...", args={...})
- Immer progress() fuer Status-Updates
- Immer try/except um execute()

Antworte NUR mit Python-Code.
```

---

## Workflow als ManagedWorkflowExecutor

Der Creator-Workflow nutzt die neue Workflow-Infrastruktur:
- `workflow_runs` trackt den State
- Budget-Check nach jedem LLM-Call
- Pause bei Budget-Ueberschreitung
- Resume ab letztem Checkpoint
- Artefakte (Spec, Gherkin, Tests, Code) im Object Store

```python
# Vereinfacht:
def run_creator_workflow(app, spec):
    workflow = app.workflow_registry.get("create-agent")
    executor = ManagedWorkflowExecutor(
        run_manager=app.workflow_run_manager,
        agent_runner=creator_step_runner,  # dispatcht pro Step
        max_retries=3,
    )
    result = executor.start_and_execute(
        workflow, budget_limit=1.0,
    )
    return result
```

---

## Abbruch-Bedingungen

| Bedingung | Aktion |
|-----------|--------|
| Budget > Limit | Pause, User fragen |
| Tests 3x rot | Pause, User informieren |
| Audit 2x abgelehnt | Pause, User informieren |
| UNREALISTISCH-Rating | Sofort ablehnen, Alternative vorschlagen |
| User sagt "abbrechen" | Abort, Artefakte erhalten |
| Gherkin nicht bestaetigt | Zurueck zu Interview |

---

## Tasks

### Task 1: AgentSpec + Effort Classification

**Files:**
- Create: `src/mycelos/agents/agent_spec.py`
- Create: `tests/test_agent_spec.py`

**Was:**
- `AgentSpec` dataclass mit allen Feldern
- `classify_effort(spec) -> str` — TRIVIAL bis UNREALISTISCH
- `estimate_cost(spec) -> float` — basierend auf Effort + Tier
- Tests: Klassifikation fuer verschiedene Specs

---

### Task 2: Gherkin Generator

**Files:**
- Create: `src/mycelos/agents/gherkin_generator.py`
- Create: `tests/test_gherkin_generator.py`

**Was:**
- `generate_gherkin(spec, llm, model_chain) -> str` — LLM-Call mit fokussiertem Prompt
- `parse_gherkin_scenarios(gherkin_text) -> list[dict]` — extrahiert Scenario-Titel + Steps
- `format_for_user(scenarios) -> str` — benutzerfreundliche Zusammenfassung
- Tests mit MockLLMBroker

---

### Task 3: Test Generator (TDD)

**Files:**
- Create: `src/mycelos/agents/test_generator.py`
- Create: `tests/test_test_generator.py`

**Was:**
- `generate_tests(spec, gherkin, llm, model_chain) -> str` — pytest-Code aus Gherkin
- Fokussierter Prompt: nur Test-Code, kein anderer Output
- Tests mit MockLLMBroker

---

### Task 4: Code Generator

**Files:**
- Create: `src/mycelos/agents/code_generator.py`
- Create: `tests/test_code_generator.py`

**Was:**
- `generate_code(spec, tests, llm, model_chain, previous_error=None) -> str`
- Bei Retry: previous_error enthaelt den pytest-Output
- Template wird erzwungen (Agent-Klasse mit execute())
- Tests mit MockLLMBroker

---

### Task 5: Test Runner (Sandbox)

**Files:**
- Create: `src/mycelos/agents/test_runner.py`
- Create: `tests/test_agent_test_runner.py`

**Was:**
- `run_agent_tests(code, tests, sandbox) -> TestResult`
- TestResult: passed, output, error, duration
- Schreibt Code + Tests in temp-dir
- Fuehrt pytest in Sandbox aus (Timeout 30s)
- Tests: gruene Tests, rote Tests, Timeout

---

### Task 6: Creator Pipeline — End-to-End Orchestrierung

**Files:**
- Create: `src/mycelos/agents/creator_pipeline.py`
- Create: `tests/test_creator_pipeline.py`

**Was:**
- `CreatorPipeline(app)` — orchestriert den kompletten Flow
- Nutzt ManagedWorkflowExecutor mit "create-agent" Workflow
- `agent_runner` dispatcht pro Step-ID:
  - `feasibility` → classify_effort()
  - `generate-gherkin` → generate_gherkin()
  - `generate-tests` → generate_tests()
  - `generate-code` → generate_code()
  - `run-tests` → run_agent_tests()
  - `audit` → AuditorAgent.review()
  - `register` → AgentRegistry + Object Store
- Budget-Check, Retry-Loop, Abort-Handling
- Artefakte: Spec, Gherkin, Tests, Code → Object Store

---

### Task 7: Interview + Orchestrator Integration

**Files:**
- Modify: `src/mycelos/chat/service.py` (CREATE_AGENT Handling)
- Create: `tests/test_creator_integration.py`

**Was:**
- Bei CREATE_AGENT Intent: Interview-Kontext injizieren
- Nach Interview (User bestaetigt Spec): CreatorPipeline starten
- Nach Gherkin (User bestaetigt Scenarios): weiter mit Tests
- Ergebnisse dem User praesentieren
- Pending-Runs bei Pause/Budget

---

### Task 8: Update create-agent.yaml + E2E Tests

**Files:**
- Modify: `artifacts/workflows/create-agent.yaml` (Gherkin-Step hinzufuegen)
- Create: `tests/test_creator_e2e.py`

**Was:**
- YAML aktualisieren mit generate-gherkin Step
- E2E mit MockLLMBroker: Interview → Gherkin → Tests → Code → gruen → Audit → Register
- Abbruch-Szenarien: Budget, Tests rot, Audit abgelehnt, User bricht ab
- Artefakte nach Abbruch noch im Object Store

---

## Reihenfolge

```
Task 1 (AgentSpec)
    ↓
Task 2 (Gherkin) + Task 3 (Tests) + Task 4 (Code) — parallel
    ↓
Task 5 (Test Runner)
    ↓
Task 6 (Creator Pipeline)
    ↓
Task 7 (Interview + Integration)
    ↓
Task 8 (YAML + E2E)
```

## Was wir NICHT machen

- Kein Claude Code / Codex Integration (spaetere Ausbaustufe)
- Kein Multi-File Agent (ein Agent = eine Python-Datei)
- Kein Auto-Deploy (Agent wird registriert, User startet manuell)
- Keine Gherkin-Execution-Engine (Gherkin ist nur fuer User-Verstaendnis, pytest sind die echten Tests)
