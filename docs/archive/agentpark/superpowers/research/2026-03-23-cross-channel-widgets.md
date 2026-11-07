# Cross-Channel Widget System — Research Findings

**Datum:** 2026-03-23
**Autor:** Stefan / Claude Research
**Status:** Research Phase — Entscheidungsgrundlage

---

## 1. Problemstellung

Mycelos liefert Antworten über mehrere Kanäle: CLI (Terminal), Telegram, WhatsApp, Slack, und zukünftig eine Next.js Web-App. Reine Textausgabe reicht nicht — Agents sollen strukturierte Widgets rendern können (Tabellen, Option-Boxen, Status-Cards, Fortschrittsanzeigen, etc.), die sich kanalspezifisch anpassen.

**Anforderungen:**
- Ein Widget wird einmal definiert, passt sich aber an jeden Kanal an
- Graceful Degradation: Wenn ein Kanal keine Rich-UI kann, wird Fallback-Text geliefert
- Neue Kanäle hinzufügen, ohne bestehende Agents zu ändern
- Interaktive Elemente (Buttons, Auswahl) wo möglich, Text-Eingabe wo nötig

---

## 2. Kanal-Fähigkeiten im Überblick

| Fähigkeit | CLI (Rich) | Telegram | WhatsApp | Slack | Web (Next.js) |
|-----------|-----------|----------|----------|-------|---------------|
| Tabellen | ASCII/Box-Drawing | Monospace-Blöcke (max ~40 Zeichen mobil) | Monospace-Blöcke | Block Kit Sections + Fields | Vollständige HTML-Tabellen |
| Buttons/Auswahl | Nummerierte Eingabe (1,2,3...) | Inline Keyboards (Callback-Buttons) | Max 3 Quick-Reply-Buttons ODER 10er-Liste | Actions Block (Buttons, Selects, Datepicker) | React-Komponenten (beliebig) |
| Bilder | Nicht direkt (ASCII-Art möglich) | Inline-Bilder + Captions | Inline-Bilder | Image Blocks | Vollständig |
| Rich Text | ANSI-Farben, Bold, Italic via Rich | MarkdownV2 / HTML-Subset | Basic Formatting | mrkdwn (Slack-Markdown) | Volles Markdown/HTML/JSX |
| Formulare | Sequentielle Prompts | Mini Apps (Web-App in Telegram) | Nicht möglich | Modal Views | React Forms |
| Cards/Panels | Rich Panels + Boxen | Kein natives Format (Text-Emulation) | Nicht möglich | Section + Context Blocks | Beliebige Komponenten |
| Fortschritt | Rich Progress Bars | Text-Updates (Edit Message) | Nicht möglich | Message Updates | Live-Streaming UI |
| Max Interaktivität | Hoch (Textual TUI) | Hoch (Mini Apps = volle Web-App) | Niedrig | Mittel-Hoch | Unbegrenzt |

---

## 3. Architektur-Patterns aus der Industrie

### 3.1 Microsoft Adaptive Cards

**Konzept:** JSON-basierte deklarative UI-Spezifikation. Eine Card wird einmal als JSON definiert und rendert nativ auf Teams, Outlook, Windows, Web etc.

**Schema-Struktur:**
```json
{
  "type": "AdaptiveCard",
  "version": "1.5",
  "body": [
    { "type": "TextBlock", "text": "Aufgabe erledigt", "weight": "Bolder" },
    { "type": "FactSet", "facts": [
      { "title": "Status", "value": "Abgeschlossen" },
      { "title": "Dauer", "value": "3m 42s" }
    ]}
  ],
  "actions": [
    { "type": "Action.Submit", "title": "OK" }
  ]
}
```

**Stärken:** Bewährtes Format, native Darstellung pro Plattform, Fallback-Mechanismus eingebaut.
**Schwächen:** Primär Microsoft-Ökosystem, begrenzte Anpassbarkeit, keine Terminal-Renderer vorhanden.

**Relevanz für Mycelos:** Das Schema-Konzept (deklarative Widgets mit Fallback) ist direkt übertragbar. Die Implementierung müsste aber eigene Renderer bekommen.

### 3.2 Slack Block Kit

**Konzept:** JSON-basiertes UI-Framework mit Blöcken (Section, Actions, Context, Header, Divider, Image, Rich Text) und interaktiven Elementen (Buttons, Select-Menus, Datepicker).

**Stärken:** Gut durchdachte interaktive Elemente, erprobtes System.
**Schwächen:** Slack-only, nicht portabel.

**Relevanz für Mycelos:** Block Kit zeigt gut, wie man interaktive Elemente strukturiert. Das Actions-Pattern (Button-Array mit Callbacks) ist direkt auf Telegram Inline Keyboards und CLI-Nummerierung übertragbar.

### 3.3 Server-Driven UI (Airbnb, Lyft, Netflix)

**Konzept:** Backend definiert die UI-Struktur als JSON. Jeder Client (iOS, Android, Web) hat einen Renderer, der dieses JSON in native Komponenten übersetzt.

**Airbnb's "Ghost Platform":**
- Shared GraphQL Schema für alle Plattformen
- Server-seitige Component Registry mit Validierung
- A/B-Tests durch serverseitige UI-Änderung (keine App-Updates nötig)
- Rollback in Sekunden

**Stärken:** Entkopplung von Backend und Frontend, schnelle Iteration, ein Change → alle Plattformen.
**Schwächen:** Pixel-perfektes Design schwierig, komplexe Interaktionen brauchen trotzdem nativen Code.

**Relevanz für Mycelos:** Direkt anwendbar — Agents definieren Widget-Strukturen, Channel-Renderer übersetzen in plattformspezifische Darstellung.

### 3.4 Vercel AI SDK — Generative UI

**Konzept:** LLM-Tool-Calls werden auf React-Komponenten gemappt. Tools haben Zod-Schemas für Input/Output, und eine `render`-Funktion, die das Ergebnis als React-Komponente darstellt.

**Besonders relevant: Vercel Chat SDK**
- JSX-Komponenten werden einmal geschrieben
- Adapter kompilieren zu plattformspezifischem Format:
  - Slack → Block Kit
  - Teams/Discord → GFM Markdown-Tabellen
  - Telegram → Code-Blöcke
  - GitHub/Linear → Markdown

**Noch relevanter: json-render Framework**
- AI generiert JSON innerhalb definierter Schemas (kein Halluzinieren von UI)
- `defineSchema()` → Zod-basierte Prop-Typen
- `defineCatalog()` → erlaubte Komponenten
- `defineRegistry()` → Mapping JSON → React-Implementierung
- Multi-Platform: `@json-render/react`, `@json-render/react-native`, `@json-render/react-pdf`

**Relevanz für Mycelos:** Das Catalog-Pattern (definiere welche Widgets erlaubt sind + Schema) passt perfekt zur Security-First-Architektur. Agents können nur Widgets aus dem Katalog verwenden.

### 3.5 React Custom Renderer Pattern

**Konzept:** React trennt Reconciliation (was hat sich geändert) von Rendering (wie wird es dargestellt). Über `react-reconciler` können Custom Renderer für beliebige Plattformen gebaut werden.

**Existierende Renderer:** react-dom (Web), react-native (Mobile), ink (Terminal), react-three-fiber (3D), react-pdf.

**Relevanz für Mycelos:** Für die Web-App relevant. Für CLI und Messaging-Kanäle ist ein Python-basiertes System (Rich/Textual) besser geeignet.

---

## 4. Empfohlene Architektur für Mycelos

### 4.1 Intermediate Representation (IR) — Der Kern

Widgets werden als plattform-agnostischer Baum definiert (ähnlich AST/Adaptive Cards), den Channel-Renderer in kanalspezifische Ausgabe übersetzen.

```
Agent Output
    ↓
Widget IR (Python-Datenstrukturen / JSON)
    ↓
Channel Renderer Registry
    ├── CLIRenderer      → Rich Panels, Tables, Prompts
    ├── TelegramRenderer → MarkdownV2, Inline Keyboards, Monospace
    ├── SlackRenderer    → Block Kit JSON
    ├── WhatsAppRenderer → Simple Text + Quick Replies
    └── WebRenderer      → React/JSON (für Next.js Frontend)
```

### 4.2 Widget-Katalog (Core Widgets)

Basierend auf der Kanalanalyse sind folgende Widgets sinnvoll:

#### `TextBlock`
Einfacher formatierter Text mit optionaler Gewichtung.

```python
TextBlock(text="Aufgabe erledigt", weight="bold")
```

| Kanal | Darstellung |
|-------|-------------|
| CLI | `Rich.Text` mit Style |
| Telegram | `**Aufgabe erledigt**` (MarkdownV2) |
| WhatsApp | `*Aufgabe erledigt*` |
| Slack | `*Aufgabe erledigt*` (mrkdwn) |
| Web | `<strong>Aufgabe erledigt</strong>` |

#### `Table`
Tabellarische Daten mit Headers und Rows.

```python
Table(
    headers=["Name", "Status", "Dauer"],
    rows=[
        ["Backup", "OK", "3m"],
        ["Sync", "Fehler", "12m"],
    ]
)
```

| Kanal | Darstellung |
|-------|-------------|
| CLI | Rich Table mit Box-Drawing-Zeichen |
| Telegram | Monospace-Block (`<pre>Name     Status  Dauer\n─────────────────────\nBackup   OK      3m\nSync     Fehler  12m</pre>`) |
| WhatsApp | Monospace-Block (gleich, aber kürzer) |
| Slack | Section mit Fields oder mrkdwn Code-Block |
| Web | HTML `<table>` mit Styling |

#### `ChoiceBox`
Auswahl aus Optionen — interaktiv wo möglich.

```python
ChoiceBox(
    prompt="Welche Aktion?",
    options=[
        Choice(id="retry", label="Nochmal versuchen"),
        Choice(id="skip", label="Überspringen"),
        Choice(id="abort", label="Abbrechen"),
    ]
)
```

| Kanal | Darstellung |
|-------|-------------|
| CLI | Nummerierte Liste + Eingabe (`[1] Nochmal versuchen  [2] Überspringen  [3] Abbrechen\n> `) |
| Telegram | Inline Keyboard mit 3 Buttons (Callback-Data = Choice.id) |
| WhatsApp | Quick-Reply-Buttons (max 3) oder List Message (bis 10) |
| Slack | Actions Block mit Buttons |
| Web | React ButtonGroup oder Dropdown |

#### `StatusCard`
Zusammenfassung mit Key-Value-Paaren.

```python
StatusCard(
    title="Task: Backup",
    facts={"Status": "Läuft", "Fortschritt": "67%", "ETA": "2 Min"},
    style="info"  # info | success | warning | error
)
```

| Kanal | Darstellung |
|-------|-------------|
| CLI | Rich Panel mit farbigem Rand (blau/grün/gelb/rot) |
| Telegram | Emoji-Prefix + fettgedruckter Titel + Zeilen |
| WhatsApp | Emoji-Prefix + Plain Text |
| Slack | Section mit Context Block |
| Web | Card-Komponente mit farbigem Badge |

#### `ProgressBar`
Fortschrittsanzeige.

```python
ProgressBar(label="Upload", current=67, total=100)
```

| Kanal | Darstellung |
|-------|-------------|
| CLI | Rich Progress Bar (`████████████░░░░░░ 67%`) |
| Telegram | Text-Update: `Upload: ▓▓▓▓▓▓▓░░░ 67%` (Message Edit) |
| WhatsApp | `Upload: 67%` (nur Text) |
| Slack | mrkdwn mit Emoji-Blöcken |
| Web | HTML Progress/Animated Bar |

#### `CodeBlock`
Code-Anzeige mit Syntax-Highlighting.

```python
CodeBlock(code="print('hello')", language="python")
```

| Kanal | Darstellung |
|-------|-------------|
| CLI | Rich Syntax (Pygments-basiert) |
| Telegram | `<pre><code class="language-python">...</code></pre>` |
| WhatsApp | Monospace-Block |
| Slack | Code-Block (dreifache Backticks) |
| Web | Prism.js / Shiki Highlighting |

#### `Confirm`
Ja/Nein-Bestätigung.

```python
Confirm(prompt="Agent 'web-scraper' registrieren?", danger=True)
```

| Kanal | Darstellung |
|-------|-------------|
| CLI | `[Y/n]` Prompt (rot bei danger) |
| Telegram | Inline Keyboard: ✅ Ja / ❌ Nein |
| WhatsApp | Quick-Reply: Ja / Nein |
| Slack | Actions mit Confirm-Dialog |
| Web | Modal Dialog |

#### `ImageBlock`
Bild-Anzeige (URL-basiert).

```python
ImageBlock(url="https://...", alt="Architektur-Diagramm", caption="System Overview")
```

| Kanal | Darstellung |
|-------|-------------|
| CLI | Alt-Text als Fallback (`[Bild: Architektur-Diagramm]`) |
| Telegram | Inline-Foto mit Caption |
| WhatsApp | Bild-Nachricht mit Caption |
| Slack | Image Block |
| Web | `<img>` mit Caption |

### 4.3 Capability Declaration Pattern

Jeder Channel deklariert seine Fähigkeiten. Der Renderer entscheidet basierend darauf, wie ein Widget dargestellt wird.

```python
@dataclass
class ChannelCapabilities:
    supports_tables: bool = False
    supports_inline_buttons: bool = False
    supports_images: bool = False
    supports_rich_text: bool = False
    supports_message_edit: bool = False  # für Progress-Updates
    supports_modals: bool = False
    supports_web_embed: bool = False     # Telegram Mini Apps
    max_buttons: int = 0
    max_message_length: int = 4096
    supports_color: bool = False

# Beispiel-Deklarationen:
CLI_CAPS = ChannelCapabilities(
    supports_tables=True, supports_rich_text=True,
    supports_color=True, max_message_length=None,
)
TELEGRAM_CAPS = ChannelCapabilities(
    supports_inline_buttons=True, supports_images=True,
    supports_rich_text=True, supports_message_edit=True,
    supports_web_embed=True, max_buttons=64,
    max_message_length=4096,
)
WHATSAPP_CAPS = ChannelCapabilities(
    supports_inline_buttons=True, supports_images=True,
    max_buttons=3, max_message_length=4096,
)
```

### 4.4 Renderer-Interface

```python
from typing import Protocol, Any

class WidgetRenderer(Protocol):
    """Jeder Channel implementiert dieses Protocol."""

    def render_text_block(self, widget: TextBlock) -> Any: ...
    def render_table(self, widget: Table) -> Any: ...
    def render_choice_box(self, widget: ChoiceBox) -> Any: ...
    def render_status_card(self, widget: StatusCard) -> Any: ...
    def render_progress_bar(self, widget: ProgressBar) -> Any: ...
    def render_code_block(self, widget: CodeBlock) -> Any: ...
    def render_confirm(self, widget: Confirm) -> Any: ...
    def render_image_block(self, widget: ImageBlock) -> Any: ...

    def render(self, widget: Widget) -> Any:
        """Dispatch basierend auf Widget-Typ."""
        ...
```

### 4.5 Composition: Mehrere Widgets in einer Antwort

Agents liefern oft zusammengesetzte Antworten. Dafür ein Container-Widget:

```python
Compose([
    TextBlock("Backup-Status", weight="bold"),
    Table(headers=["Server", "Status"], rows=[["DB", "OK"], ["Files", "Fehler"]]),
    ChoiceBox(
        prompt="Was tun?",
        options=[Choice("retry", "Retry"), Choice("ignore", "Ignorieren")]
    ),
])
```

Jeder Renderer iteriert über die Liste und rendert sequentiell.

---

## 5. Wettbewerbsanalyse: OpenClaw und andere Frameworks

### 5.1 OpenClaw — Der direkte Vergleich

OpenClaw (openclaw.ai) ist ein Open-Source Multi-Channel AI Gateway (TypeScript/Node.js, MIT-Lizenz), das als lokaler Daemon läuft und sich mit 50+ Messaging-Plattformen verbindet. Es ist derzeit das populärste Open-Source-Projekt in diesem Bereich.

**Was OpenClaw bereits hat:**

- **Channel-Adapter für 50+ Plattformen:** WhatsApp, Telegram, Slack, Discord, Signal, iMessage, Google Chat, Teams, Matrix, u.v.m. Jede Plattform hat einen dedizierten Adapter (Inbound: Normalisierung → Outbound: Plattform-natives Format).
- **Markdown als Intermediate Representation:** Agent-Responses werden als Markdown geschrieben. Ein `markdownToIR()`-Parser wandelt in eine IR um, `renderMarkdownWithMarkers()` wendet plattformspezifisches Styling an, `chunkMarkdownIR()` splittet für Plattform-Message-Limits.
- **Adaptive Rendering:** Markdown-Tabellen → Text-Listen (WhatsApp), volle HTML-Tabellen (Discord/Web), Code-Blöcke (Telegram). Automatische Degradation je nach Plattform-Fähigkeit.
- **Canvas-Komponente:** Eingebettete Web-Komponente für interaktive Dashboards/Formulare (funktioniert über Web, macOS WebKit, iOS, Android WebView).
- **Control UI:** Dashboard gebaut mit Lit.js Web Components (nicht React), Chat-Interface, Session-Monitoring, Konfiguration.

**OpenClaw's Grenzen und Lücken:**

- **Markdown-only als IR:** OpenClaw nutzt Markdown als einziges Zwischenformat. Das bedeutet: keine strukturierten Widgets, keine typisierte Widget-IR, kein Schema-basierter Katalog. Ein Agent kann keine `ChoiceBox` oder `StatusCard` erzeugen — nur Markdown schreiben und hoffen, dass der Adapter es sinnvoll übersetzt.
- **Keine interaktiven Widgets:** Keine Inline-Keyboards, keine Buttons, keine Formulare über den Channel-Layer. Die Canvas-Komponente ist ein separater Webview, kein integriertes Widget-System.
- **Kein Widget-Katalog:** Agents haben keinen definierten Satz von UI-Primitiven. Jeder Agent formatiert Markdown frei — keine Konsistenz, keine Validierung, keine Security-Kontrolle über erlaubte UI-Elemente.
- **TypeScript-only:** Kein Python-Support. Für Python-basierte Agent-Systeme muss man über HTTP/WebSocket bridgen.
- **Kein Capability-System:** Channels haben keine deklarierten Fähigkeiten. Die Adaptation passiert implizit in den Adaptern, nicht explizit über ein Capability-Modell.

### 5.2 Vergleich: Andere Agent-Frameworks

| Framework | Widget-System | Multi-Channel | Adaptive Rendering | Interaktive Elemente |
|-----------|:---:|:---:|:---:|:---:|
| **OpenClaw** | ⚠️ Markdown IR + Canvas | ✅ 50+ Plattformen | ✅ Plattform-Adapter | ❌ Nur Canvas (separater Webview) |
| **Claude Code (Anthropic)** | ❌ Terminal only | ❌ CLI einziger Kanal | ⚠️ Rich-Terminal (Markdown) | ❌ |
| **Cline** | ❌ VS Code Extension | ❌ Nur IDE | ❌ | ❌ |
| **Aider** | ❌ Terminal CLI | ❌ Nur Terminal | ⚠️ ANSI-Farben | ❌ |
| **Open Interpreter** | ❌ Terminal-Streaming | ❌ Terminal + Android (Termux) | ⚠️ Markdown-Streaming | ❌ |
| **CrewAI** | ❌ Kein UI-System | ❌ Programmatisch | ⚠️ JSON/Pydantic Output | ❌ |
| **AutoGPT** | ⚠️ Block-System | ⚠️ Separates Frontend | ⚠️ JSON Output | ⚠️ Eingeschränkt |

### 5.3 Alleinstellungsmerkmal-Analyse für Mycelos

**Was KEIN Framework bisher hat:**

1. **Typisierte Widget IR mit Schema-Katalog:** Kein bestehendes Agent-Framework bietet einen definierten, validierten Satz von UI-Primitiven (Table, ChoiceBox, StatusCard, etc.), die Agents als strukturierte Objekte erzeugen und die dann kanalspezifisch gerendert werden. OpenClaw kommt mit Markdown am nächsten, aber Markdown ist unstrukturiert und nicht validierbar.

2. **Security-kontrollierte Widget-Erzeugung:** Kein Framework kontrolliert, welche UI-Elemente ein Agent erzeugen darf. In Myceloss Security-First-Architektur könnte der Widget-Katalog als Capability behandelt werden — ein Agent darf nur Widgets erzeugen, für die er Capability-Tokens hat.

3. **Interaktive Elemente über alle Kanäle:** OpenClaw kann Telegram-Inline-Keyboards oder Slack-Buttons nicht aus dem Agent-Layer heraus erzeugen. Myceloss ChoiceBox/Confirm-Widgets würden automatisch zu Inline-Keyboards (Telegram), Quick-Replies (WhatsApp), Buttons (Slack) oder nummerierten Optionen (CLI) — das macht keiner.

4. **Deklaratives Capability-System:** Kein Framework deklariert Channel-Fähigkeiten explizit. OpenClaw's Adapter sind implizit — der Code im Adapter entscheidet. Myceloss ChannelCapabilities-Dataclass macht Fähigkeiten sichtbar, testbar und erweiterbar.

**Zusammenfassung: Ja, das Widget-System wäre ein echtes Alleinstellungsmerkmal.** OpenClaw ist stark im Multi-Channel-Routing, aber schwach in strukturierter, interaktiver UI. Mycelos könnte sich genau hier differenzieren: nicht nur Text über viele Kanäle schicken, sondern **interaktive, strukturierte, security-kontrollierte Widgets**, die sich intelligent an jeden Kanal anpassen.

---

## 6. Build vs. Buy — Bewertung

### 6.1 Option A: Komplett selbst bauen

**Aufwand:** Mittel (Widget IR + 5 Renderer)
**Vorteile:**
- Volle Kontrolle, passt perfekt zur Mycelos-Architektur
- Keine externen Abhängigkeiten
- Python-native Integration (Rich für CLI, python-telegram-bot für TG)
- Security-Layer kann Widget-Erzeugung kontrollieren

**Nachteile:**
- Jeder neue Widget-Typ braucht Implementierung in allen Renderern
- Web-Renderer muss React-Komponenten erzeugen (Brücke Python → JS nötig)

### 6.2 Option B: Vercel Chat SDK für Web + Messaging, eigene CLI

**Aufwand:** Mittel
**Vorteile:**
- Chat SDK hat bereits Adapter für Slack, Telegram, Discord, Teams
- Next.js-Integration out of the box
- json-render Framework für Schema-basierte UI

**Nachteile:**
- JavaScript/TypeScript-Ökosystem (Mycelos ist Python)
- Abhängigkeit von Vercel-Roadmap
- CLI müsste trotzdem separat in Python gebaut werden
- Zwei Sprachen im Stack (Python + TS)

### 6.3 Option C: Hybrid — Eigene IR + Adapter-Schicht

**Empfohlen.** Kombiniert die Stärken beider Ansätze.

**Architektur:**
1. **Widget IR in Python** (Dataclasses, JSON-serialisierbar)
2. **Python-Renderer** für CLI (Rich) und Telegram/WhatsApp (Bot-APIs direkt)
3. **JSON-Export** der Widget IR für Web-Frontend
4. **Next.js Frontend** konsumiert Widget-JSON und rendert mit React-Komponenten
5. **Optional:** Vercel AI SDK / json-render für die Web-Komponenten nutzen

**Vorteile:**
- Python-Kern bleibt einheitlich
- Web-Frontend bekommt JSON und kann beliebige UI-Frameworks nutzen
- CLI-Renderer nutzt bewährte Rich-Library
- Neue Kanäle: Python-Renderer für Bots, JSON-Consumer für Web-basierte
- Passt zum Security-First-Ansatz (Widget-Katalog als Whitelist)

---

## 7. Empfehlung

### Gewählter Ansatz: Option C (Hybrid)

**Phase 1 — Foundation:**
- Widget-IR als Python-Dataclasses definieren (TextBlock, Table, ChoiceBox, StatusCard, ProgressBar, CodeBlock, Confirm, ImageBlock, Compose)
- `WidgetRenderer` Protocol definieren
- CLI-Renderer mit Rich implementieren (erster Kanal)
- JSON-Serialisierung der Widget IR

**Phase 2 — Messaging-Kanäle:**
- Telegram-Renderer (python-telegram-bot + Inline Keyboards)
- Slack-Renderer (Block Kit JSON-Generierung)
- WhatsApp-Renderer (Fallback-fokussiert, max 3 Buttons)

**Phase 3 — Web:**
- Next.js Frontend konsumiert Widget-JSON via API/WebSocket
- React-Komponenten pro Widget-Typ (shadcn/ui als Basis)
- Optional: Vercel AI SDK für Streaming-Integration
- Optional: json-render für Schema-Validierung

**Phase 4 — Erweiterung:**
- Neue Widget-Typen nach Bedarf (ChartWidget, FormWidget, TreeWidget)
- Telegram Mini Apps für komplexe Interaktionen
- Capability-basierte automatische Degradation

### Integration in Mycelos-Architektur

```
Agent (Creator/Planner/etc.)
    ↓ erzeugt Widget IR
Channel Layer (bestehend)
    ↓ ruft Renderer auf
WidgetRenderer (pro Kanal)
    ↓ liefert kanalspezifische Ausgabe
User
```

Die Widget IR wird Teil des Agent-Output-Formats. Agents importieren Widget-Klassen und bauen ihre Antworten damit. Der Channel Layer dispatched an den richtigen Renderer.

---

## 8. Quellen

**Adaptive Cards:** https://adaptivecards.io/, https://learn.microsoft.com/en-us/adaptive-cards/
**Slack Block Kit:** https://docs.slack.dev/block-kit/
**Vercel AI SDK:** https://ai-sdk.dev/, https://json-render.dev/
**Vercel Chat SDK:** https://github.com/vercel/chat
**Server-Driven UI:** https://medium.com/airbnb-engineering/ (Ghost Platform)
**Rich Library:** https://github.com/Textualize/rich
**Textual Framework:** https://textual.textualize.io/
**Telegram Bot API:** https://core.telegram.org/bots/api
**Telegram Mini Apps:** https://core.telegram.org/bots/webapps
**WhatsApp Business API:** https://developers.facebook.com/docs/whatsapp/
**React Reconciler:** https://www.npmjs.com/package/react-reconciler
**json-render:** https://github.com/vercel-labs/json-render
**OpenClaw:** https://openclaw.ai/, https://github.com/openclaw/openclaw, https://docs.openclaw.ai/
