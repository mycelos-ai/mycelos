# Rich Web Components — Channel-Aware Agent Responses

## Übersicht

Der Agent passt seine Antworten an den Client an:
- **Web UI:** Rich Components (Formulare, Buttons, Cards) statt Slash-Commands
- **CLI:** Text + Slash-Commands (wie bisher)
- **Telegram:** Kompakter Text + Inline-Buttons

## Alle Slash-Commands und ihre Web-UI Ersetzung

| Command | Aktuell | Web UI Replacement | Bypasses LLM? |
|---------|---------|-------------------|----------------|
| `/help` | Markdown-Liste | Help-Panel / Sidebar | Nein |
| `/memory` | Text-Liste | Memory-Cards mit Edit/Delete Buttons | Nein |
| `/memory set` | Textuelle Eingabe | Settings-Formular mit Labels | Nein |
| `/mount` | Text add/revoke | Mount-Manager mit Path-Picker | Nein |
| `/sessions` | Text-Liste | Session-Liste (existiert schon als Seite) | Nein |
| `/cost` | Text-Tabelle | Cost-Dashboard mit Charts | Nein |
| `/config` | Text + Rollback | Config-Card mit Rollback-Button | Nein |
| `/agent` | Text info/grant/revoke | Agent-Cards mit Toggle-Switches | Nein |
| `/connector` | Text add/test/search | **Connector Wizard** (teilweise gebaut) | Teilweise (Secret) |
| `/schedule` | Text add/delete/pause | Schedule-Tabelle mit Pause/Resume | Nein |
| `/workflow` | Text list/delete | Workflow-Cards (existiert schon) | Nein |
| `/run` | Text-Ausführung | **Workflow Run Form** (gebaut) | Nein |
| `/model` | Text-Liste | Model-Tabelle | Nein |
| `/reload` | Neustart MCP | Reload-Button mit Spinner | Nein |
| `/restart` | Gateway-Neustart | **Confirmation Dialog** (destructive) | Nein |
| `/credential` | Text store/delete | **Secure Input Form** | **JA — MUSS LLM bypassen** |
| `/bg` | Task-Liste | Task-Panel mit Status-Badges | Nein |
| `/inbox` | Datei-Liste | Inbox-Cards mit Clear-Button | Nein |

## Security-Grenze: Was MUSS am LLM vorbei?

### Hard Bypass (Credential-Material)
- **Credential Store** — API Keys gehen direkt an `POST /api/credentials`. Web UI rendert `SecureInput` Widget mit Passwort-Feld. Wert erscheint NIE im Chat-Verlauf.

### Soft Bypass (Destruktiv mit Bestätigung)
- Config Rollback → Confirmation Dialog
- Restart → Confirmation Dialog (danger)
- Agent Revoke → Confirmation Dialog
- Workflow Delete → Confirmation Dialog
- Memory Clear → Confirmation Dialog

## Neue Widget-Typen

| Widget | Zweck | Beispiel |
|--------|-------|---------|
| `SecureInput` | Passwort-Feld das direkt an API postet | Credential eingeben |
| `FormGroup` | Mehrere Felder mit Validierung + Submit | Workflow-Inputs, Mount hinzufügen |
| `ActionBar` | Button-Reihe die API-Endpoints aufruft | Reload, Restart, Pause/Resume |
| `DataTable` | Tabelle mit Row-Actions (Delete, Edit) | Credentials, Schedules, Mounts |

Existierende Widgets: TextBlock, Table, ChoiceBox, StatusCard, ProgressBar, CodeBlock, Confirm, ImageBlock, Compose.

## Channel-Detection im System-Prompt

`ChatRequest.channel` ist schon vorhanden ("api", "telegram", "cli"). Wird an den System-Prompt angehängt:

```python
_CHANNEL_PROMPTS = {
    "api": "The user is on the web interface. Instead of suggesting slash commands, "
           "emit widget events for forms, tables, and actions.",
    "cli": "The user is on the terminal. Suggest slash commands for system actions.",
    "telegram": "The user is on Telegram. Keep responses under 4096 chars. "
                "Use inline keyboard buttons.",
}
```

## Implementierungsphasen

### Phase 1: Channel-Aware Prompt (1h)
System-Prompt-Builder bekommt `channel` Parameter. Agent verhält sich je nach Client anders. Kein Frontend-Change nötig — Agent gibt einfach andere Texte.

### Phase 2: Widget-Typen + Web-Rendering (4h)
Neue Widget-Definitionen in `widgets/types.py`. Chat.html rendert `widget` Events als Formulare, Buttons, Tabellen.

### Phase 3: Agent-Emitted Widgets (4h)
Agent gibt `widget_event` statt Text zurück wenn User auf Web ist. "Add my OpenAI key" → `SecureInput` Widget statt `/credential store openai <key>`.

### Phase 4: Direct API Actions (2h)
Widget-Buttons rufen REST-Endpoints direkt auf statt Chat-Messages zu senden. Fehlende Endpoints: `POST /api/reload`, `PATCH /api/schedules/{id}`.

## UI-gesteuerte Workflows (Unified Pattern)

### Kernidee

Setup-Flows (Connector Add, Telegram Onboarding, Email-Setup) werden als **Workflows mit Widget-Definitionen** implementiert. Statt hardcoded Setup-Code definiert ein Workflow die Schritte UND die UI-Komponenten:

```yaml
name: connector-setup
interactive: true
inputs:
  - name: connector_type
    type: choice
    options: [email, telegram, github, playwright, ...]

widgets:
  - step: choose_connector
    type: choice_grid     # Use-Case Tiles
  - step: enter_credentials
    type: secure_input    # LLM-Bypass
  - step: test_connection
    type: action_button   # "Test" mit Spinner
  - step: confirm
    type: status_card     # Grün/Rot Ergebnis
```

### Interaktionstypen

| Aktion | Typ | UX |
|--------|-----|-----|
| Connector Add (bekannt) | **Formular** | Multi-Field Dialog, direkt an API |
| Connector Add (unbekannt) | **Interview** → Formular | Agent sucht → dann Setup-Form |
| Credential Store | **Formular** | SecureInput, LLM-bypass |
| Workflow erstellen | **Interview** | Builder-Agent fragt, User beschreibt |
| Agent erstellen | **Interview** | Builder-Agent oder Persona-Form |
| Telegram Onboarding | **Workflow** | Token → Test → Allowlist → fertig |
| Schedule | **Formular** | Cron-Picker + Workflow-Auswahl |

### Warum als Workflow?

1. **Ein Pattern für alles** — Connector-Setup, Onboarding, Email-Setup: gleicher Mechanismus
2. **Kein neuer Code pro Connector** — nur Widget-Definitionen in YAML
3. **Agent delegiert** — System-Prompt sagt: "Wenn User Connector will → starte connector-setup Workflow"
4. **Testbar** — Workflow-Tests prüfen den ganzen Flow
5. **Erweiterbar** — neue Connectors = neue YAML-Definition, kein Code

### Verbindung zu bestehenden Bausteinen

- **Workflows** liefern Plan + Tools + Inputs + Schritte
- **Widgets** liefern die UI-Komponenten (SecureInput, FormGroup, etc.)
- **Persona-Agents** können den Flow interaktiv moderieren
- **Channel-Awareness** bestimmt ob Widgets oder Text gerendert werden

### System-Prompt Integration

```
Web-Agent erkennt: "User will Telegram einrichten"
→ Startet connector-setup Workflow mit connector_type="telegram"
→ Workflow rendert Widget-Schritte im Chat
→ User interagiert mit Formularen (kein Tippen nötig)
→ Credentials gehen direkt an API (LLM-bypass)
→ Test + Bestätigung als StatusCard
```

Für CLI/Telegram: gleicher Workflow, aber Text-basiert statt Widget-basiert.
