# Smart Init: Multi-Provider LLM Setup

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `mycelos init` wird zum interaktiven Setup-Wizard der mehrere LLM-Provider konfiguriert, Modelle abruft/anzeigt, Smart Defaults fuer System-Agents setzt, und die erste vollstaendige Config-Generation erzeugt.

---

## Aktueller Stand

`init_cmd.py` hat eine hardcoded PROVIDERS-Liste (Anthropic, OpenAI, Gemini) mit statischen Modell-Listen. User waehlt EINEN Provider + EIN Modell. Keine Fallbacks, kein Ollama, kein OpenRouter, keine Smart Defaults.

## Ziel-Flow

```
$ mycelos init

Welcome to Mycelos!

Master key generated automatically.

━━━ LLM Provider Setup ━━━

Welche Provider moechtest du nutzen? (Mehrere moeglich)

  [1] Anthropic       (claude-sonnet-4-6, claude-haiku-4-5, ...)
  [2] OpenAI          (gpt-4o, gpt-4o-mini, o4-mini, ...)
  [3] Gemini          (gemini-2.5-flash, gemini-2.5-pro, ...)
  [4] OpenRouter      (Zugriff auf 200+ Modelle)
  [5] Ollama          (lokale Modelle, kein API Key)
  [6] Custom Server   (eigener Endpoint)

Provider (kommagetrennt, z.B. 1,5): 1,5

━━━ Anthropic Setup ━━━
ANTHROPIC_API_KEY found in environment. ✓

Verfuegbare Modelle:
  [x] claude-sonnet-4-6     $0.003/$0.015 per 1K  200K context
  [x] claude-haiku-4-5      $0.001/$0.005 per 1K  200K context
  [x] claude-opus-4-6       $0.005/$0.025 per 1K  1M context
  [ ] claude-sonnet-4-5     $0.003/$0.015 per 1K  200K context (aelter)

Alle aktivieren? [Y/n]: y

━━━ Ollama Setup ━━━
Ollama URL [localhost:11434]:

Lokale Modelle gefunden:
  [x] llama3.3:latest        8B params
  [x] mistral:latest         7B params
  [ ] codellama:latest       7B params

Welche aktivieren? (kommagetrennt oder 'all'): 1,2

━━━ Smart Defaults ━━━

Basierend auf deinen Providern schlage ich vor:

  Agent             Primary                      Fallback
  ─────────────────────────────────────────────────────────
  Creator-Agent     claude-sonnet-4-6            claude-opus-4-6
  Auditor-Agent     claude-sonnet-4-6            —
  Planner-Agent     claude-haiku-4-5             ollama/llama3.3
  Evaluator-Agent   claude-haiku-4-5             ollama/mistral
  Classifier        claude-haiku-4-5             —
  System Default    claude-sonnet-4-6            claude-haiku-4-5

Uebernehmen? [Y/n]: y

━━━ Built-in Connectors ━━━
  ✓ DuckDuckGo (search.web, search.news) — ready
  ✓ HTTP (http.get, http.post) — ready

Mycelos initialized at ~/.mycelos
Config Generation #1 created (full state snapshot)
5 LLM models, 2 connectors, 6 agent assignments

Next: run mycelos chat to start.
```

---

## Provider-Konfiguration

### Statische Provider (API Key + bekannte Modelle)

| Provider | Key | Modelle | Kosten |
|----------|-----|---------|--------|
| Anthropic | ANTHROPIC_API_KEY | Statisch: neueste 3-4 aus LiteLLM | Aus `litellm.model_cost` |
| OpenAI | OPENAI_API_KEY | Statisch: neueste 4-5 aus LiteLLM | Aus `litellm.model_cost` |
| Gemini | GEMINI_API_KEY | Statisch: neueste 3 aus LiteLLM | Aus `litellm.model_cost` |

### Dynamische Provider (API abfragen)

| Provider | Key | Modelle | Endpoint |
|----------|-----|---------|----------|
| Ollama | Kein Key, nur URL | Dynamisch: `GET {url}/api/tags` | localhost:11434 |
| OpenRouter | OPENROUTER_API_KEY | Dynamisch: `GET https://openrouter.ai/api/v1/models` | — |

### Custom Server

```
URL: http://my-server:8080
API Key (optional): sk-...
Modell-ID: my-custom-model
```

Wird als `custom/{model_id}` gespeichert. LiteLLM unterstuetzt `openai/{model}` mit `api_base` Override.

---

## Modell-Auswahl UX

Fuer jeden Provider zeigen wir die verfuegbaren Modelle als Tabelle mit:
- Name
- Kosten (Input/Output per 1K tokens)
- Context Window
- Checkbox (an/aus)

Standardmaessig sind die neuesten/besten aktiviert. User kann einzelne deaktivieren.

Bei Ollama zeigen wir zusaetzlich:
- Parameter-Groesse (aus API Response)
- Ob das Modell gerade geladen ist

---

## Smart Defaults Logik

Nach der Modell-Auswahl schlaegt Mycelos eine Zuordnung vor. Die Logik:

```python
def compute_smart_defaults(available_models: list[dict]) -> dict[str, list[str]]:
    """Compute agent-to-model assignments based on available models.

    Strategy:
    - Creator/Auditor: staerkstes verfuegbares Modell (braucht Qualitaet)
    - Planner/Evaluator: guenstigstes brauchbares Modell (spart Kosten)
    - Classifier: billigstes Modell (nur Intent-Erkennung)
    - System Default: bestes Preis-Leistungs-Verhaeltnis
    - Fallbacks: naechst-guenstigeres oder anderer Provider
    """
    # Sortiere nach tier: opus > sonnet > haiku
    # Innerhalb tier: bevorzuge cloud > local (Zuverlaessigkeit)
    # Fallback: anderer Provider als Primary (Diversitaet)
```

### Tier-Zuordnung:

| Role | Tier | Warum |
|------|------|-------|
| Creator-Agent | sonnet (fallback: opus) | Code-Generierung braucht Qualitaet |
| Auditor-Agent | sonnet | Security-Review muss gruendlich sein |
| Planner-Agent | haiku (fallback: sonnet) | Planung ist einfacher |
| Evaluator-Agent | haiku (fallback: sonnet) | Oft deterministisch |
| Classifier | haiku | Nur Intent-Erkennung, muss schnell + billig sein |
| System Default | sonnet (fallback: haiku) | Gutes Preis-Leistungs-Verhaeltnis |

### Cross-Provider Fallbacks:

Wenn der User Anthropic + Ollama hat:
- Primary: Anthropic (zuverlaessig, cloud)
- Fallback: Ollama (lokal, kostenlos, aber ggf. langsamer)

Wenn der User Anthropic + OpenAI hat:
- Primary: Anthropic
- Fallback: OpenAI (Provider-Diversitaet)

---

## Datenmodell

Alles nutzt die bestehenden V2-Tabellen:

- `llm_models` — alle aktivierten Modelle mit Kosten
- `agent_llm_models` — Zuordnung Agent → Modell mit Priority
- `credentials` — API Keys encrypted
- `connectors` + `connector_capabilities` — Built-in Connectors

Nach Init: `apply_from_state()` erzeugt Generation #1 mit dem vollen State.

---

## Tasks

### Task 1: Provider Registry — verfuegbare Provider und Modell-Discovery

**Files:**
- Create: `src/mycelos/llm/providers.py`
- Create: `tests/test_providers.py`

**Was:**

```python
@dataclass
class ProviderConfig:
    id: str                      # "anthropic", "openai", "ollama", ...
    name: str                    # "Anthropic"
    env_var: str | None          # "ANTHROPIC_API_KEY" oder None fuer Ollama
    requires_key: bool
    default_url: str | None      # "http://localhost:11434" fuer Ollama
    models: list[ModelInfo]      # verfuegbare Modelle

@dataclass
class ModelInfo:
    id: str                      # "anthropic/claude-sonnet-4-6"
    name: str                    # "Claude Sonnet 4.6"
    tier: str                    # "haiku", "sonnet", "opus"
    input_cost_per_1k: float | None
    output_cost_per_1k: float | None
    max_context: int | None
    enabled: bool = True         # User kann deaktivieren

PROVIDERS: dict[str, ProviderConfig]  # statische Definition

def discover_ollama_models(url: str = "http://localhost:11434") -> list[ModelInfo]:
    """GET {url}/api/tags → parse response → ModelInfo list."""

def get_provider_models(provider_id: str, api_key: str | None = None) -> list[ModelInfo]:
    """Get models for a provider — static for cloud, dynamic for Ollama."""
    # Cloud: filter litellm.model_cost by provider
    # Ollama: call discover_ollama_models()
```

Tests:
- Statische Provider haben Modelle
- Ollama-Discovery mit gemocktem HTTP-Response
- LiteLLM-Kosten werden korrekt gemappt
- Unbekannter Provider → leere Liste

---

### Task 2: Smart Defaults — Berechnung der Modell-Zuordnung

**Files:**
- Create: `src/mycelos/llm/smart_defaults.py`
- Create: `tests/test_smart_defaults.py`

**Was:**

```python
def compute_smart_defaults(
    available_models: list[ModelInfo],
) -> dict[str, list[str]]:
    """Compute agent-to-model assignments.

    Returns dict like:
    {
        "system:execution": ["anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5"],
        "system:classification": ["anthropic/claude-haiku-4-5"],
        "creator-agent:execution": ["anthropic/claude-sonnet-4-6", "anthropic/claude-opus-4-6"],
        "planner-agent:execution": ["anthropic/claude-haiku-4-5", "ollama/llama3.3"],
        ...
    }
    """
```

Logik:
1. Modelle nach Tier sortieren (opus > sonnet > haiku)
2. Pro Role den passenden Tier waehlen
3. Fallback aus anderem Provider oder niedrigerem Tier
4. Lokale Modelle (Ollama) bevorzugt als Fallback (kostenlos)

Tests:
- Nur Anthropic → alle Anthropic-Modelle
- Anthropic + Ollama → Cloud primary, Ollama fallback
- Nur Ollama → alles lokal
- Anthropic + OpenAI → Cross-Provider Fallbacks
- Keine Modelle → leeres Dict
- Ein einziges Modell → ueberall als Primary, kein Fallback

---

### Task 3: Init Wizard — Interaktives Multi-Provider Setup

**Files:**
- Rewrite: `src/mycelos/cli/init_cmd.py`
- Create: `tests/test_init_wizard.py`

**Was:**

Der neue Init-Flow:
1. Master Key (wie bisher)
2. Provider-Auswahl (Mehrfachauswahl mit Nummern)
3. Pro Provider: Key eingeben + Modelle anzeigen/auswaehlen
4. Smart Defaults anzeigen + bestaetigen
5. Built-in Connectors registrieren (DuckDuckGo, HTTP)
6. `apply_from_state()` → erste Generation

Neuer Code:

```python
def _select_providers() -> list[str]:
    """Multi-select: welche Provider nutzen?"""

def _setup_provider(provider: ProviderConfig) -> tuple[str | None, list[ModelInfo]]:
    """Pro Provider: Key + Modell-Auswahl. Returns (api_key, selected_models)."""

def _show_model_table(models: list[ModelInfo]) -> list[ModelInfo]:
    """Rich-Tabelle mit Checkboxes. Returns aktivierte Modelle."""

def _show_smart_defaults(defaults: dict) -> dict:
    """Zeigt Vorschlag als Tabelle, User kann bestaetigen/aendern."""

def _register_builtin_connectors(app: App) -> None:
    """DuckDuckGo + HTTP in ConnectorRegistry + Policies."""
```

Tests (mit CliRunner + input):
- Default-Flow (1 Provider, alle Defaults akzeptiert)
- Multi-Provider (Anthropic + Ollama)
- Custom Server
- Smart Defaults werden korrekt gesetzt
- Generation #1 wird erzeugt mit vollem State
- Built-in Connectors werden registriert
- Reinit fragt nach Bestaetigung

---

### Task 4: Ollama Integration — Modell-Discovery

**Files:**
- Modify: `src/mycelos/llm/providers.py` (discover_ollama_models)
- Create: `tests/test_ollama_discovery.py`

**Was:**

```python
def discover_ollama_models(url: str = "http://localhost:11434") -> list[ModelInfo]:
    """Query Ollama API for locally available models.

    GET {url}/api/tags
    Response: {"models": [{"name": "llama3.3:latest", "size": 4661224676, ...}]}
    """
    import httpx
    try:
        resp = httpx.get(f"{url}/api/tags", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return [
            ModelInfo(
                id=f"ollama/{m['name']}",
                name=m["name"],
                tier=_classify_ollama_tier(m),
                input_cost_per_1k=0.0,   # lokal = kostenlos
                output_cost_per_1k=0.0,
                max_context=_estimate_context(m),
                enabled=True,
            )
            for m in data.get("models", [])
        ]
    except Exception:
        return []
```

Tests (gemockter HTTP):
- Ollama laeuft → Modelle gefunden
- Ollama nicht erreichbar → leere Liste, kein Crash
- Modell-Tier-Klassifikation (llama → sonnet, phi → haiku)

---

### Task 5: Custom Server Support

**Files:**
- Modify: `src/mycelos/llm/providers.py`
- Modify: `src/mycelos/cli/init_cmd.py`
- Create: `tests/test_custom_server.py`

**Was:**

Custom Server wird gespeichert als:
- `llm_models` Row mit `provider="custom"` und `id="custom/{model_id}"`
- Credential mit `api_base` URL

```python
# In LiteLLMBroker.complete():
if model.startswith("custom/"):
    # LiteLLM supports openai-compatible endpoints via api_base
    kwargs["api_base"] = self._get_custom_base(model)
```

Tests:
- Custom Server registrieren mit URL + Key
- Custom Server ohne Key (lokal)
- Modell-ID korrekt in `llm_models`

---

### Task 6: Integration — Alles zusammen + Generation #1

**Files:**
- Create: `tests/test_init_integration.py`

**Was:**
- Vollstaendiger Init-Flow → DB hat korrekte Tabellen befuellt
- `llm_models` hat alle aktivierten Modelle
- `agent_llm_models` hat Smart Defaults
- `connectors` hat DuckDuckGo + HTTP
- `config_generations` hat Generation #1 mit vollem State
- Rollback nach Init → Generation #1 stellt alles wieder her

---

## Reihenfolge

```
Task 1 (Provider Registry)
    ↓
Task 2 (Smart Defaults) + Task 4 (Ollama Discovery) — parallel
    ↓
Task 3 (Init Wizard)
    ↓
Task 5 (Custom Server)
    ↓
Task 6 (Integration Tests)
```

## Was wir NICHT machen

- OpenRouter dynamische API-Abfrage (spaeter — erstmal statisch wie andere Cloud-Provider)
- Model-Benchmarking oder Auto-Selection basierend auf Performance
- Hot-Reload von Modellen (spaeter ueber Config-Changes)
- `mycelos config models` CLI Command (spaeter — erstmal nur Init)
