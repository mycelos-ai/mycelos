# Slash-Commands: Sichere System-Befehle am LLM vorbei

> Konzeptioneller Plan + Implementierung

---

## Konzept

Alles mit `/` geht NICHT ans LLM. Es wird direkt vom System verarbeitet.
Credentials, Permissions, Config-Aenderungen fliessen nie durch den LLM-Pfad.

```
User: "Ich will auch Brave Search"
Mycelos (LLM): "Klar! Richte es ein mit:
               /connector setup brave"

User: /connector setup brave
System: "Enter your Brave Search API key: ********"
System: "Brave Search eingerichtet!"

User: "Der news-agent soll auch http nutzen duerfen"
Mycelos (LLM): "Ich kann ihm die Berechtigung geben. Fuehre aus:
               /agent news-agent grant http.get"

User: /agent news-agent grant http.get
System: "Capability 'http.get' fuer 'news-agent' freigegeben."
```

## Slash-Command Kategorien

### `/connector` — Connector-Management (Credentials sicher)
```
/connector list                     → Zeige alle Connectors
/connector setup <name>             → Interaktives Setup (Key-Eingabe)
/connector remove <name>            → Connector entfernen
/connector test <name>              → Connector testen
```

### `/agent` — Agent-Management + Permissions
```
/agent list                         → Alle Agents mit Capabilities
/agent <id> info                    → Detail-Info zu einem Agent
/agent <id> grant <capability>      → Capability freigeben
/agent <id> revoke <capability>     → Capability entziehen
/agent <id> model <model-id>        → LLM-Modell aendern
/agent <id> model list              → Zugewiesene Modelle anzeigen
/agent <id> disable                 → Agent deaktivieren
/agent <id> enable                  → Agent aktivieren
```

### `/config` — System-Konfiguration
```
/config show                        → Aktueller State (Tabellen-Ansicht)
/config show <gen-id>               → Bestimmte Generation
/config diff <a> <b>                → Vergleich
/config rollback [<gen-id>]         → Rollback
/config show --json                 → Raw JSON
```

### `/schedule` — Cron-Jobs
```
/schedule list                      → Geplante Tasks
/schedule add <wf> --cron "..."     → Neuen Job erstellen
/schedule pause <id>                → Pausieren
/schedule resume <id>               → Fortsetzen
/schedule delete <id>               → Loeschen
/schedule run <id>                  → Sofort ausfuehren
```

### `/model` — LLM-Modelle
```
/model list                         → Verfuegbare Modelle
/model set-default <model-id>       → System-Default aendern
/model cost                         → Kosten-Uebersicht
```

### `/memory` — Memory-Management
```
/memory list                        → Gespeicherte Eintraege
/memory search <query>              → Suchen
/memory delete <key>                → Eintrag loeschen
/memory clear                       → Alles loeschen (mit Bestaetigung)
```

### `/workflow` — Workflow-Management
```
/workflow list                      → Definierte Workflows
/workflow show <id>                 → Detail mit Steps
/workflow runs                      → Laufende/pausierte Runs
/workflow resume <run-id>           → Pausierten Run fortsetzen
/workflow abort <run-id>            → Run abbrechen
```

### `/help` — Hilfe
```
/help                               → Uebersicht aller Commands
/help <command>                     → Detail-Hilfe
```

---

## LLM-Integration: Vorschlaege machen

Das LLM weiss welche Slash-Commands es gibt und kann sie vorschlagen:

```python
# Im System-Prompt:
"""
## Slash-Commands (fuer den User)
Wenn der User etwas braucht das Credentials oder Permissions benoetigt,
schlage den passenden Slash-Command vor. Du fuehrst ihn NICHT aus —
der User tippt ihn selbst.

Beispiele:
- User will neuen Connector: schlage "/connector setup <name>" vor
- User will Agent Rechte geben: schlage "/agent <id> grant <cap>" vor
- User will Config sehen: schlage "/config show" vor
- User will Job planen: schlage "/schedule add <wf> --cron '...'" vor

WICHTIG: Schlage Commands vor, fuehre sie nicht aus.
Credentials und Permissions gehen IMMER ueber Slash-Commands.
"""
```

---

## Implementierung

### Chat-Level Command Router

```python
# In chat_cmd.py REPL-Loop, VOR dem ChatService:
if user_input.startswith("/"):
    result = handle_slash_command(app, user_input, session_id)
    console.print(Markdown(result))
    continue  # Skip LLM

# Im Gateway: gleiches Prinzip
if request.message.startswith("/"):
    result = handle_slash_command(app, request.message)
    return SSE([system_response_event(result), done_event()])
```

### Command Parser

```python
# src/mycelos/chat/slash_commands.py

def handle_slash_command(app, command: str, session_id: str | None = None) -> str:
    """Parse and execute a slash command. Returns response text."""
    parts = command.strip().split()
    if not parts:
        return "Unbekannter Befehl. Tippe /help fuer Hilfe."

    cmd = parts[0].lower().lstrip("/")
    args = parts[1:]

    handlers = {
        "help": handle_help,
        "connector": handle_connector,
        "agent": handle_agent,
        "config": handle_config,
        "schedule": handle_schedule,
        "model": handle_model,
        "memory": handle_memory,
        "workflow": handle_workflow,
    }

    handler = handlers.get(cmd)
    if handler is None:
        return f"Unbekannter Befehl: /{cmd}. Tippe /help fuer Hilfe."

    return handler(app, args)
```

### Agent Grant/Revoke (der spannende Teil)

```python
def handle_agent(app, args):
    if len(args) < 1:
        return _agent_list(app)

    agent_id = args[0]

    if len(args) == 1 or args[1] == "info":
        return _agent_info(app, agent_id)

    action = args[1]

    if action == "grant" and len(args) >= 3:
        capability = args[2]
        # Add capability to agent
        agent = app.agent_registry.get(agent_id)
        if not agent:
            return f"Agent '{agent_id}' nicht gefunden."
        current_caps = agent.get("capabilities", [])
        if capability in current_caps:
            return f"Agent '{agent_id}' hat '{capability}' bereits."
        app.agent_registry.set_capabilities(agent_id, current_caps + [capability])
        # Set policy
        app.policy_engine.set_policy("default", agent_id, capability, "always")
        # New generation
        app.config.apply_from_state(
            app.state_manager,
            description=f"Agent '{agent_id}' granted '{capability}'",
            trigger="slash_command",
        )
        return f"Capability '{capability}' fuer '{agent_id}' freigegeben."

    if action == "revoke" and len(args) >= 3:
        capability = args[2]
        agent = app.agent_registry.get(agent_id)
        if not agent:
            return f"Agent '{agent_id}' nicht gefunden."
        current_caps = agent.get("capabilities", [])
        if capability not in current_caps:
            return f"Agent '{agent_id}' hat '{capability}' nicht."
        app.agent_registry.set_capabilities(
            agent_id, [c for c in current_caps if c != capability]
        )
        app.config.apply_from_state(
            app.state_manager,
            description=f"Agent '{agent_id}' revoked '{capability}'",
            trigger="slash_command",
        )
        return f"Capability '{capability}' fuer '{agent_id}' entzogen."

    if action == "model" and len(args) >= 3:
        model_id = args[2]
        # ... set model for agent ...
```

---

## Security-Garantien

1. **Slash-Commands gehen NIE ans LLM** — kein Teil des Commands oder seiner Ausgabe wird in die Conversation injiziert
2. **Credentials werden im Slash-Command Handler verarbeitet** — direkte Eingabe, direkte Verschluesselung
3. **Permission-Aenderungen erzeugen Config Generations** — rollbackbar
4. **Das LLM kann Slash-Commands VORSCHLAGEN** aber nicht AUSFUEHREN
5. **Audit-Log** fuer jeden Slash-Command der State aendert

---

## Tasks

### Task 1: Slash-Command Router + /help
- Create: `src/mycelos/chat/slash_commands.py`
- Modify: `src/mycelos/cli/chat_cmd.py` (intercept `/` before ChatService)
- Modify: `src/mycelos/gateway/routes.py` (intercept `/` in API)

### Task 2: /connector (setup mit sicherer Key-Eingabe)
### Task 3: /agent (list, info, grant, revoke, model)
### Task 4: /config, /schedule, /model, /memory, /workflow (delegates to existing)
### Task 5: System-Prompt Update (LLM kennt Slash-Commands)
### Task 6: Tests
