# Analyse: Unterspezifizierte Bereiche im AgentOS Design

**Datum:** 2026-03-17
**Bezug:** `2026-03-15-agentos-design.md` (Hauptspec), Section 9 (Blueprint Lifecycle), Section 7 (Security), Section 4 (System Agents), Section 31 (Datenretention)
**Status:** Analyse — erfordert Design-Entscheidungen

---

## 1. Config-Snapshot: Was gehoert rein, was nicht?

### Problem

Die Spec definiert in Section 9, dass `config_snapshot` folgendes enthaelt:
- Registrierte Agenten und ihre Policies
- Aktive Modelle und Provider-Konfiguration
- Tool-Konfigurationen
- Sandbox-Policies
- Channel-Konfigurationen

Aber es fehlt eine klare Abgrenzung: **Was gehoert NICHT in den Snapshot?** Insbesondere:

### 1a. Credentials duerfen niemals im Config-Snapshot erscheinen

**Aktueller Zustand:** Section 7.2 definiert den Credential Proxy als separaten Prozess mit SOPS + age Verschluesselung. Section 7.1 sagt "Keine Credential-Variablen im Agent-Prozess". Aber nirgends steht explizit, dass der `config_snapshot` in `config_generations` **keine Credentials enthalten darf**.

**Risiko:** Der `config_snapshot` wird als Klartext-JSON in SQLite gespeichert. Wenn dort versehentlich ein API-Key oder ein OAuth-Token landet:
1. Jede Generation bewahrt ihn dauerhaft auf (Retention: unbegrenzt)
2. `mycelos config diff` wuerde ihn im Terminal anzeigen
3. Ein kompromittierter Agent mit Lesezugriff auf die DB haette alle historischen Keys

**Empfohlene Spezifikation:**

```
Config State (im Snapshot):             Credential State (im Credential Proxy):
  - Agent-Registrierungen                - API Keys
  - Policies                             - OAuth Tokens
  - Modell-Tier pro Agent                - App Passwords
  - Tool-Konfigurationen                 - Encryption Keys
  - Channel-Configs                      - Credential Proxy Config selbst
  - Sandbox-Policies
                                        Environment State (eigene Verwaltung):
  NIEMALS im Snapshot:                    - LLM Provider URLs
  - API Keys                             - Proxy-Adressen
  - Tokens                               - Feature Flags
  - Passwoerter                          - Runtime-Overrides
  - Connection Strings mit Auth
  - Pfade zu Credential-Dateien
```

**Design-Entscheidung noetig:** Der ConfigGenerationManager sollte beim `apply()` eine Validation durchfuehren, die bekannte Credential-Patterns erkennt und ablehnt:

```python
CREDENTIAL_PATTERNS = [
    r"sk-[a-zA-Z0-9]{20,}",           # Anthropic/OpenAI API Keys
    r"ghp_[a-zA-Z0-9]{36}",           # GitHub PAT
    r"-----BEGIN.*PRIVATE KEY-----",    # Private Keys
    r"password\s*[:=]",                 # Password fields
]

def _validate_no_credentials(self, config: dict) -> None:
    """Reject configs that contain credential-like values."""
    serialized = json.dumps(config)
    for pattern in CREDENTIAL_PATTERNS:
        if re.search(pattern, serialized):
            raise SecurityViolation(
                f"Config contains credential-like value matching {pattern}. "
                "Use the Credential Proxy instead."
            )
```

**Blueprint Lifecycle Integration:** Diese Validation gehoert in die **Verify-Phase** des Blueprint Lifecycle (Section 9, Phase 2). Neben dem SHA-256 Digest-Check wird auch ein Credential-Scan durchgefuehrt.

### 1b. Referenz-Schema fuer config_snapshot fehlt

**Aktueller Zustand:** Nirgends ist die exakte JSON-Struktur des Snapshots definiert.

**Empfohlenes Schema (muss in Spec aufgenommen werden):**

```json
{
  "version": "0.1.0",
  "default_model": "claude-haiku-4-5-20251001",
  "agents": {
    "<agent-id>": {
      "name": "Email Summary Agent",
      "agent_type": "llm",
      "model_tier": "haiku",
      "status": "active"
    }
  },
  "policies": {
    "<agent-id>": {
      "email.read": "allow",
      "email.send": "ask",
      "web": "deny"
    }
  },
  "tools": {
    "<tool-id>": {
      "enabled": true,
      "config": {}
    }
  },
  "sandbox": {
    "default_timeout": 30,
    "max_memory_mb": 512
  },
  "channels": {
    "terminal": {"enabled": true},
    "telegram": {"enabled": false, "bot_token_ref": "credential:telegram.bot_token"}
  },
  "llm": {
    "providers": {
      "anthropic": {"enabled": true, "credential_ref": "credential:anthropic.api_key"}
    }
  }
}
```

**Wichtig:** Das Schema verwendet `credential_ref`-Referenzen (z.B. `"credential:anthropic.api_key"`) statt echte Credentials. Der Credential Proxy loest diese Referenzen zur Laufzeit auf. So ist der Snapshot sicher speicherbar und diffbar, ohne Secrets zu exponieren.

---

## 2. Environment-Variablen: .env ist der falsche Ansatz

### Problem

Die Spec erwaehnt ".env" nur in der `.gitignore`. Es gibt keine Spezifikation wie Environment-Variablen ins System fliessen. Das ist ein Problem, weil:

1. **Env-Vars koennten Credentials enthalten** (klassisch: `ANTHROPIC_API_KEY=sk-...`). Ein Agent mit Shell-Zugriff koennte `env` oder `printenv` ausfuehren.
2. **Env-Var-Aenderungen sind nicht rollbackbar.** Wenn jemand `MYCELOS_DEFAULT_MODEL=opus` setzt und das System sich anders verhaelt, gibt es keine Generation dazu.
3. **Env-Vars sind unsichtbar.** Sie tauchen nicht im Audit Trail auf, nicht im Config Diff, nicht in der Generation History.

### Empfohlener Ansatz: Keine .env-Dateien, stattdessen drei Ebenen

```
Ebene 1: Config Generations (persistent, rollbackbar, auditierbar)
  → Alles was das Systemverhalten bestimmt
  → Gespeichert in SQLite, versioniert, diffbar

Ebene 2: Credential Proxy (persistent, verschluesselt, nie rollbackbar)
  → API Keys, Tokens, Passwoerter
  → Gespeichert in secrets.enc.yaml (SOPS + age)
  → Rotation ueber "mycelos credential rotate <service>"

Ebene 3: Runtime Overrides (temporaer, pro Session, nicht persistent)
  → CLI-Flags: mycelos chat --model opus
  → Session-Parameter: einmalige Overrides
  → Verschwinden nach Session-Ende
  → Werden im Audit Trail geloggt
```

**Was NICHT existieren sollte:**
- Keine `.env`-Datei im Projektverzeichnis
- Keine `MYCELOS_*` Environment-Variablen die Systemverhalten steuern
- Keine Moeglichkeit fuer Agents, `process.env` oder `os.environ` zu lesen

**Ausnahme:** Eine einzige Env-Var ist akzeptabel: `MYCELOS_DATA_DIR` als Pfad zum Datenverzeichnis (analog zu `XDG_DATA_HOME`). Diese steuert nur WO die Daten liegen, nicht WAS das System tut.

### Begründung

Der NixOS-Ansatz lebt davon, dass der gesamte Systemzustand deklarativ und versioniert ist. `.env`-Dateien sind das Gegenteil: unsichtbar, nicht versioniert, nicht diffbar, nicht rollbackbar. Jede Env-Var die Systemverhalten beeinflusst, ist ein Loch im Generationsmodell.

**Konkret:** Wenn ein User `export MYCELOS_LLM_PROVIDER=openai` setzt und dann `mycelos config rollback` ausfuehrt — was passiert? Der Config-Snapshot sagt "anthropic", die Env-Var sagt "openai". Wer gewinnt? Diesen Konflikt vermeiden wir komplett, indem wir Env-Vars als Config-Quelle ablehnen.

---

## 3. Config-Retention und Speicheroptimierung

### Problem

Section 31 sagt: "Config Generations: Unbegrenzt (klein, ~KB pro Generation)". Die Kernregel in Section 9 sagt: "Immer den vollen State speichern, nie Deltas." Gleichzeitig gibt es den Blueprint Lifecycle der theoretisch hochfrequente automatische Aenderungen erzeugen koennte.

### Analyse: Wie gross wird das wirklich?

**Realistische Szenarien:**

| Szenario | Aenderungen/Tag | KB/Generation | Wachstum/Jahr |
|---|---|---|---|
| Einzel-User, manuell | 1-3 | ~2 KB | ~2 MB |
| Einzel-User, mit Agents | 5-15 | ~2 KB | ~10 MB |
| Power-User, viele Agents | 20-50 | ~5 KB | ~90 MB |
| Pathologisch (Bug/Loop) | 1000+ | ~5 KB | ~1.8 GB |

### Empfehlung: Keyframe + Diff Hybrid (MPEG-Style)

Die Kernregel "nie Deltas" ist richtig fuer die **aktive** History. Aber fuer **archivierte** Generationen (aelter als N Tage) koennen wir komprimieren:

```
Aktive Zone (letzte 50 Generationen oder 7 Tage):
  → Volle Snapshots, sofort rollbackbar, sofort diffbar
  → Keine Komprimierung

Archiv-Zone (aelter als 7 Tage):
  → Jede 10. Generation: Voller Snapshot (Keyframe)
  → Dazwischen: Nur der Diff zum vorherigen Keyframe
  → Rollback moeglich, aber erfordert Keyframe + Diffs reapply

Expired-Zone (aelter als 90 Tage, konfigurierbar):
  → Nur noch Keyframes behalten (jede 10.)
  → Diffs werden geloescht
  → Audit Trail der geloeschten Generationen bleibt (Metadaten, kein Snapshot)
```

**Datenmodell-Erweiterung:**

```sql
ALTER TABLE config_generations ADD COLUMN storage_type TEXT NOT NULL DEFAULT 'full';
-- Werte: 'full' (kompletter Snapshot), 'diff' (nur Aenderungen), 'archived' (Metadaten only)

ALTER TABLE config_generations ADD COLUMN keyframe_id INTEGER REFERENCES config_generations(id);
-- Bei Diffs: Verweis auf den Keyframe von dem aus der Diff berechnet wird
```

**Wichtig:** Die Komprimierung ist ein **Background-Job**, kein Teil des Blueprint Lifecycle. Der Lifecycle schreibt immer volle Snapshots. Die Komprimierung laeuft asynchron und verdichtet alte Generationen.

### Schutzmechanismus gegen pathologisches Wachstum

```python
CONFIG_GENERATION_LIMITS = {
    "max_generations_per_hour": 100,     # Circuit Breaker
    "max_generations_per_day": 500,      # Hard Limit
    "warning_threshold_per_hour": 20,    # Alert an User
}
```

Wenn ein Agent in einer Schleife Config-Aenderungen erzeugt (z.B. Bug im Optimizer oder Replan-Sturm), greift der Circuit Breaker und blockiert weitere Aenderungen bis der User eingreift.

---

## 4. Agent-Benennung: God-Agent und Devil's Advocate umbenennen

### Problem

Die aktuellen Namen haben religioes-mythologische Konnotationen die problematisch sein koennen:
- **"God-Agent"**: Suggeriert Allmacht, ist aber gerade NICHT allmaechttig (braucht User-Bestaetigung). Der Name widerspricht der Architektur.
- **"Devil's Advocate"**: Ist zwar ein etablierter Begriff, aber in Kombination mit "God" wird das Naming-Schema fragwuerdig.

### Analyse der Rollen

| Aktuell | Tatsaechliche Rolle | Alternatives Naming |
|---|---|---|
| GodAgent | Erstellt Agenten, erweitert System | **CreatorAgent** |
| AdvocateAgent (Devil's Advocate) | Reviewt Code auf Security/Quality | **GuardianAgent** oder **ReviewerAgent** |
| PlannerAgent | Plant und delegiert | PlannerAgent (passt) |
| EvaluatorAgent | Bewertet Output-Qualitaet | EvaluatorAgent (passt) |
| OptimizerAgent | Raeumt auf, findet Duplikate | OptimizerAgent (passt) |

### Empfehlung

**Option A: CreatorAgent + GuardianAgent**

```
CreatorAgent    — erstellt und erweitert (produktive Rolle)
GuardianAgent   — prueft und schuetzt (schuetzende Rolle)
PlannerAgent    — plant und delegiert (orchestrierende Rolle)
EvaluatorAgent  — bewertet Ergebnisse (bewertende Rolle)
OptimizerAgent  — optimiert System (wartende Rolle)
```

Vorteile:
- "Creator" beschreibt exakt was der Agent tut: Agenten erstellen, System erweitern
- "Guardian" passt zur Security-First-Philosophie und beschreibt die schuetzende Rolle
- Kein religioeses/mythologisches Naming
- Konsistentes Pattern: jeder Name beschreibt die Funktion

**Option B: CreatorAgent + AuditorAgent**

"Auditor" betont die unabhaengige Pruefung staerker als "Guardian":
- GuardianAgent klingt nach aktiver Verteidigung (blockiert Bedrohungen)
- AuditorAgent klingt nach unabhaengiger Pruefung (reviewt und berichtet)

Die tatsaechliche Rolle ist naeher an einem Auditor: unabhaengig, nicht beeinflussbar, prueft systematisch, berichtet Findings. Der Agent verteidigt nicht aktiv, er reviewt und meldet.

**Option C: CreatorAgent + SentinelAgent**

"Sentinel" (Waechter) ist ein Kompromiss:
- Weniger "passiv" als Auditor
- Weniger "aktiv" als Guardian
- Konnotation: wachsam, beobachtend, meldet Auffaelligkeiten
- Keine religioese Konnotation

### Auswirkungen auf den Code

Die Umbenennung betrifft:
- Spec: Section 4 (System Agents), Section 7.5 (Policies), Section 3 (Bootstrap), Glossar
- Foundation Plan: Noch keine Implementierung der Agents (erst Milestone 2+)
- `src/mycelos/agents/` Verzeichnis (noch nicht erstellt)
- Alle Stellen die "God-Agent" oder "AdvocateAgent" referenzieren (~40 Stellen in der Spec)

Da die Agent-Implementierung noch nicht existiert, ist JETZT der richtige Zeitpunkt fuer die Umbenennung.

---

## Entscheidungen (getroffen am 2026-03-17)

| # | Thema | Entscheidung | Status |
|---|---|---|---|
| 1 | Credential Storage | **Verschluesselt in SQLite** (asymmetrisch, MYCELOS_MASTER_KEY). Ein Storage-Layer, Credentials sind rollbackbar. | In Spec eingearbeitet (Section 7.2) |
| 2 | Environment-Variablen | **Keine .env-Dateien.** Nur MYCELOS_MASTER_KEY + MYCELOS_DATA_DIR. Drei Ebenen: Config Generations + Credential Proxy + Runtime Overrides. | In Spec eingearbeitet (Section 7.2a) |
| 3 | Config Retention | **Keyframe+Diff (MPEG-Style).** Volle Snapshots 7 Tage, Keyframes (jede 10.) + Diffs bis 90 Tage, nur Keyframes danach. Circuit Breaker gegen Loops. | In Spec eingearbeitet (Section 9) |
| 4 | Agent-Benennung | **CreatorAgent + AuditorAgent.** GodAgent und AdvocateAgent/Devil's Advocate umbenannt. | In Spec + Plan eingearbeitet (~90 Stellen) |

### Noch offen (aus dieser Analyse):
| # | Thema | Naechster Schritt |
|---|---|---|
| 1a | Credential-Scan in Verify-Phase | Implementieren in Milestone 2 (Blueprint Lifecycle) |
| 1b | Config-Snapshot JSON-Schema | Formales Schema definieren (Milestone 2) |
