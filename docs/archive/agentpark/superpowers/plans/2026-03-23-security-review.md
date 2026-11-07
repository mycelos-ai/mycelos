# Security Layer Review — IST-Zustand

**Datum:** 2026-03-23
**Status:** REVIEW (Analyse, nicht implementieren)

---

## Gesamt-Architektur (Design vs. Realitaet)

```
Design (7 Schichten):              Realitaet:
1. Credential Proxy          ✅ Gebaut, teilweise verdrahtet
2. Capability Tokens          ✅ Gebaut, NUR ExecutionRuntime
3. Policy Engine              ✅ Gebaut, NUR ExecutionRuntime
4. Mount Registry             ✅ Gebaut, NUR ChatService (filesystem)
5. Response Sanitizer         ✅ Gebaut, NUR ExecutionRuntime
6. Guardian                   ❌ NICHT GEBAUT
7. Audit Logger               ✅ Gebaut, aber nicht an Security-Events
```

## Das Kern-Problem

**Die Security-Primitives existieren und sind getestet — aber nicht in den Hauptpfad verdrahtet.**

- **ExecutionRuntime** (sandboxed Agents): ALLE 5 Checks aktiv ✅
- **ChatService** (User-Interaktion): KEINE Checks ausser Mounts ❌
- **WorkflowRunner** (Workflow-Steps): KEINE Checks ❌
- **MCP Manager** (externe Tools): KEINE Checks ❌

```
User → ChatService → _execute_tool() → Tool
                     ↑ HIER FEHLT ALLES:
                     - Kein Token
                     - Keine Policy
                     - Keine Sanitization
                     - Kein Guardian
```

## Komponenten-Status

### 1. CapabilityTokenManager
- **Gebaut:** ✅ (security/capabilities.py)
- **Verdrahtet:** ExecutionRuntime ✅ | Chat ❌ | Workflows ❌
- **Tests:** 12 Unit Tests + SEC07 Szenarien
- **Funktion:** Token mit Scope + Expiry + Rate Limit, validiert bei jedem Tool-Call

### 2. PolicyEngine
- **Gebaut:** ✅ (security/policies.py)
- **Verdrahtet:** ExecutionRuntime ✅ | Chat ❌ | Workflows ❌
- **Tests:** 12 Unit Tests + Hypothesis + SEC08 Szenarien
- **Funktion:** always/confirm/prepare/never pro User×Agent×Resource
- **Offen:** "prepare" und "confirm" Flow nicht implementiert

### 3. ResponseSanitizer
- **Gebaut:** ✅ (security/sanitizer.py)
- **Verdrahtet:** ExecutionRuntime ✅ | Chat ❌ | Workflows ❌
- **Tests:** 8 Unit Tests + SEC14 Szenarien
- **Funktion:** Redacted API Keys, SSH Paths, Base64 Credentials

### 4. MountRegistry
- **Gebaut:** ✅ (security/mounts.py)
- **Verdrahtet:** Chat (filesystem) ✅ | ExecutionRuntime ❌ | Workflows ❌
- **Tests:** 8 Unit Tests + Path Traversal Tests

### 5. CredentialProxy
- **Gebaut:** ✅ (security/credentials.py, AES-256-GCM)
- **Verdrahtet:** LLM Broker ✅ | MCP (teilweise) ⚠️ | Chat ❌
- **Offen:** Credentials als env vars geladen, nicht per-call

### 6. Guardian
- **Gebaut:** ❌
- **Design:** Post-Execution Output-Check (Prompt Injection, Exfiltration)
- **Prio:** P1 — wird gebraucht wenn Agents autonom arbeiten

---

## Blueprint-Lifecycle (offen)

Das Blueprint-Thema aus der Spec (Section 9) definiert 5 Phasen fuer Config-Aenderungen:

```
proposed → dry-run → review → active → archived
```

Mit Risk-Classification:
- **low-risk:** Auto-approved (z.B. Memory write)
- **medium-risk:** Dry-run required (z.B. neuer Connector)
- **high-risk:** Human review required (z.B. neuer Agent, Policy-Aenderung)
- **critical:** Human + Audit (z.B. Credential rotation)

**Status:** NICHT IMPLEMENTIERT. Aktuell werden alle Config-Aenderungen direkt applied ohne Risiko-Bewertung.

---

## MCP Security Gaps (aus separatem Audit)

12 Findings, davon:
- 3 Critical: Command Injection, fehlende Capability Enforcement, Template Injection
- 4 High: Tool Shadowing, keine Subprocess-Isolation, Netzwerk-Zugriff, keine Trust-Levels
- Siehe: separater MCP Security Audit Report

---

## Remediation-Plan

### Phase 1: Security Gate in ChatService (BLOCKER)
1. `_execute_tool()` bekommt Security-Check:
   - Policy Engine evaluieren
   - Response Sanitizer auf Ergebnis
   - Audit Event loggen
2. Kein Capability Token noetig fuer Chat (User IS the authority)
   - Aber Policy muss gelten (User kann sich selbst Dinge verbieten)

### Phase 2: Security Gate in WorkflowRunner
1. Pro Workflow-Step: Policy check
2. Pro Workflow-Step: Response Sanitizer
3. Mount-Validation fuer Filesystem-Steps

### Phase 3: MCP Hardening
1. Command Validation (Allowlist + Shell-Metachar-Blocklist)
2. Tool-Name Validation (kein Shadowing)
3. Trust-Levels (recipe vs. custom)
4. Template-Fix in Recipes

### Phase 4: Blueprint Lifecycle
1. Risk Classification bei Config-Aenderungen
2. Dry-Run fuer medium/high risk
3. Human Review fuer high/critical
4. Audit Trail fuer alle Aenderungen

### Phase 5: Guardian
1. Post-Execution Output Check
2. Pattern Detection (Injection, Exfiltration)
3. Integration in Chat + Workflow + Execution

---

## Fazit

Die Fundamente sind solide — jeder Security-Baustein hat Unit Tests und funktioniert isoliert.
Das Problem ist rein architektonisch: Die Bausteine sind nur im ExecutionRuntime verdrahtet,
nicht im ChatService und nicht im WorkflowRunner.

**Gesamtabdeckung: ~45%**

Die kritischste Luecke ist der ChatService — dort passiert 90% der User-Interaktion,
aber null Security-Checks (ausser Mounts). Das ist der erste Fix.
