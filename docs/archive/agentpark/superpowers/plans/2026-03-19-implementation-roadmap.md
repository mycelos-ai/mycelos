# Mycelos Implementation Roadmap

Stand: 2026-03-19

## Was existiert (Foundation, ~1.150 Zeilen)

| Komponente | Status | Tests |
|---|---|---|
| SQLiteStorage (WAL) | ✅ Fertig | 4/4 |
| ConfigGenerationManager | ✅ Fertig | 11/11 |
| SQLiteMemoryService (4 Scopes) | ✅ Fertig | 8/8 |
| SQLiteAuditLogger | ✅ Fertig | 4/4 |
| LiteLLMBroker | ✅ Fertig | 3/3 (blocked by import) |
| CLI: init, chat, config list/rollback | ✅ Fertig | — |
| Protocols: 6 Service Interfaces | ✅ Fertig | — |
| App (Service Container) | ✅ Fertig | — |

## Bekannter Widerspruch zur Spec

- `init_cmd.py` speichert API-Key als Klartext in `.env` → muss durch CredentialProxy ersetzt werden (Phase 2)

---

## Implementierungs-Reihenfolge

### Phase 1: Security Layer (Außenhülle)
*Gekapselt, wenig Abhängigkeiten nach innen. Kann unabhängig gebaut und getestet werden.*

**Schritt 1.1: Credential Proxy**
- Protocol existiert bereits in `protocols.py`
- Implementation: age-kompatible Verschlüsselung (X25519) mit MYCELOS_MASTER_KEY
- Encrypted Storage in SQLite (`credentials` Tabelle hinzufügen)
- `.env`-Hack in init_cmd.py ersetzen
- Tests: `tests/security/test_credential_isolation.py`
- Scenarios: SEC04, SEC10

**Schritt 1.2: Capability Token Manager**
- Neues Protocol + Implementation
- Zeitlich begrenzte, scope-beschränkte Tokens
- Token-Issuance, Validation, Revocation
- `capability_tokens` Tabelle in Schema
- Tests: `tests/security/test_capability_enforcement.py`
- Scenarios: SEC07, SEC08

**Schritt 1.3: Policy Engine**
- always/confirm/prepare/never Entscheidungslogik
- Vertrauens-Eskalation (User-Antworten werden gemerkt)
- `policies` Tabelle in Schema
- Property-based Tests mit Hypothesis
- Scenarios: CC04, CC12

**Schritt 1.4: Response Sanitizer**
- Reflektierte Tokens/Credentials aus Agent-Output entfernen
- Regex-basiert + Allowlist
- Tests: `tests/security/test_response_sanitization.py`
- Scenarios: SEC14, SEC19

**Ergebnis Phase 1:** Vollständiger Security Layer. Alle SEC-Scenarios sollten bestehen.

---

### Phase 2: Sandbox & Execution (Agent-Laufzeitumgebung)
*Baut auf Phase 1 auf — Sandbox braucht Capability Tokens und Credential Proxy.*

**Schritt 2.1: LocalSandbox (Development)**
- Protocol existiert bereits
- Subprocess mit gestripptem Environment
- `/input/`, `/workspace/`, `/output/` via temp-Verzeichnisse und Symlinks
- MYCELOS_SESSION_TOKEN Injection
- Timeout und Resource Limits (ulimit)
- **Expliziter Hinweis: Keine echte Sicherheitsgrenze**
- Tests: Sandbox-Ausbruchstests (sollten in LocalSandbox *teilweise* gelingen — das ist dokumentiert)
- Scenarios: SEC05, SEC06

**Schritt 2.2: IPC-Protokoll (JSON-RPC über STDIN/STDOUT)**
- SDK-Wrapper (`mycelos.sdk.run()`)
- Gateway-seitige Request-Handler
- Ephemeres Token-Auth
- Tool-Dispatch: `run(tool="artifacts.read", args={...})` → Security Layer → Execution
- Tests: IPC Round-Trip, Auth-Validation
- Scenarios: UC17

**Schritt 2.3: Zwei-Layer-Execution**
- Execution Layer: rohe Outputs, Exit Codes
- Presentation Layer: Truncation nach Datentyp (Text/JSON/CSV/Binary)
- Token-Budget pro Agent konfigurierbar
- Tests: Truncation-Tests für alle 5 Datentypen
- Scenarios: UC17

**Schritt 2.4: DockerSandbox (Production)** *(optional, kann warten)*
- Docker SDK Integration
- Bind-Mounts statt Symlinks
- Network Namespace (kein Netzwerk)
- cgroups für Resource Limits
- Standard-Image mit Pre-installed Libraries

**Ergebnis Phase 2:** Agents können isoliert ausgeführt werden. Tool-Calls fließen durch den Security Layer.

---

### Phase 3: Agent-System (Das Herzstück)
*Baut auf Phase 1 + 2 auf — Agents brauchen Sandbox, Security, LLM Broker.*

**Schritt 3.1: Agent Runtime Contract**
- `Agent(Protocol)` mit `execute()`, `validate_output()` (Spec Section 5)
- AgentInput/AgentOutput Dataclasses
- Agent Registry in SQLite (`agent_registry` Tabelle)
- Agent-Code im Filesystem (`artifacts/agents/<id>/code/`)
- Tests: Deterministischer Test-Agent (kein LLM nötig)
- Scenarios: UC03 (deterministischer OCR-Agent)

**Schritt 3.2: PlannerAgent (Orchestrierung)**
- Intent-Analyse: Was will der User?
- Workflow-Suche: Dreistufig (Top-10 → FTS5 → LLM)
- Fast Path für bewährte Workflows (Score >= 0.8)
- Plan-Erstellung: Workflow-Steps → Task → Execution
- Tests: Mock-Planner zuerst, dann LLM-basierter Planner
- Scenarios: UC21 (Ad-hoc Tasks), UC24 (Workflow Reuse)

**Schritt 3.3: EvaluatorAgent**
- Deterministische Checks (Format, Länge, Forbidden Content)
- LLM-basierte Evaluation (nur wenn deterministische Checks bestehen)
- Scoring-Update nach jedem Run
- Tests: Evaluation mit bekannten guten/schlechten Outputs
- Scenarios: UC08 (Kosten), UC05 (Reputation)

**Schritt 3.4: CreatorAgent**
- Agent-Code-Generierung via LLM
- Test-Generierung (Happy Path + Fehlerfälle)
- AuditorAgent Review Pipeline
- Registrierung via Blueprint Lifecycle
- Tests: End-to-End Agent-Erstellung
- Scenarios: UC01 (Email-Agent), UC22 (Onboarding Interview)

**Schritt 3.5: AuditorAgent**
- Unabhängiger Reviewer (hartcodierter Prompt)
- Code-Review: keine unauthorisierten Netzwerk-Calls, kein Filesystem-Escape
- Dependency-Review: Typosquatting-Check, Vulnerability-Check
- Unveränderbar durch andere Agents
- Tests: `tests/security/test_auditor_independence.py`
- Scenarios: SEC11, SEC15

**Ergebnis Phase 3:** Agents können erstellt, ausgeführt, bewertet und verbessert werden.

---

### Phase 4: Workflow Engine & Task Lifecycle
*Baut auf Phase 3 auf — braucht funktionierende Agents.*

**Schritt 4.1: Workflow YAML Parser**
- YAML-Schema Validation
- Step-Parsing mit Conditionals und Early-Exit
- Input/Output-Verknüpfung zwischen Steps
- Workflow-Registry (SQLite + Filesystem)
- Tests: Parse gültige/ungültige YAMLs
- Scenarios: UC09, UC24

**Schritt 4.2: Task Execution Engine**
- Task Lifecycle: pending → planning → running → completed/failed
- Checkpoint-Erstellung nach jedem Step
- Checkpoint-Contract: Artifacts + Result + optional state.json
- Budget-Enforcement (--max-cost)
- Tests: Multi-Step Workflow mit Checkpoints
- Scenarios: UC20 (Durable Execution)

**Schritt 4.3: Blueprint Lifecycle (vollständig)**
- 5 Phasen: resolve → verify → plan → apply → status
- Risiko-Klassifizierung (LOW/MEDIUM/HIGH/CRITICAL)
- Guard Period mit Health Monitor
- Auto-Rollback bei Fehlerrate > 40%
- User-Approval für MEDIUM+ Änderungen
- Tests: Vollständiger Lifecycle + Auto-Rollback
- Scenarios: CC01-CC20 (alle!), UC06, UC23

**Schritt 4.4: Scoring & Improvement Cycle**
- Workflow-Score Berechnung (Erfolgsrate × 0.4, Kosten × 0.25, Speed × 0.15, Stabilität × 0.2)
- Score-Schwellwerte → EvaluatorAgent Diagnose → PlannerAgent/CreatorAgent Verbesserung
- Workflow-Versioning (v1 → v2 → v3)
- Tests: Score-Berechnung, Improvement-Trigger
- Scenarios: UC05, UC24

**Ergebnis Phase 4:** Vollständige Workflow-Engine mit Scoring, Checkpoints und Blueprint Lifecycle.

---

### Phase 5: Connectors & Channels
*Baut auf Phase 1-4 auf — Connectors brauchen Security Layer, Channels brauchen Gateway.*

**Schritt 5.1: MCP Connector Framework**
- MCP Client Implementation
- Connector Setup Flow (`mycelos connector setup <name>`)
- MCP Tool-Schemas → Capabilities Registration
- Dry-Run Mode für schreibende MCP-Ops
- Tests: Mock MCP Server
- Scenarios: CC01 (Email), CC07 (GitHub), CC21 (Google OAuth)

**Schritt 5.2: Email Connector (Gmail/IMAP)**
- Google OAuth Flow (Browser → Callback → Token Storage)
- IMAP via MCP
- Inkrementelle Scopes
- Token Refresh
- Scenarios: UC01, UC22, CC21

**Schritt 5.3: Gateway (mycelos serve)**
- Async Event Loop (uvicorn)
- Local API (Unix Socket)
- Huey Consumer (Thread-Pool)
- Health Monitor
- Scenarios: UC07

**Schritt 5.4: Scheduler (Huey Integration)**
- SqliteHuey Setup
- Cron-Jobs, Intervall-Jobs
- Vorab-Freigabe Permission-Set
- Circuit Breaker + Dead Letter Queue
- Tests: Scheduled Task Lifecycle
- Scenarios: UC07, CC05

**Schritt 5.5: Notification Pipeline**
- 3-Stufen-Eskalation (Push → Email-Digest → Inbox)
- Notification Queue in SQLite
- Huey Delayed Tasks für Eskalation
- Email-Batching/Digest
- Quiet Hours
- Scenarios: UC26

**Schritt 5.6: Telegram Channel**
- Bot API Integration
- RichContent → Telegram HTML + Inline Keyboards
- Push Notifications
- Approval-Buttons
- Scenarios: UC04, UC26

**Ergebnis Phase 5:** Vollständiges System mit externen Integrationen.

---

### Phase 6: Artifact Management & Conversations
*Kann teilweise parallel zu Phase 5 gebaut werden.*

**Schritt 6.1: Artifact Storage**
- `artifacts` Tabelle + Filesystem-Layout
- Derivation Chain (derived_from + derivation_type)
- Artifact-Tools (artifacts.read/create/list/process)
- Content-Hashing (SHA-256)
- Scenarios: UC03, UC14, SEC13

**Schritt 6.2: Media Processing Pipeline**
- Mime-Type Erkennung
- Auto-OCR (Tesseract), Auto-Vision (Haiku), Auto-Schema
- Konfigurierbar pro Config-Generation
- Scenarios: UC03

**Schritt 6.3: Conversation Storage (JSONL)**
- Sessions-Tabelle + JSONL-Dateien
- Message-Index in SQLite
- Cross-Channel Session Identity
- Context-Komprimierung (compact_summary)
- Scenarios: UC25

**Ergebnis Phase 6:** Vollständiges Artifact- und Conversation-System.

---

## Abhängigkeitsgraph

```
Phase 1: Security Layer          ← unabhängig, sofort startbar
    |
Phase 2: Sandbox & Execution     ← braucht Phase 1
    |
Phase 3: Agent-System            ← braucht Phase 1 + 2
    |
Phase 4: Workflow & Blueprint    ← braucht Phase 3
    |
Phase 5: Connectors & Channels   ← braucht Phase 1-4
    |
Phase 6: Artifacts & Conversations ← teilweise parallel zu Phase 5

Parallel möglich:
  - Phase 6.1-6.3 können ab Phase 3 parallel gestartet werden
  - Phase 5.3-5.4 (Gateway/Scheduler) können ab Phase 2 gestartet werden
```

## Geschätzte Reihenfolge

| Phase | Fokus | Abhängigkeit |
|---|---|---|
| **1.1** | Credential Proxy | — (sofort) |
| **1.2** | Capability Tokens | 1.1 |
| **1.3** | Policy Engine | 1.2 |
| **1.4** | Response Sanitizer | — (parallel zu 1.2/1.3) |
| **2.1** | LocalSandbox | 1.1, 1.2 |
| **2.2** | IPC-Protokoll | 2.1 |
| **2.3** | Zwei-Layer-Execution | 2.2 |
| **3.1** | Agent Runtime | 2.1, 2.2 |
| **3.2** | PlannerAgent | 3.1 |
| **3.3** | EvaluatorAgent | 3.1 |
| **3.4** | CreatorAgent | 3.1, 3.3, 3.5 |
| **3.5** | AuditorAgent | 3.1 |
| **4.1** | Workflow YAML Parser | 3.2 |
| **4.2** | Task Execution Engine | 4.1 |
| **4.3** | Blueprint Lifecycle (voll) | 4.2 |
| **4.4** | Scoring & Improvement | 3.3, 4.2 |
| **5.1** | MCP Connector Framework | 1.1, 1.2 |
| **5.2** | Email/Gmail Connector | 5.1 |
| **5.3** | Gateway | 2.2 |
| **5.4** | Scheduler (Huey) | 5.3 |
| **5.5** | Notification Pipeline | 5.4 |
| **5.6** | Telegram Channel | 5.3 |
| **6.1** | Artifact Storage | 2.1 |
| **6.2** | Media Processing | 6.1 |
| **6.3** | Conversation Storage | 3.2 |
