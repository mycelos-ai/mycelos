# Schema V2: Normalisierte Live-Tabellen + NixOS State Snapshots

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Sauberes relationales Schema mit normalisierten Verknuepfungstabellen. Content-addressed Object Store fuer Agent-Code. LLM-Konfiguration pro Agent mit Fallback-Kette und Kostendaten. Jede Zustandsaenderung erzeugt automatisch eine NixOS-style Config Generation. Rollback stellt den kompletten Systemzustand wieder her.

---

## Architektur-Entscheidung: Was gehoert in den NixOS State?

### IN den State (deklarativ, rollbackable):

| Bereich | Begruendung |
|---------|-------------|
| **Agents** (id, name, type, status) | Wer existiert im System |
| **Agent Code** (Hash-Referenz auf Object Store) | Was ein Agent tut — Code-Aenderung = neue Generation |
| **Agent Capabilities** (agent → capability) | Was ein Agent darf |
| **Agent LLM Config** (model override, fallbacks) | Welches Modell ein Agent verwendet |
| **Connectors** (id, name, type, status) | Welche externen Dienste angebunden sind |
| **Connector Capabilities** (connector → capability) | Welche Tools ein Connector bereitstellt |
| **Policies** (agent x resource → decision) | Bereits in eigener Tabelle, wird in Snapshot aufgenommen |
| **LLM Config** (primary, fallbacks, tiers) | Systemweite Modell-Konfiguration |
| **Workflows** (Definitionen) | Was automatisiert werden kann |
| **Credentials** (encrypted blobs) | Mit Rollback-Ausnahme: rotierte Keys werden NICHT zurueckgesetzt |

### NICHT im State (laufende/ephemere Daten):

| Bereich | Begruendung |
|---------|-------------|
| **Tasks** | Laufende Arbeit, nicht deklarativer Zustand |
| **Attempts** | Ausfuehrungshistorie, nicht Konfiguration |
| **Audit Events** | Append-only Log, nie rollbackable |
| **Memory Entries** | Lernzustand, wird bei Rollback als "orphaned from Gen N" markiert |
| **Capability Tokens** | Kurzlebig (TTL), nicht Teil der Deklaration |
| **Workflow Events** | Laufzeit-Checkpoints |
| **Sessions** | JSONL-Files, nicht in DB |

### Sonderfall: Credentials bei Rollback

Wenn ein Credential rotiert wurde (z.B. alter API Key kompromittiert), darf Rollback den **alten Key NICHT reaktivieren**. Der StateManager loggt: `"Rollback skipped credential revert (security)"` und behaelt den neuen Key.

---

## Content-Addressed Object Store (Option C)

Agent-Code lebt auf dem Filesystem, nicht in der DB. Wie Git: content-addressed, immutable.

```
~/.mycelos/
    mycelos.db                  <-- Metadaten, Hash-Referenzen
    objects/
        sha256/
            abc123def456...    <-- Agent-Code (immutable)
            789ghi012jkl...    <-- Agent-Tests
            mno345pqr678...    <-- Agent-Prompt
```

### ObjectStore API

```python
class ObjectStore:
    """Content-addressed immutable file storage."""

    def __init__(self, base_dir: Path):
        self._dir = base_dir / "objects" / "sha256"
        self._dir.mkdir(parents=True, exist_ok=True)

    def store(self, content: str) -> str:
        """Store content, return sha256 hash. Idempotent."""
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        path = self._dir / content_hash
        if not path.exists():
            path.write_text(content)
        return content_hash

    def load(self, content_hash: str) -> str | None:
        """Load content by hash. Returns None if not found."""
        path = self._dir / content_hash
        return path.read_text() if path.exists() else None

    def exists(self, content_hash: str) -> bool:
        return (self._dir / content_hash).exists()
```

### Vorteile

- DB bleibt schlank (nur Hashes, kein Code-Blob)
- Snapshots sind klein (Hash-Referenzen, nicht voller Code)
- Dedup gratis: gleicher Code = gleicher Hash = eine Datei
- Rollback funktioniert: alte Dateien bleiben im Store (immutable, nie geloescht)
- Spaeter erweiterbar: Workflow-YAML, Prompt-Files, Artifacts

---

## LLM-Konfiguration: Verknuepfungstabelle mit Failover-Kette

Jeder Agent bekommt seine eigenen Modelle mit Prioritaet (Primary + Fallbacks).
System-Defaults gelten fuer Agents ohne eigene Config.

### Neue Tabelle: `llm_models` (alle verfuegbaren Modelle)

```sql
CREATE TABLE IF NOT EXISTS llm_models (
    id                  TEXT PRIMARY KEY,    -- z.B. "anthropic/claude-sonnet-4-6"
    provider            TEXT NOT NULL,       -- anthropic, openai, google, ollama, ...
    tier                TEXT NOT NULL,       -- haiku, sonnet, opus, deterministic
    input_cost_per_1k   REAL,               -- $/1K input tokens (aus LiteLLM)
    output_cost_per_1k  REAL,               -- $/1K output tokens
    max_context         INTEGER,            -- Max context window
    status              TEXT NOT NULL DEFAULT 'available',  -- available, deprecated, unavailable
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
```

### Neue Tabelle: `agent_llm_models` (Verknuepfung Agent → Modell mit Failover)

```sql
CREATE TABLE IF NOT EXISTS agent_llm_models (
    agent_id    TEXT REFERENCES agents(id) ON DELETE CASCADE,  -- NULL = System-Default
    model_id    TEXT NOT NULL REFERENCES llm_models(id),
    priority    INTEGER NOT NULL DEFAULT 1,   -- 1 = primary, 2 = fallback 1, 3 = fallback 2, ...
    purpose     TEXT NOT NULL DEFAULT 'execution',  -- execution, classification, evaluation
    PRIMARY KEY (agent_id, model_id, purpose)
);

CREATE INDEX IF NOT EXISTS idx_agent_llm ON agent_llm_models(agent_id, purpose, priority);
```

### Beispieldaten

```sql
-- System-Defaults (agent_id = NULL, gelten fuer alle ohne eigene Config)
INSERT INTO agent_llm_models VALUES (NULL, 'anthropic/claude-sonnet-4-6', 1, 'execution');
INSERT INTO agent_llm_models VALUES (NULL, 'anthropic/claude-haiku-4-5',  2, 'execution');
INSERT INTO agent_llm_models VALUES (NULL, 'anthropic/claude-haiku-4-5',  1, 'classification');

-- Agent-spezifisch (ueberschreibt System-Defaults)
INSERT INTO agent_llm_models VALUES ('news-agent', 'anthropic/claude-haiku-4-5', 1, 'execution');
INSERT INTO agent_llm_models VALUES ('news-agent', 'ollama/llama3',              2, 'execution');
INSERT INTO agent_llm_models VALUES ('news-agent', 'anthropic/claude-sonnet-4-6',3, 'execution');

INSERT INTO agent_llm_models VALUES ('code-agent', 'anthropic/claude-sonnet-4-6', 1, 'execution');
INSERT INTO agent_llm_models VALUES ('code-agent', 'openai/gpt-4o',               2, 'execution');
```

### Model Resolution (mit Failover)

```python
def resolve_models(agent_id: str | None, purpose: str = "execution") -> list[str]:
    """Gibt die Modell-Kette zurueck: primary zuerst, dann Fallbacks.

    1. Agent-spezifische Modelle (nach priority sortiert)
    2. Wenn keine: System-Defaults (agent_id=NULL, nach priority sortiert)
    """
    # Zuerst agent-spezifisch
    models = storage.fetchall(
        """SELECT model_id FROM agent_llm_models
           WHERE agent_id = ? AND purpose = ?
           ORDER BY priority""",
        (agent_id, purpose),
    )
    if models:
        return [m["model_id"] for m in models]

    # Fallback: System-Defaults
    models = storage.fetchall(
        """SELECT model_id FROM agent_llm_models
           WHERE agent_id IS NULL AND purpose = ?
           ORDER BY priority""",
        (purpose,),
    )
    return [m["model_id"] for m in models]
```

### Aufruf mit Failover im LLM Broker

```python
def complete_with_failover(models: list[str], messages: list) -> LLMResponse:
    """Versucht Modelle der Reihe nach, bis eines antwortet."""
    last_error = None
    for model_id in models:
        try:
            return llm.complete(messages, model=model_id)
        except Exception as e:
            audit.log("llm.failover", details={"model": model_id, "error": str(e)})
            last_error = e
    raise last_error  # Alle fehlgeschlagen
```

### Kosten automatisch aus LiteLLM

```python
def sync_model_costs(storage: StorageBackend) -> None:
    """Sync model costs from LiteLLM's built-in cost database into llm_models."""
    import litellm
    for model_id, info in litellm.model_cost.items():
        # Nur direkte Provider-Modelle (kein vertex_ai/, bedrock/, etc.)
        if "/" in model_id and model_id.split("/")[0] not in ("anthropic", "openai", "ollama", "google"):
            continue
        provider = model_id.split("/")[0] if "/" in model_id else _guess_provider(model_id)
        tier = _classify_tier(model_id)
        storage.execute(
            """INSERT OR REPLACE INTO llm_models
               (id, provider, tier, input_cost_per_1k, output_cost_per_1k, max_context, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))""",
            (model_id, provider, tier,
             info.get("input_cost_per_token", 0) * 1000,
             info.get("output_cost_per_token", 0) * 1000,
             info.get("max_input_tokens")),
        )
```

### Snapshot-Format (LLM-Bereich)

```python
"llm": {
    "models": {
        "anthropic/claude-sonnet-4-6": {
            "provider": "anthropic", "tier": "sonnet",
            "input_cost_per_1k": 0.003, "output_cost_per_1k": 0.015,
            "max_context": 200000, "status": "available"
        },
        "anthropic/claude-haiku-4-5": { ... },
        "ollama/llama3": { ... }
    },
    "assignments": {
        "system:execution": ["anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5"],
        "system:classification": ["anthropic/claude-haiku-4-5"],
        "news-agent:execution": ["anthropic/claude-haiku-4-5", "ollama/llama3", "anthropic/claude-sonnet-4-6"],
        "code-agent:execution": ["anthropic/claude-sonnet-4-6", "openai/gpt-4o"]
    }
}
```

### Rollback-Szenarien

**Szenario 1: Model-Wechsel geht schief**
Agent X nutzt `claude-haiku-4-5` (primary) + `ollama/llama3` (fallback).
Jemand aendert primary auf `gpt-4o`. Agent X beginnt schlecht zu performen.
→ Rollback auf vorherige Generation
→ `agent_llm_models` wird wiederhergestellt: primary = `claude-haiku-4-5`
→ Agent X funktioniert wieder

**Szenario 2: Lokales Modell faellt aus**
Agent X hat `ollama/llama3` als primary. Ollama-Server ist offline.
→ Failover auf Fallback-Modell (z.B. `claude-haiku-4-5`)
→ Audit-Log: `llm.failover`
→ Kein Rollback noetig — Failover-Kette greift automatisch

**Szenario 3: Provider komplett weg**
Anthropic-API hat Ausfall. Alle Agents mit Anthropic-primary scheitern.
→ Failover auf naechsten Provider in der Kette (z.B. OpenAI)
→ Wenn kein Fallback: Task failed, User wird informiert

---

## Schema V2: Alle neuen und geaenderten Tabellen

### Neue Tabelle: `connectors`

```sql
CREATE TABLE IF NOT EXISTS connectors (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    connector_type  TEXT NOT NULL,       -- search, http, google, github, mcp
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    setup_type      TEXT,                -- key, gog, mcp, none
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT
);
```

### Neue Tabelle: `connector_capabilities`

```sql
CREATE TABLE IF NOT EXISTS connector_capabilities (
    connector_id TEXT NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    capability   TEXT NOT NULL,
    PRIMARY KEY (connector_id, capability)
);
CREATE INDEX IF NOT EXISTS idx_connector_caps ON connector_capabilities(capability);
```

### Neue Tabelle: `agent_capabilities` (ersetzt JSON-Array)

```sql
CREATE TABLE IF NOT EXISTS agent_capabilities (
    agent_id    TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    capability  TEXT NOT NULL,
    PRIMARY KEY (agent_id, capability)
);
CREATE INDEX IF NOT EXISTS idx_agent_caps ON agent_capabilities(capability);
```

### Neue Tabelle: `llm_models` (siehe LLM-Abschnitt oben)

### Neue Tabelle: `agent_llm_models` (siehe LLM-Abschnitt oben)

### Geaenderte Tabelle: `agents`

```sql
CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    agent_type      TEXT NOT NULL DEFAULT 'deterministic',
    status          TEXT NOT NULL DEFAULT 'proposed',
    -- capabilities: ENTFERNT → jetzt in agent_capabilities
    -- model: ENTFERNT → jetzt in agent_llm_models (Verknuepfung mit Failover)
    policy          TEXT,
    reputation      REAL NOT NULL DEFAULT 0.5,
    code_hash       TEXT,            -- NEU: Hash-Referenz auf Object Store (Code)
    tests_hash      TEXT,            -- NEU: Hash-Referenz auf Object Store (Tests)
    prompt_hash     TEXT,            -- NEU: Hash-Referenz auf Object Store (Prompt)
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    created_by      TEXT NOT NULL DEFAULT 'system'
);
```

---

## StateManager: Snapshot + Restore

### Snapshot-Format (vollstaendig)

```python
{
    "schema_version": 2,

    "llm": {
        "primary_model": "anthropic/claude-sonnet-4-6",
        "fallback_models": ["anthropic/claude-haiku-4-5"],
        "classifier_model": "anthropic/claude-haiku-4-5"
    },

    "connectors": {
        "web-search-duckduckgo": {
            "name": "Web Search (DuckDuckGo)",
            "connector_type": "search",
            "status": "active",
            "capabilities": ["search.web", "search.news"]
        }
    },

    "agents": {
        "agent-news-123": {
            "name": "news-agent",
            "agent_type": "deterministic",
            "status": "active",
            "capabilities": ["search.web", "search.news"],
            "code_hash": "abc123def...",
            "tests_hash": "789ghi012...",
            "prompt_hash": "mno345pqr..."
        }
    },

    "policies": {
        "default:*:search.web": "always",
        "default:*:agent.register": "confirm"
    },

    "credentials": {
        "connector:web-search-brave": {
            "encrypted": "<base64>",
            "nonce": "<base64>",
            "security_rotated": 0
        }
    },

    "models": {
        "anthropic/claude-sonnet-4-6": {
            "provider": "anthropic",
            "tier": "sonnet",
            "input_cost_per_1k": 0.003,
            "output_cost_per_1k": 0.015,
            "max_context": 200000
        }
    }
}
```

**Wichtig:** Agent-Code ist NICHT im Snapshot. Nur die Hashes. Der Code selbst lebt im Object Store auf dem Filesystem. Rollback setzt die Hashes zurueck → die Dateien sind noch da (immutable).

### Restore-Ablauf

1. BEGIN TRANSACTION
2. DELETE FROM `connectors`, `connector_capabilities`, `agent_capabilities`, `agent_llm_models`, `policies`, `llm_models`
3. Soft-Reset `agents` (update hashes, capabilities, model_override)
4. INSERT Connectors + Capabilities aus Snapshot
5. INSERT Agent Capabilities aus Snapshot
6. INSERT Policies aus Snapshot
7. INSERT/UPDATE LLM Models aus Snapshot
8. INSERT agent_llm_models Zuweisungen aus Snapshot
9. Agents: `code_hash`, `tests_hash`, `prompt_hash` wiederherstellen
9. Credentials: nur wenn `security_rotated == 0` (sonst Skip + Audit)
10. COMMIT

---

## Gherkin Scenarios: Rollback

```gherkin
# docs/scenarios/config-changes/CC22-rollback-lifecycle.feature

Feature: Config Generation Rollback
  Als Mycelos-Benutzer moechte ich jederzeit zu einem frueheren Systemzustand
  zurueckkehren koennen, damit ich riskante Aenderungen gefahrlos ausprobieren kann.

  Background:
    Given a fresh Mycelos installation with Generation 1
    And the Object Store is initialized

  # --- Happy Path ---

  Scenario: Rollback restores connectors
    Given I set up connector "web-search-brave" with capabilities ["search.web.brave"]
    And a new Generation 2 is created
    When I rollback to Generation 1
    Then connector "web-search-brave" should not exist in the connectors table
    And capability "search.web.brave" should not exist in connector_capabilities
    And Generation 1 should be active

  Scenario: Rollback restores agent capabilities
    Given I register agent "news-agent" with capabilities ["search.web", "search.news"]
    And a new Generation 2 is created
    When I add capability "http.get" to agent "news-agent"
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then agent "news-agent" should have capabilities ["search.web", "search.news"]
    And agent "news-agent" should NOT have capability "http.get"

  Scenario: Rollback restores agent code via Object Store hashes
    Given I register agent "news-agent" with code hash "abc123"
    And a new Generation 2 is created
    When I update agent "news-agent" code with hash "def456"
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then agent "news-agent" should have code_hash "abc123"
    And Object Store should still contain both "abc123" and "def456"

  Scenario: Rollback restores agent LLM assignments
    Given I register agent "news-agent" with models:
      | model_id                     | priority | purpose   |
      | anthropic/claude-haiku-4-5   | 1        | execution |
      | ollama/llama3                | 2        | execution |
    And a new Generation 2 is created
    When I change agent "news-agent" models to:
      | model_id                     | priority | purpose   |
      | openai/gpt-4o               | 1        | execution |
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then agent "news-agent" should have execution models ["anthropic/claude-haiku-4-5", "ollama/llama3"]
    And agent "news-agent" should NOT have model "openai/gpt-4o"

  Scenario: Rollback restores policies
    Given policy for "search.web" is "always"
    And a new Generation 2 is created
    When I change policy for "search.web" to "confirm"
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then policy for "search.web" should be "always"

  Scenario: Rollback restores LLM primary model
    Given primary model is "anthropic/claude-sonnet-4-6"
    And a new Generation 2 is created
    When I change primary model to "openai/gpt-4o"
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then primary model should be "anthropic/claude-sonnet-4-6"

  # --- Sonderfaelle ---

  Scenario: Rollback skips rotated credentials
    Given I store credential "connector:brave" with api_key "old-key"
    And a new Generation 2 is created
    When I rotate credential "connector:brave" to "new-key" with security_rotated=1
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then credential "connector:brave" should still have api_key "new-key"
    And an audit event "rollback.credential_skipped" should exist

  Scenario: Rollback to non-existent generation fails gracefully
    When I try to rollback to Generation 999
    Then I should get a GenerationNotFoundError

  Scenario: Dedup prevents unnecessary generations
    Given the current state produces hash "snapshot-hash-1"
    When I call apply_from_state twice without changes
    Then only one Generation should exist with hash "snapshot-hash-1"

  Scenario: Object Store files survive rollback
    Given I store code "v1-code" in Object Store with hash "hash-v1"
    And I store code "v2-code" in Object Store with hash "hash-v2"
    When I rollback to a generation referencing "hash-v1"
    Then Object Store should contain "hash-v1"
    And Object Store should contain "hash-v2"
    # Immutable: nothing is ever deleted from the Object Store

  Scenario: Rollback is atomic — failure restores previous state
    Given Generation 2 is active with connector "brave" and agent "news"
    When I start rollback to Generation 1
    And the restore fails mid-transaction (simulated)
    Then Generation 2 should still be active
    And connector "brave" should still exist
    And agent "news" should still have its capabilities

  Scenario: Rollback restores system LLM defaults
    Given system defaults are:
      | model_id                     | priority | purpose   |
      | anthropic/claude-sonnet-4-6  | 1        | execution |
      | anthropic/claude-haiku-4-5   | 2        | execution |
    And a new Generation 2 is created
    When I change system defaults to:
      | model_id                     | priority | purpose   |
      | openai/gpt-4o               | 1        | execution |
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then system execution models should be ["anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5"]

  Scenario: Agent-specific models override system defaults
    Given system default execution model is "anthropic/claude-sonnet-4-6"
    And agent "news-agent" has execution models ["anthropic/claude-haiku-4-5", "ollama/llama3"]
    When I resolve models for agent "news-agent" purpose "execution"
    Then the result should be ["anthropic/claude-haiku-4-5", "ollama/llama3"]
    And system defaults should NOT be included

  Scenario: Agent without own models falls back to system defaults
    Given system default execution models are ["anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5"]
    And agent "simple-agent" has NO execution models configured
    When I resolve models for agent "simple-agent" purpose "execution"
    Then the result should be ["anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5"]

  Scenario: Full round-trip — snapshot, modify, rollback, verify
    Given I set up connector "duckduckgo" with capabilities ["search.web"]
    And I register agent "search-agent" with capabilities ["search.web"] and code "v1"
    And I set policy "search.web" to "always"
    And I set primary model to "anthropic/claude-sonnet-4-6"
    And a new Generation 2 is created (snapshot of full state)
    When I add connector "brave" with capabilities ["search.web.brave"]
    And I add capability "http.get" to agent "search-agent"
    And I change primary model to "openai/gpt-4o"
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then connector "duckduckgo" should exist with capabilities ["search.web"]
    And connector "brave" should NOT exist
    And agent "search-agent" should have capabilities ["search.web"] only
    And primary model should be "anthropic/claude-sonnet-4-6"
    And policy "search.web" should be "always"
```

---

## Tasks

### Task 1: Object Store

**Files:**
- Create: `src/mycelos/storage/object_store.py`
- Create: `tests/test_object_store.py`

**Was:**
- `ObjectStore(base_dir)` — content-addressed immutable file storage
- `store(content) -> hash` — SHA-256, idempotent
- `load(hash) -> str | None`
- `exists(hash) -> bool`
- `list_objects() -> list[str]` (fuer Debugging/Maintenance)
- Tests: store, load, dedup, missing hash, concurrent writes

---

### Task 2: Schema V2 — Neue Tabellen

**Files:**
- Rewrite: `src/mycelos/storage/schema.sql`
- Modify: `src/mycelos/storage/database.py`
- Create: `tests/test_schema_v2.py`

**Was:**
- Neue Tabellen: `connectors`, `connector_capabilities`, `agent_capabilities`, `llm_models`
- `agents`: `capabilities` entfernt, `model_override`, `code_hash`, `tests_hash`, `prompt_hash` hinzugefuegt
- Clean-Start (wir sind im Testing, keine Migration noetig)

---

### Task 3: ConnectorRegistry — CRUD

**Files:**
- Create: `src/mycelos/connectors/connector_registry.py`
- Create: `tests/test_connector_registry.py`
- Modify: `src/mycelos/app.py`

**Was:**
- `register(id, name, type, capabilities, setup_type)`
- `get(id) -> dict` (mit JOIN auf capabilities)
- `list_connectors(status=None) -> list[dict]`
- `set_status(id, status)`
- `remove(id)` (CASCADE)

---

### Task 4: AgentRegistry V2 — Normalisiert + Object Store

**Files:**
- Rewrite: `src/mycelos/agents/registry.py`
- Create: `tests/test_agent_registry_v2.py`
- Modify: bestehende Tests

**Was:**
- `register()` → `agents` + `agent_capabilities`
- `set_capabilities(agent_id, capabilities)` — DELETE + INSERT
- `save_code(agent_id, code, tests, prompt, object_store)` — speichert im Object Store, setzt Hashes
- `get()` → capabilities via JOIN, code via Object Store load
- `get_code(agent_id, object_store)` → laedt aus Object Store
- `set_models(agent_id, models, purpose)` — setzt LLM-Zuweisungen in `agent_llm_models`
- `get_models(agent_id, purpose) -> list[str]` — gibt Modell-Kette zurueck (primary + fallbacks)

---

### Task 5: LLM Model Registry + Failover Resolution + Smart Init

**Files:**
- Create: `src/mycelos/llm/model_registry.py`
- Create: `tests/test_model_registry.py`
- Modify: `src/mycelos/app.py`

**Was:**
- `ModelRegistry(storage)` — CRUD fuer `llm_models` + `agent_llm_models`
- `sync_from_litellm()` — importiert Kosten aus `litellm.model_cost`
- `get_model(id) -> dict` (cost, context, tier, status)
- `list_models(provider=None, tier=None) -> list[dict]`
- `set_system_defaults(models_by_purpose)` — System-Defaults setzen (agent_id=NULL)
- `set_agent_models(agent_id, models, purpose)` — Agent-spezifische Modelle setzen
- `resolve_models(agent_id, purpose) -> list[str]` — Agent-spezifisch > System-Default, nach priority
- `complete_with_failover(models, messages) -> LLMResponse` — versucht Modelle der Reihe nach
- `setup_smart_defaults(provider)` — setzt intelligente Defaults bei `mycelos init`

**Smart Defaults bei `mycelos init`:**

Wenn der User z.B. "anthropic" waehlt, werden automatisch sinnvolle Modelle pro System-Agent gesetzt:

```python
SMART_DEFAULTS = {
    "anthropic": {
        # System-Defaults
        "system:execution":      ["anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5"],
        "system:classification": ["anthropic/claude-haiku-4-5"],
        # System-Agents
        "creator-agent:execution":   ["anthropic/claude-sonnet-4-6", "anthropic/claude-opus-4-6"],
        "planner-agent:execution":   ["anthropic/claude-haiku-4-5", "anthropic/claude-sonnet-4-6"],
        "evaluator-agent:execution": ["anthropic/claude-haiku-4-5", "anthropic/claude-sonnet-4-6"],
        "auditor-agent:execution":   ["anthropic/claude-sonnet-4-6"],
    },
    "openai": {
        "system:execution":      ["openai/gpt-5.4", "openai/gpt-5.4-mini"],
        "system:classification": ["openai/gpt-5.4-mini"],
        "creator-agent:execution":   ["openai/gpt-5.4", "openai/gpt-5.4-pro"],
        "planner-agent:execution":   ["openai/gpt-5.4-mini", "openai/gpt-5.4"],
        "evaluator-agent:execution": ["openai/gpt-5.4-mini"],
        "auditor-agent:execution":   ["openai/gpt-5.4"],
    },
}
```

Die Idee:
- **Creator-Agent** braucht starke Modelle (generiert Code) → Sonnet primary, Opus als Fallback
- **Planner-Agent** braucht weniger Power (plant nur) → Haiku primary, Sonnet als Fallback
- **Evaluator** ist oft deterministisch, braucht guenstiges Modell → Haiku primary
- **Auditor** muss gruendlich sein (Security!) → Sonnet, kein billiges Fallback
- **Classifier** (Intent-Erkennung) → immer Haiku (billig, schnell)

Beim Init zeigt Mycelos die Defaults und fragt: "Smart defaults setzen? [Y/n]".
User kann spaeter per `mycelos config` oder Chat jederzeit anpassen.

**Modify:** `src/mycelos/cli/init_cmd.py`
- Multi-Provider Support: User kann mehrere Provider konfigurieren (primary + fallback von anderem Provider)
- Smart Defaults werden in `agent_llm_models` + `llm_models` geschrieben
- `sync_from_litellm()` wird aufgerufen fuer Kostendaten
- Erste Config-Generation enthaelt den vollen State inkl. LLM-Zuweisungen

---

### Task 6: StateManager — Snapshot + Restore

**Files:**
- Create: `src/mycelos/config/state_manager.py`
- Create: `tests/test_state_manager.py`

**Was:**
- `snapshot(storage, config) -> dict` — liest ALLE deklarativen Tabellen
- `restore(storage, snapshot)` — schreibt in Live-Tabellen, Transaction
- Credential-Ausnahme: `security_rotated == 1` → Skip + Audit
- Agent-Code: nur Hashes werden restored (Object Store Dateien bleiben)
- Atomicity: ROLLBACK bei Fehler

---

### Task 7: ConfigGenerationManager V2

**Files:**
- Modify: `src/mycelos/config/generations.py`
- Modify: `tests/test_config.py`

**Was:**
- `apply_from_state(state_manager, description, trigger)` — snapshot → hash → neue Generation
- `rollback(state_manager)` — restore aus Ziel-Generation
- Alter `apply(config)` bleibt fuer einfache Config-Aenderungen
- Dedup wie bisher

---

### Task 8: Connector-Setup + Agent-Registration Integration

**Files:**
- Modify: `src/mycelos/cli/connector_cmd.py`
- Modify: `src/mycelos/agents/creator.py`
- Modify: `tests/test_connector_setup.py`

**Was:**
- `connector_cmd` nutzt `connector_registry.register()` + `apply_from_state()`
- `creator.py` nutzt `agent_registry.save_code()` mit Object Store
- Jede Aenderung erzeugt neue Generation

---

### Task 9: Creator-Agent Context dynamisch

**Files:**
- Modify: `src/mycelos/cli/chat_cmd.py`
- Create: `tests/test_build_context.py`

**Was:**
- `_build_context()` liest aus `connector_registry`, `agent_registry`, `llm_models`
- Entfernt hardcoded Tool-Liste
- Zeigt: Connectors, Agents, LLM Config (primary + fallbacks + Kosten)

---

### Task 10: Gherkin Scenarios + Integration Tests

**Files:**
- Create: `docs/scenarios/config-changes/CC22-rollback-lifecycle.feature`
- Create: `tests/test_generation_lifecycle.py`

**Was:**
- Alle 12 Gherkin Scenarios aus dem Plan oben
- Integration Tests die den kompletten Round-Trip testen
- Rollback-Sonderfaelle: rotierte Credentials, Object Store Persistenz, Atomicity

---

## Reihenfolge und Abhaengigkeiten

```
Task 1 (Object Store) + Task 2 (Schema V2)  — parallel
    |
Task 3 (ConnectorRegistry) + Task 4 (AgentRegistry V2) + Task 5 (LLM Models) — parallel
    |
Task 6 (StateManager)
    |
Task 7 (ConfigGeneration V2)
    |
Task 8 (Integration: Setup + Registration) + Task 9 (Context) — parallel
    |
Task 10 (Gherkin + Integration Tests)
```

## Was wir NICHT aendern (bewusst)

- `plans.steps` bleibt JSON (geordnetes Array, keine Entitaet)
- `capability_tokens.scope` bleibt JSON (kurzlebig)
- `tasks.inputs` bleibt JSON (frei-form)
- `audit_events.details` bleibt JSON (Log-Daten)
- Session-Storage bleibt JSONL-Files
- Blueprint-Lifecycle (resolve/verify/plan/apply/status) — separater Plan, baut auf diesem Fundament auf
