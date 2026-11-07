# Memory System V2 — Persistent Agent Knowledge

**Status:** Specification
**Date:** 2026-03-22

---

## Vision

Mycelos merkt sich was wichtig ist — ueber Sessions, Agents und Workflows hinweg.
Nicht nur Chat-History, sondern **strukturiertes Wissen**: Praeferenzen, Entscheidungen,
Kontext, Fakten. Alles in SQLite, queryable, und fuer den User transparent.

---

## Was wir haben

```sql
memory_entries:
  id, user_id, scope, agent_id, key, value, created_by, created_at, updated_at, expires_at
```

```python
SQLiteMemoryService:
  get(user_id, scope, key, agent_id=None) → value
  set(user_id, scope, key, value, agent_id=None)
  search(user_id, scope, query) → list
  delete(user_id, scope, key)
```

**Genutzt fuer:** User-Name (`user.name`), Pending Agent Defaults. Sonst nichts.

## Was fehlt

1. **Agents nutzen Memory nicht** — kein Agent liest oder schreibt
2. **Kein Session-Memory** — nichts wird aus Chats extrahiert
3. **Memory nicht im Prompt** — LLM weiss nicht was gespeichert ist
4. **Kein Expiry-Check** — `expires_at` wird nie geprueft
5. **Keine Kategorisierung** — nur Key-Value, kein Typ (Praeferenz, Fakt, Entscheidung)

---

## Memory-Architektur: Drei Quellen

```
┌─────────────────────────────────────────────────┐
│ 1. AGENT MEMORY TOOLS (real-time)               │
│    Agent schreibt waehrend Ausfuehrung:          │
│    "User mag Markdown" → memory.write()          │
│    Agent liest vor Ausfuehrung:                   │
│    memory.read("user.preference.*") → Kontext    │
└──────────────────────┬──────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────┐
│ 2. SESSION SUMMARY (async, via Huey)            │
│    Nach Session-Ende:                            │
│    LLM (Haiku) analysiert Conversation           │
│    → Extrahiert Praeferenzen, Entscheidungen     │
│    → Schreibt in memory_entries                  │
└──────────────────────┬──────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────┐
│ 3. MEMORY INJECTION (bei jedem LLM-Call)        │
│    Relevante Eintraege werden in den             │
│    System-Prompt injiziert                       │
│    → LLM weiss was es ueber den User weiss      │
└─────────────────────────────────────────────────┘
```

---

## 1. Agent Memory Tools

Jeder Agent bekommt `memory_read` und `memory_write` als Tools — genauso wie `search_web` und `http_get`. Das LLM entscheidet selbst wann es etwas merkenswert findet.

### Tool-Definitionen

```python
MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "memory_read",
            "description": "Read stored facts about the user or context. "
                "Use this to recall preferences, decisions, or past context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look up (e.g., 'format preference', 'current project')",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": "Remember an important fact about the user. "
                "Use this when the user states a preference, makes a decision, "
                "or shares context that should be remembered for future sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["preference", "decision", "context", "fact"],
                        "description": "Type of information",
                    },
                    "key": {
                        "type": "string",
                        "description": "Descriptive key (e.g., 'output_format', 'current_project')",
                    },
                    "value": {
                        "type": "string",
                        "description": "The fact to remember (1-2 sentences max)",
                    },
                },
                "required": ["category", "key", "value"],
            },
        },
    },
]
```

### Wann schreibt der Agent?

Der System-Prompt sagt dem LLM:

```
## Memory
Du hast Zugriff auf ein persistentes Memory-System.

SCHREIBE ins Memory wenn der User:
- Eine Praeferenz aeussert ("Ich mag keine Emojis")
- Eine Entscheidung trifft ("Wir nehmen Claude Sonnet")
- Wichtigen Kontext teilt ("Ich arbeite an einem AI-Projekt")
- Etwas Wiederkehrendes erwaehnt ("Mein Team-Meeting ist Montags")

SCHREIBE NICHT:
- Jede Kleinigkeit ("User hat Hallo gesagt")
- Dinge die im Chat-Kontext bleiben (aktuelle Frage)
- Sensible Daten (Passwoerter, persoenliche Details)

LESE aus dem Memory wenn du:
- Unsicher bist ob der User eine Praeferenz hat
- Kontext aus frueheren Sessions brauchst
- Eine Entscheidung referenzieren willst
```

### Key-Schema

```
user.preference.{name}     → "Prefers Markdown output"
user.decision.{name}       → "Uses Claude Sonnet for code review"
user.context.{name}        → "Working on Mycelos agent OS"
user.fact.{name}           → "Team meeting is Monday 10am"
agent.{id}.{name}          → Agent-spezifische Erinnerungen
workflow.{id}.{name}       → Workflow-spezifische Daten
```

### Execution

```python
def _execute_tool(self, tool_name, args):
    if tool_name == "memory_write":
        category = args.get("category", "fact")
        key = f"user.{category}.{args.get('key', 'unknown')}"
        self._app.memory.set(
            "default", "system", key,
            args["value"], created_by="agent",
        )
        return {"status": "remembered", "key": key}

    elif tool_name == "memory_read":
        query = args.get("query", "")
        results = self._app.memory.search("default", "system", query)
        if not results:
            return {"results": [], "message": "Nothing found in memory"}
        return {"results": [
            {"key": r["key"], "value": r["value"]} for r in results[:10]
        ]}
```

---

## 2. Session Summary (Async via Huey)

Bereits implementiert in `src/mycelos/scheduler/session_summary.py`:

```python
# Laeuft alle 5 Minuten als Huey-Job:
def process_stale_sessions(app, stale_minutes=30):
    # Findet Sessions die >30 Min inaktiv sind
    # LLM (Haiku) extrahiert wichtige Fakten
    # Schreibt in memory_entries
```

### Was extrahiert wird

```json
{
  "preferences": [{"key": "output_format", "value": "Prefers Markdown"}],
  "decisions": [{"key": "llm_model", "value": "Uses Claude Sonnet"}],
  "context": [{"key": "current_project", "value": "Building Mycelos"}],
  "facts": [{"key": "team_meeting", "value": "Monday 10am"}]
}
```

### Deduplizierung

Wenn ein Key schon existiert, wird der Wert **aktualisiert** (nicht dupliziert).
Das passiert automatisch durch `SQLiteMemoryService.set()` (UPSERT-Logik).

---

## 3. Memory Injection in System-Prompt

Bei jedem LLM-Call werden relevante Memory-Eintraege in den Prompt injiziert.

### Ohne Embeddings (Phase 1 — jetzt)

```python
def inject_memory_context(app, user_id="default") -> str:
    """Build memory context for system prompt injection.

    Loads recent/relevant memory entries and formats them
    for the LLM to understand the user's context.
    """
    entries = app.memory.search(user_id, "system", "")  # Alle System-Scope
    if not entries:
        return ""

    # Gruppiere nach Kategorie
    preferences = []
    decisions = []
    context = []
    facts = []

    for e in entries[:30]:  # Max 30 Eintraege
        key = e["key"]
        value = e["value"]
        if "preference" in key:
            preferences.append(f"- {key.split('.')[-1]}: {value}")
        elif "decision" in key:
            decisions.append(f"- {key.split('.')[-1]}: {value}")
        elif "context" in key:
            context.append(f"- {key.split('.')[-1]}: {value}")
        else:
            facts.append(f"- {key.split('.')[-1]}: {value}")

    parts = ["<memory>"]
    if preferences:
        parts.append("User Preferences:\n" + "\n".join(preferences))
    if decisions:
        parts.append("Decisions:\n" + "\n".join(decisions))
    if context:
        parts.append("Current Context:\n" + "\n".join(context))
    if facts:
        parts.append("Known Facts:\n" + "\n".join(facts))
    parts.append("</memory>")

    return "\n\n".join(parts)
```

### Integration in ChatService

```python
# In ChatService.get_system_prompt():
memory_context = inject_memory_context(self._app)
if memory_context:
    prompt += "\n\n" + memory_context
```

### Mit Embeddings (Phase 2 — spaeter)

```python
def inject_memory_context_semantic(app, user_message, user_id="default") -> str:
    """Semantic search: find memory entries similar to the current message."""
    # Embed user message
    embedding = app.embedding_model.embed(user_message)
    # Search for similar entries
    similar = app.memory.semantic_search(user_id, embedding, limit=5)
    # Format
    ...
```

---

## User-Transparenz: `/memory` Slash-Command

Der User muss sehen koennen was gespeichert ist und es verwalten koennen:

```bash
/memory list                    # Alle gespeicherten Eintraege
/memory list --category preference  # Nur Praeferenzen
/memory search "format"         # Suchen
/memory delete user.preference.emojis  # Loeschen
/memory clear                   # Alles loeschen (mit Bestaetigung)
```

Beispiel-Output:

```
Memory Entries (12 total):

Preferences:
  output_format:    Prefers Markdown without emojis
  language:         German (switches to English for tech)
  news_topics:      AI, Security, NixOS

Decisions:
  llm_model:        Claude Sonnet as primary
  search_provider:  DuckDuckGo (no Brave)

Context:
  current_project:  Building Mycelos agent OS
  team:             Solo developer

Facts:
  timezone:         CET (Europe/Vienna)
  name:             Stefan
```

---

## Schema-Erweiterung

Die bestehende `memory_entries` Tabelle reicht — wir nutzen das Key-Schema
fuer Kategorisierung (`user.preference.*`, `user.decision.*`, etc.).

Optional spaeter:
```sql
-- Fuer Embedding-Support:
CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id   INTEGER REFERENCES memory_entries(id),
    embedding   BLOB NOT NULL,
    model       TEXT NOT NULL,
    PRIMARY KEY (memory_id)
);
```

---

## Memory im NixOS State?

**Nein.** Memory ist **Lernzustand**, nicht deklarative Konfiguration.

Bei Rollback:
- Memory-Eintraege bleiben erhalten (nicht geloescht)
- Aber: Eintraege die von einem gerollbackten Agent geschrieben wurden,
  werden als `orphaned` markiert (nicht geloescht, aber gekennzeichnet)

---

## Implementierungs-Tasks

### Task 1: Memory Tools fuer ChatService
- Memory-Tools zu `CHAT_AGENT_TOOLS` hinzufuegen
- `_execute_tool()` Handler fuer `memory_read` + `memory_write`
- System-Prompt Update: wann schreiben/lesen
- Tests

### Task 2: Memory Injection in System-Prompt
- `inject_memory_context()` Funktion
- Integration in `ChatService.get_system_prompt()`
- Kategorisierte Darstellung (Preferences, Decisions, Context, Facts)
- Tests

### Task 3: /memory Slash-Command
- list, search, delete, clear
- Kategorisierte Anzeige als Rich-Tabelle
- Tests

### Task 4: Session Summary Integration (schon gebaut)
- Bereits in `scheduler/session_summary.py`
- Nur sicherstellen dass Keys dem Schema folgen
- Test: Session → Summary → Memory → naechste Session sieht es

### Task 5: Expiry-Check
- Bei Memory-Read: abgelaufene Eintraege filtern
- Cron-Job: alte Eintraege aufraeumen
- Tests
