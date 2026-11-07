# Architektur-Audit: Mycelos vs. OpenClaw

**Datum:** 20. März 2026
**Auditor:** Unabhängige Architektur-Analyse
**Status Mycelos:** Aktive Entwicklung (Milestone 1 ~80%)
**Status OpenClaw:** Produktiv / Fortgeschritten

---

## 1. Executive Summary

Mycelos und OpenClaw verfolgen dasselbe Grundziel — ein autonomes Agent-Betriebssystem — aber mit fundamental verschiedenen Philosophien. **Mycelos** setzt auf Security-First mit immutabler Konfiguration und eigenen Abstraktionen ohne Framework-Abhängigkeiten. **OpenClaw** setzt auf Channel-First mit einem ausgereiften Multi-Plattform-Messaging-System und einem Plugin-Ökosystem.

**Kernurteil:** Mycelos hat die stärkere architektonische Grundlage für ein sicheres, erweiterbares Agent-OS. OpenClaw hat die ausgereiftere Implementierung und reale Produkterfahrung. Beide Systeme haben signifikante blinde Flecken, die das jeweils andere System besser löst.

---

## 2. Architektur im Überblick

### Mycelos: Schichten-Architektur (Bottom-Up)

```
Channel Layer          Terminal, Telegram, Slack          → GEPLANT
Control Layer          Planner, Creator, Evaluator, Auditor → TEILWEISE
Execution Layer        Sandbox, CLI Runtime, Workflows     → TEILWEISE
Security Layer         Credentials, Capabilities, Policies → IMPLEMENTIERT
Storage Layer          SQLite WAL + Filesystem              → IMPLEMENTIERT
```

Mycelos baut von unten nach oben: Zuerst die Sicherheitsschicht, dann Storage, dann Execution. Die Channels kommen zuletzt. Jede Schicht ist durch Python Protocols definiert — keine Vererbung, kein Framework.

### OpenClaw: Gateway-zentrierte Architektur (Channel-First)

```
Channel Layer       20+ Plattformen (Telegram, Discord, Slack, Signal, iMessage...)
    ↓
Gateway             WebSocket-Server als zentrale Steuerung
    ↓
Agent Runtime       Session-basierte Agenten mit Plugin-System
    ↓
Tool/Sandbox Layer  Exec-Approvals, optionale Sandbox
    ↓
Storage             JSONL Sessions + sqlite-vec Memory
```

OpenClaw baut von der Nutzerinteraktion nach innen: Zuerst die Channels, dann das Gateway-Routing, dann die Agent-Ausführung. Die Architektur optimiert für Konversations-Kontinuität über Plattformen hinweg.

### Bewertung

Mycelos's Bottom-Up-Ansatz ist architektonisch sauberer — Sicherheit und Storage als Fundament zu haben verhindert, dass diese später nachgerüstet werden müssen. OpenClaw's Channel-First-Ansatz hat den Vorteil, dass das System sofort nützlich ist (es kann über 20 Plattformen kommunizieren), aber die Sicherheitsschicht ist weniger durchdacht.

---

## 3. Security-Modell — Der größte Unterschied

### Mycelos: Defense-in-Depth

Mycelos implementiert vier ineinandergreifende Sicherheitsmechanismen:

**Credential Isolation** (`src/mycelos/security/credentials.py`): AES-256-GCM-Verschlüsselung mit HKDF-abgeleiteten Schlüsseln. Credentials werden pro Eintrag mit eigenem Nonce verschlüsselt und sind niemals im Klartext — weder in der Datenbank, noch im Speicher der Agenten. Der Master Key (`MYCELOS_MASTER_KEY`) existiert ausschließlich im Gateway-Prozess.

**Capability Tokens** (`src/mycelos/security/capabilities.py`): Jeder Agent erhält kurzlebige, scope-beschränkte Tokens (z.B. `["email.read"]`) mit TTL und Rate-Limiting. Tokens sind UUID-basiert, revokierbar und kaskadierend löschbar pro Agent.

**Policy Engine** (`src/mycelos/security/policies.py`): Vierstufiges Entscheidungsmodell — `always`, `confirm`, `prepare`, `never`. Policies gelten pro Agent und pro Ressource und sind auditierbar.

**Prozess-Isolation** (`src/mycelos/execution/sandbox.py`): Agenten laufen in separaten Prozessen mit JSON-RPC-IPC. Die Umgebung wird gestripped — nur `MYCELOS_SESSION_TOKEN` und minimale Pfade sind sichtbar. Allerdings ist aktuell nur `LocalSandbox` implementiert, der explizit keine Sicherheitsgrenze ist. DockerSandbox ist geplant, aber nicht gebaut.

**Geplant aber fehlend:** Response Sanitization (Regex-Patterns für API-Key-Redaktion), Guardian Checks (Erkennung von Exfiltrations-Versuchen), DockerSandbox.

### OpenClaw: Trust-Boundary-Modell

OpenClaw verfolgt ein anderes Sicherheitskonzept: Ein Gateway = ein vertrauenswürdiger Operator. Die Sicherheit basiert auf:

**Operator Scopes** (`src/gateway/method-scopes.ts`): Least-Privilege per Gateway-Methode. Jede CLI-Operation hat definierte Scopes.

**Auth-System** (`src/gateway/auth.ts`): Token/Password-Authentifizierung mit Rate-Limiting und Auth-Mode-Policies. Read-Only-Modus für Audit-Zwecke möglich.

**Exec-Approvals**: Agenten-Befehle können über Allowlists oder Ask-UI genehmigt werden. Dies ist allerdings ein Guardrail, keine Multi-Tenant-Grenze.

**Sandbox (optional):** Standardmäßig deaktiviert (`mode: off`). Kann auf `non-main` oder `all` gesetzt werden. Temporäre Dateien unter `/tmp/openclaw`.

### Vergleichstabelle Security

| Dimension | Mycelos | OpenClaw |
|---|---|---|
| Credential-Verschlüsselung | AES-256-GCM, niemals Klartext | Env-Vars, Secret-Refs zur Laufzeit |
| Capability Tokens | UUID + Scope + TTL + Rate-Limit | Nicht vorhanden |
| Policy Engine | 4-Stufen (always/confirm/prepare/never) | Operator-Scope-basiert |
| Sandbox Standard | LocalSandbox (nicht sicher), Docker geplant | Aus (Host-Ausführung) |
| Threat Model | Dokumentiert im Spec (20 SEC-Szenarien) | trust.openclaw.ai, Prompt-Injection out-of-scope |
| Plugin-Isolation | Geplant (Sandbox pro Agent) | Keine (gleicher Prozess, gleiche Rechte) |
| Audit Trail | Dedizierte audit_events-Tabelle | Nicht formal dokumentiert |

### Urteil Security

**Mycelos ist hier klar führend.** Das Vier-Säulen-Modell (Credentials + Capabilities + Policies + Isolation) ist architektonisch das stärkere Fundament. Die Implementierung ist allerdings noch nicht komplett — insbesondere fehlt DockerSandbox, was die Prozess-Isolation in der Praxis aushöhlt. OpenClaw's Trust-Modell ist pragmatisch (ein Operator pro Gateway ist eine vernünftige Annahme für Personal-Assistenten), aber nicht skalierbar für Multi-Tenant-Szenarien. Die fehlende Plugin-Isolation ist ein echtes Risiko.

---

## 4. Konfigurations-Management

### Mycelos: NixOS-Stil Generationen

(`src/mycelos/config/generations.py`)

Jede Konfigurationsänderung erzeugt eine neue Generation — ein Append-Only-Log mit SHA-256-Content-Addressing. Identische Konfigurationen werden dedupliziert. Rollback ist O(1) — ein einzelner Pointer-Update. Jede Generation hat einen Parent-Verweis, was einen vollständigen Änderungsbaum ergibt.

Dieses Modell ist elegant, weil es eine der häufigsten Fehlerquellen in Agent-Systemen adressiert: "Warum verhält sich der Agent plötzlich anders?" Mit Generationen kann man zu jedem Zeitpunkt die exakte Konfiguration nachvollziehen und bei Problemen sofort zurückrollen.

### OpenClaw: JSON5-Config mit Zod-Validierung

(`src/config/zod-schema*.ts`)

Umfassende Konfiguration in `~/.openclaw/openclaw.json` mit Zod-Schema-Validierung. Unterstützt per-Agent-, per-Channel- und per-Sandbox-Konfiguration. Ein Onboarding-Wizard (`openclaw onboard`) führt durch das Setup. Legacy-Migrationen sind eingebaut.

Es gibt eine Art Snapshot-Mechanismus, aber kein formales Generationen-System mit atomarem Rollback.

### Urteil

**Mycelos's Generationen-System ist architektonisch überlegen.** Es ist deterministisch, nachvollziehbar und rollback-fähig. OpenClaw hat die reichhaltigere Konfiguration (20+ Channel-Optionen, per-Agent-Settings), aber ohne formales Versioning ist eine Konfigurationsänderung potenziell destruktiv.

---

## 5. Agent-Modell

### Mycelos: Agenten-Spektrum mit Kostenoptimierung

Mycelos definiert Agenten als Spektrum von deterministisch (Kosten ~0) über Light-Model (Haiku) bis Heavy-Model (Opus). Fünf System-Agenten (Planner, Creator, Evaluator, Auditor, Optimizer) orchestrieren die Ausführung. Jeder Agent implementiert ein Python Protocol und kommuniziert über standardisierte `AgentInput`/`AgentOutput`-Datenstrukturen (frozen dataclasses).

Das Besondere: Der CreatorAgent soll neue Agenten generieren können (Code + Tests), die dann vom AuditorAgent geprüft und erst nach menschlicher Bestätigung registriert werden. Diese Fähigkeit ist aktuell nur als Stub implementiert — der Creator kann noch keinen Code generieren.

Die Kostenoptimierung ("cheapest first") ist ein kluger Design-Entscheid: Deterministic > Haiku > Sonnet > Opus. Für einfache Aufgaben wird kein teures Modell verschwendet.

### OpenClaw: Channel-basiertes Routing mit Session-Identität

OpenClaw organisiert Agenten um Sessions und Channels. Jeder Agent hat eine Konfiguration (Modell, Tools, Sandbox-Policy) und ist an spezifische Channels gebunden. Das Session-System ist ausgereift: Session-Keys basieren auf Route + Agent-Identity + Kontext, mit Reply-Back-Routing und Group-Isolation.

Subagenten werden über ACP (Agent Control Protocol) via Prozess-Spawning ausgeführt. Aktivierungsmodi (mute/sleep/active), Queue-Modi und Thinking-Levels (low/medium/high) ermöglichen feingranulare Steuerung.

OpenClaw's Stärke ist die Konversations-Kontinuität: Ein Agent kann über Telegram starten und über Discord weitermachen, mit vollständiger Session-Historie in JSONL-Dateien.

### Urteil

**Unterschiedliche Stärken.** Mycelos's Agenten-Spektrum und Kostenoptimierung sind architektonisch besser durchdacht. OpenClaw's Session-Routing und Channel-Binding sind in der Praxis weiter fortgeschritten. Mycelos's größte Schwäche hier: Der CreatorAgent — das Kernfeature — ist noch ein Stub. OpenClaw hat keine vergleichbare selbst-evolvierende Fähigkeit geplant.

---

## 6. LLM-Integration

### Mycelos: Eigener Broker über LiteLLM

(`src/mycelos/llm/broker.py`)

Eigene `LLMBroker`-Abstraktion über LiteLLM für Multi-Provider-Support. Token-Counting ohne API-Call (Pre-Flight-Kostenschätzung), akkumuliertes Cost-Tracking, Streaming vorbereitet aber nicht verdrahtet. Keine Framework-Abhängigkeit — LiteLLM ist austauschbar durch direkte API-Calls.

### OpenClaw: Direkte API-Calls mit Failover

Direkte Provider-spezifische Integrationen für OpenAI, Anthropic, DeepSeek, Gemini, Minimax, Bedrock, Ollama. Auth-Profile mit Rotation und Cooldown. Tool-Streaming und Block-Streaming für Channel-Delivery. "Thinking"-Mode-Support für Claude und GPT-4o.

### Urteil

**OpenClaw ist hier praktisch weiter.** Direktes API-Integration mit Failover und Rotation ist production-ready. Mycelos's LiteLLM-Ansatz ist flexibler (ein Abstraction-Layer für alle Provider), aber Streaming, Failover und Rotation fehlen noch. Mycelos's Vorteil: Kein Framework-Lock-in.

---

## 7. Speicher und Persistenz

### Mycelos: SQLite WAL mit formalem Schema

(`src/mycelos/storage/database.py`, `schema.sql`)

11 Tabellen mit Foreign Keys, Indizes und Unique Constraints. WAL-Mode für concurrent Reads. Kein ORM — parametrisierte SQL-Queries. Das Schema deckt Tasks, Agents, Plans, Attempts, Config Generations, Memory, Audit Events, Workflow Events, Credentials, Capability Tokens und Policies ab.

Memory hat vier Scopes (System, Agent, Shared, Session) mit TTL-Support für Session-Scope.

### OpenClaw: JSONL Sessions + sqlite-vec

Session-Persistenz in JSONL-Dateien pro Agent/Session. Vector-DB via sqlite-vec für Embeddings (Voyage, Mistral). Zusätzlich `MEMORY.md` als Klartext-Memory. Media-Store mit Lifecycle-Management.

### Urteil

**Mycelos hat das strukturiertere Speichermodell.** 11 Tabellen mit FK-Constraints vs. JSONL-Dateien — Mycelos's relationales Modell ist für komplexe Queries und Audit-Trails besser geeignet. OpenClaw's JSONL ist einfacher, aber für analytische Abfragen ("Welcher Agent hat in den letzten 7 Tagen am meisten Kosten verursacht?") nicht geeignet. OpenClaw hat dafür bereits Vector-Search (sqlite-vec), was Mycelos erst plant.

---

## 8. Externe Integrationen

### Mycelos: MCP-Connector-Architektur (geplant)

Das Design sieht MCP-basierte Connectors vor (Email, GitHub, Web, Shell), die als eigene MCP-Server im Sandbox laufen. Der CLI-Befehl `mycelos connector setup` existiert, aber kein Connector ist implementiert. Credentials fließen über den CredentialProxy — Agenten sehen nie rohe API-Keys.

### OpenClaw: 20+ Native Channel-Integrationen

Telegram (grammY), Discord (discord.js), Slack (Bolt), WhatsApp (Baileys), Signal (signal-cli), iMessage, IRC, Microsoft Teams, Matrix, Feishu, LINE, Mattermost, Nextcloud Talk, Nostr, Synology Chat, Tlon, Twitch, Zalo. DM-Pairing-Sicherheit, Group-Routing, Channel-Health-Monitoring.

Zusätzlich MCP-Support via `@agentclientprotocol/sdk` und Playwright für Browser-Automation.

### Urteil

**OpenClaw ist hier haushoch überlegen** — 20+ produktive Channel-Integrationen vs. null implementierte Connectors. Dies ist Mycelos's größte praktische Schwäche. Mycelos's Design (MCP-basiert, Sandbox-isoliert, Credential-Proxy-geschützt) ist allerdings architektonisch stärker — wenn es implementiert wäre.

---

## 9. Testing

### Mycelos

TDD-Workflow mit Pflicht-Tests für jede Änderung. 100+ Testdateien, 4 Security-Invarianten-Tests, Hypothesis-ready für Property-Based-Testing. 60 Gherkin-Szenarien definiert (aber noch nicht als pytest implementiert). Security-Tests dürfen niemals brechen.

### OpenClaw

Vitest mit V8-Coverage und 70%-Schwellenwert für Lines/Branches/Functions. E2E-Tests mit Docker-Sandbox. Live-Tests mit echten API-Keys optional. Co-located Test-Dateien (`*.test.ts`).

### Urteil

**Beide sind stark**, mit leichtem Vorteil Mycelos wegen der expliziten Security-Invarianten und der 60 Gherkin-Szenarien als Acceptance-Criteria. OpenClaw hat die höhere Coverage-Disziplin (70%-Mandate).

---

## 10. Stärken und Schwächen — Zusammenfassung

### Mycelos: Stärken

1. **Security-First-Architektur** — Vier ineinandergreifende Sicherheitsschichten, in den Grundstein eingebaut
2. **Immutable Config Generations** — NixOS-Style Rollback, Content-Addressing, vollständige Nachvollziehbarkeit
3. **Protocol-basiertes Design** — Keine Framework-Abhängigkeit, jede Komponente austauschbar
4. **Agenten-Spektrum** — Kostenoptimierung von deterministisch bis Opus
5. **Formales Speichermodell** — 11 Tabellen mit Constraints, Audit-Trail eingebaut
6. **Umfassende Spezifikation** — 5000+ Zeilen Spec, 60 Gherkin-Szenarien

### Mycelos: Schwächen

1. **CreatorAgent nicht funktional** — Das Kern-Feature (autonome Agent-Erstellung) ist ein Stub
2. **Keine produktive Sandbox** — LocalSandbox ist explizit keine Sicherheitsgrenze
3. **Null Connectors implementiert** — Kein Agent kann aktuell mit externen Services interagieren
4. **Response Sanitization unvollständig** — Credential-Leaks über Agent-Output möglich
5. **Kein Streaming** — Lange Operationen geben dem Nutzer kein Feedback
6. **Guardian Checks fehlen** — Exfiltrations-Erkennung nur designed, nicht gebaut

### OpenClaw: Stärken

1. **20+ Channel-Integrationen** — Sofort nutzbar über alle relevanten Messaging-Plattformen
2. **Session-Kontinuität** — Konversationen über Plattformen hinweg mit vollem Kontext
3. **Plugin-Ökosystem** — Registry-basierte Erweiterbarkeit für Channels, Tools, Services
4. **Companion Apps** — macOS Menu-Bar, iOS/Android-Nodes
5. **Vector-Memory** — sqlite-vec für semantische Suche bereits implementiert
6. **Ausgereiftes LLM-Management** — Failover, Rotation, Thinking-Modes, Streaming

### OpenClaw: Schwächen

1. **Schwaches Security-Modell** — Ein Operator pro Gateway, keine Credential-Verschlüsselung
2. **Plugin-Trust-Lücke** — Installierte Plugins haben volle Prozess-Rechte
3. **Sandbox standardmäßig aus** — Host-Ausführung als Default ist riskant
4. **Kein formales Config-Versioning** — Konfigurationsänderungen nicht atomisch rollback-fähig
5. **Kein Multi-Tenant-Support** — By Design, aber limitiert die Einsatzszenarien
6. **Kein Audit-Trail** — Keine formale Protokollierung von Security-Events

---

## 11. Architektonische Führung — Gesamtbewertung

### Bewertungsmatrix (1-5, 5 = Beste)

| Dimension | Mycelos | OpenClaw | Kommentar |
|---|:---:|:---:|---|
| **Security-Architektur** | 5 | 2 | Mycelos's 4-Schichten-Modell vs. Trust-Boundary |
| **Config-Management** | 5 | 3 | Generationen vs. einfache JSON-Config |
| **Agent-Modell** | 4 | 4 | Verschiedene Stärken (Spektrum vs. Routing) |
| **LLM-Integration** | 3 | 5 | OpenClaw production-ready, Mycelos noch basic |
| **Externe Integrationen** | 1 | 5 | 0 Connectors vs. 20+ Channels |
| **Speicher-Architektur** | 4 | 3 | Relational + Formal vs. JSONL + Vector |
| **Testing** | 4 | 4 | Beide stark, verschiedene Schwerpunkte |
| **Produktreife** | 2 | 4 | Mycelos in Entwicklung, OpenClaw produktiv |
| **Erweiterbarkeit** | 4 | 5 | Beide gut, OpenClaw hat das Plugin-Ökosystem |
| **Code-Qualität** | 5 | 4 | Protocols + Type Hints + Frozen Dataclasses |
| **Dokumentation** | 5 | 3 | 5000+ Zeilen Spec vs. README-basiert |
| **Zukunftsfähigkeit** | 5 | 3 | Mycelos's Fundament skaliert besser |
| **Durchschnitt** | **3.9** | **3.75** | |

### Fazit

**Mycelos führt architektonisch**, besonders in Security, Config-Management und Code-Qualität. Die Grundlage ist so solide, dass die fehlenden Features (Connectors, CreatorAgent, DockerSandbox) ohne größeres Refactoring implementiert werden können — das Protocol-basierte Design zahlt sich hier aus.

**OpenClaw führt praktisch.** Es ist heute nutzbar, hat ein funktionierendes Ökosystem und löst reale Probleme (Multi-Channel-Messaging-Assistent). OpenClaw's Schwäche ist, dass Sicherheit nachgerüstet werden müsste — ein deutlich schwierigerer Umbau als Features auf ein sicheres Fundament zu setzen.

**Die strategische Empfehlung:** Mycelos sollte die Connector-Lücke als höchste Priorität schließen. Ein Agent-OS ohne externe Integrationen ist wie ein Betriebssystem ohne Treiber. Die Security-Grundlage ist das wertvollste Asset — sie muss jetzt mit praktischem Nutzen gefüllt werden.

---

## Anhang: Tech-Stack-Vergleich

| Aspekt | Mycelos | OpenClaw |
|---|---|---|
| Sprache | Python 3.12+ | TypeScript/Node.js 22+ |
| Zentrale Abstraktion | Python Protocols | Gateway WebSocket-Server |
| LLM-Zugang | LiteLLM (Multi-Provider) | Direkte API-Calls + Failover |
| Storage | SQLite WAL (11 Tabellen) | JSONL + sqlite-vec |
| Config | NixOS-Generationen (SHA-256) | JSON5 + Zod-Validierung |
| CLI | Click + Rich | Commander |
| Testing | pytest + Hypothesis | Vitest + V8 Coverage |
| Sandbox | LocalSandbox (Docker geplant) | Optional (Default: aus) |
| Deployment | pip install | Daemon (launchd/systemd) + Apps |
| Lizenz | Proprietär (in Entwicklung) | MIT |
