# Security Findings: Prozessgrenzen, Credential-Isolation und LLM-Containment

**Datum:** 2026-03-24  
**Status:** Findings / Entscheidungsvorlage  
**Kontext:** Zielbild ist, dass LLMs niemals direkte Credential-Einsicht oder unkontrollierten Datenzugriff erhalten.

---

## 1) Executive Summary

Die aktuelle Architektur hat bereits starke Security-Bausteine (Capability Tokens, Policy Engine, Sanitizer, Credential Proxy), aber sie sind nicht durchgängig in allen Hauptpfaden erzwungen. Dadurch entsteht eine relevante Lücke zwischen Design und tatsächlicher Laufzeit-Sicherheit.

**Kernaussage:**
- Ein Single-Process-Ansatz kann sicher sein, **wenn** alle Sicherheitsgrenzen zentral, unvermeidbar und durchgängig erzwungen werden.
- In der aktuellen Lage ist das nicht vollständig gegeben; daher ist das Risiko erhöht.
- Für das Ziel „LLM sieht nie Credentials“ ist ein **separater Credential-Prozess/Broker** dringend empfohlen.
- Externe/high-risk Gateways (insb. MCP/Custom-Tools/Shell-nahe Integrationen) sollten in **separaten, gehärteten Worker-Prozessen** laufen.

---

## 2) Feststellungen (Findings)

### F1 — Security-Primitives existieren, sind aber nicht überall verdrahtet

Die Sicherheitsmechanismen sind implementiert, jedoch laut Security-Review nur in Teilbereichen verbindlich aktiv. Besonders ChatService, WorkflowRunner und MCP-Pfade haben Lücken.

**Risiko:** LLM-nahe Flows können an Security-Checks vorbeiführen, obwohl dieselben Checks im ExecutionRuntime vorhanden sind.

---

### F2 — Credential-Isolation ist begonnen, aber nicht Ende-zu-Ende erzwungen

Der CredentialProxy (AES-256-GCM) ist ein wichtiger Schritt. Dennoch reicht Verschlüsselung „at rest“ allein nicht aus, wenn Entschlüsselung/Verwendung in zu breiten Prozesskontexten möglich ist.

**Risiko:** Bei Prozesskompromittierung oder unzureichender Call-Isolation können Secrets trotz verschlüsselter Speicherung indirekt exfiltriert werden.

---

### F3 — Fehlende harte Trennung zwischen LLM-Intent und privilegierter Ausführung

Aktuell ist das Risiko gegeben, dass Modell-Ausgaben (direkt oder indirekt) zu Aktionen führen, die über sichere Boundaries hinausgehen.

**Risiko:** „Agent baut sich selbst Wege“ (z. B. indirekter Zugriff auf DB/Secrets über ungenügend kontrollierte Toolpfade).

---

### F4 — MCP-/Gateway-Fläche ist ein multiplikativer Risikofaktor

Je offener Tooling und externe Integrationen, desto höher die Angriffsfläche (Injection, Shadowing, unsichere Templates, unkontrollierter Egress).

**Risiko:** Selbst gute Kernkomponenten verlieren Wirkung, wenn Erweiterungspunkte nicht gleichwertig gehärtet sind.

---

## 3) Sicherheitsprinzipien (Soll-Zustand)

1. **LLM ist nie Vertrauensanker.**
   - LLM erzeugt Intent, nie finale Autorisierung.
2. **Deny-by-default.**
   - Jede Aktion braucht explizite Capability + Policy-Entscheidung.
3. **Keine Secret-Übergabe an LLM.**
   - Weder direkt im Prompt noch indirekt als Tool-Output.
4. **Unumgehbare Policy-Gates.**
   - Vor jedem Tool-/DB-/Netzwerkzugriff, unabhängig vom Einstiegspfad.
5. **Isolation schlägt Konvention.**
   - Kritische Funktionen in eigene Prozesse/Sandboxes.
6. **Auditierbarkeit als Sicherheitskontrolle.**
   - Alle sensitiven Entscheidungen und Zugriffe müssen nachvollziehbar sein.

---

## 4) Architekturentscheidung: Was auslagern, was im Prozess lassen?

## 4.1 Credential-Management

**Empfehlung:** In separaten Credential-Broker-Prozess auslagern (muss für High-Assurance).

**Mindestanforderungen:**
- Master-Key nicht im LLM-Orchestrator-Prozess.
- Zugriff nur über lokale, authentisierte Broker-API (z. B. Unix Socket + mTLS/Peer-Checks).
- Broker gibt bevorzugt kurzlebige, zweckgebundene Tokens/Derived Credentials statt Rohsecrets.
- Strikte Operation-Allowlist, kein freier „get all secrets“-Pfad.

---

## 4.2 Gateway-/Tool-Ausführung

**Empfehlung:** Hybrid-Modell.

- **Im Prozess erlaubt (low risk):** deterministische, rein interne Funktionen ohne Secret-/Netzwerk-Reichweite.
- **Auslagern (medium/high risk):** MCP-Connectoren, Shell-nahe Tools, generische HTTP/DB-Tools, untrusted Plugins.

**Ziel:** Blast-Radius reduzieren; Kompromittierung eines Workers darf nicht zur Secret- oder Control-Plane-Kompromittierung führen.

---

## 5) Konkrete Guardrails gegen „LLM baut Agenten mit DB/Secret-Zugriff“

1. **Keine dynamische Tool-Registrierung durch Modellausgabe.**
2. **Kein Raw-SQL-Tool für LLM.** Nur kuratierte, parametrisierte Operationen.
3. **Capability Tokens pro Schritt:** enges Scope, kurze TTL, Audience-Bindung.
4. **Policy-Engine überall erzwingen:** Chat, Workflow, Runtime, MCP.
5. **OS-level Egress-Kontrolle:** nur erlaubte Ziele/Ports pro Worker.
6. **Filesystem-Mount-Whitelists + Path-Normalisierung.**
7. **Output-Guardian/DLP:** Secret- und Exfil-Pattern blockieren/redacten.
8. **Tool-Allowlist + Name-Collision/Shadowing-Schutz.**
9. **Human-in-the-loop für high/critical Aktionen.**
10. **Tamper-evidentes Audit für alle sensitiven Events.**

---

## 6) Priorisierte Umsetzung (Roadmap)

### P0 (sofort)
- Security-Gates in **ChatService** und **WorkflowRunner** erzwingen.
- Einheitlicher Enforcement-Wrapper für Tool-Calls (Policy + Capability + Sanitizer + Audit).

### P1 (kurzfristig)
- Credential-Broker in separaten Prozess verlagern.
- Secret-Access nur per-call, keine breiten Env-Exposure-Pfade.
- MCP-Hardening (Validation, Trust-Levels, Template-Härtung, Shadowing-Schutz).

### P2 (mittelfristig)
- Guardian als verbindliche Post-Execution-Kontrolle.
- Blueprint-/Change-Lifecycle mit Risk-Klassen und verpflichtendem Review-Flow.

---

## 7) Entscheidungslogik für Architektur-Review

Eine Auslagerung ist **verpflichtend**, wenn mindestens eines gilt:

- Komponente kann Secrets entschlüsseln oder weiterreichen.
- Komponente hat generischen Netzwerkzugang zu externen Zielen.
- Komponente führt modelldefinierte, offene Befehle aus (Shell/HTTP/DB/generische Connectoren).
- Komponente lädt untrusted Erweiterungen (Plugins/Tools/Templates).

Wenn keines davon gilt und vollständige zentrale Gates nachweislich erzwungen sind, ist In-Process akzeptabel.

---

## 8) Abnahmekriterien (Security Acceptance)

- LLM erhält in keinem Pfad Rohcredentials.
- Jeder externe Zugriff ist durch Policy + Capability + Audit belegt.
- Secret-Exfiltration-Tests schlagen fehl (gewünschtes Verhalten: Block/Redaction).
- Pen-Test-Szenarien für MCP/Connectoren bestehen.
- Rollback/Change-Prozesse für Security-relevante Konfiguration sind kontrolliert und auditiert.

---

## 9) Kurzfazit

Für euer Sicherheitsziel reicht „alles in einem Prozess“ **nur dann**, wenn Security enforcement wirklich unumgehbar und überall gleich ist. Der aktuelle Stand deutet auf relevante Integrationslücken hin. Deshalb ist die sichere, pragmatische Richtung:

1) Security-Gates überall schließen,  
2) Credential-Management strikt auslagern,  
3) high-risk Gateways/Connectoren isolieren.

Damit verhindert ihr, dass die LLM – direkt oder indirekt – an Pfade kommt, die sie niemals kontrollieren dürfen.
