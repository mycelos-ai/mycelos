# Connectors + Channels: MCP Tools + Slack/Telegram

**Ergebnis der Recherche:** Vercel Chat SDK faellt raus (TypeScript-only, falscher Stack). Stattdessen:
- **Tool-Konnektoren:** Offizielles MCP Python SDK (`pip install mcp`)
- **Channel-Konnektoren:** Python-native: `slack-bolt` (Slack) + `aiogram` (Telegram)
- Beide integrieren sich direkt in unseren FastAPI Gateway — kein Node.js noetig

---

## Architektur-Entscheidung

```
USER spricht mit Mycelos:              Mycelos nutzt externe Services:
  Terminal (Rich) ← direkt             GitHub API ← MCP Server (stdio)
  Slack Bot ← slack-bolt + FastAPI     Filesystem ← MCP Server (stdio)
  Telegram Bot ← aiogram + FastAPI     Brave Search ← MCP Server (stdio)
  Web UI ← Next.js + SSE (spaeter)    SQLite ← MCP Server (stdio)
       ↓                                    ↑
    Gateway (/api/chat)               MCP Client (Python SDK)
       ↓                                    ↑
    ChatService ────────────────→ Agent mit scoped Tools
```

**Kein Vercel Chat SDK** — das ist TypeScript-only und wuerde einen separaten Node.js-Prozess brauchen. slack-bolt und aiogram sind Python-native und mounten direkt in FastAPI.

**Kein LangChain MCP Adapter** — wir nutzen das offizielle `mcp` SDK direkt (CLAUDE.md: "own interfaces only").

---

## Teil 1: MCP Tool-Konnektoren

### Was das MCP SDK kann

```python
pip install mcp  # v1.26+

# Minimal: MCP Server starten + Tool aufrufen
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async with stdio_client(StdioServerParameters(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_TOKEN": "ghp_..."},  # via CredentialProxy!
)) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("create_issue", {...})
```

### Mycelos MCP Integration

```python
# src/mycelos/connectors/mcp_client.py

class MycelosMCPClient:
    """Wraps the official MCP SDK with Mycelos's security layer."""

    async def connect(self, connector_id: str) -> None:
        """Start MCP server, inject credentials via env."""

    async def discover_tools(self) -> list[ToolDefinition]:
        """List tools from MCP server → register as Capabilities."""

    async def call_tool(self, tool_name: str, args: dict) -> Any:
        """Call tool with Capability + Policy check."""
```

### Verfuegbare MCP-Server (sofort nutzbar)

| Server | Package | Transport |
|--------|---------|-----------|
| GitHub | `@modelcontextprotocol/server-github` | stdio (npx) |
| Filesystem | `@modelcontextprotocol/server-filesystem` | stdio |
| Brave Search | `@anthropic/mcp-server-brave-search` | stdio |
| Git | `@modelcontextprotocol/server-git` | stdio |
| SQLite | `@anthropic/mcp-server-sqlite` | stdio |
| Fetch/HTTP | `@anthropic/mcp-server-fetch` | stdio |

Alle laufen als Subprocess via `npx` — der MCP Client startet sie und kommuniziert ueber stdin/stdout.

### Security-Integration

```
Agent will "github.create_issue" aufrufen:
  1. Capability Check → hat Agent "github.create_issue"?
  2. Policy Check → "always"/"confirm"/"never"?
  3. MCP Client → startet GitHub Server mit GITHUB_TOKEN aus CredentialProxy
  4. Tool Call → JSON-RPC → MCP Server → GitHub API
  5. Response Sanitizer → prueft auf Credential-Leaks
  6. Agent bekommt sanitized Result
```

Credentials gehen NIE ans LLM — sie werden als ENV-Variablen an den MCP-Server-Prozess uebergeben, und der CredentialProxy entschluesselt sie nur fuer die Dauer des Calls.

---

## Teil 2: Slack Channel

### slack-bolt in FastAPI

```python
# src/mycelos/channels/slack.py
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

slack_app = AsyncApp(token="xoxb-...", signing_secret="...")

@slack_app.event("app_mention")
async def handle_mention(event, say, client):
    # Route to Mycelos ChatService
    session_id = get_or_create_session(event["user"])
    events = chat_service.handle_message(event["text"], session_id)

    # Stream response
    streamer = client.chat_stream(
        channel=event["channel"],
        thread_ts=event["ts"],
    )
    for e in events:
        if e.type == "text":
            streamer.append(markdown_text=e.data["content"])
    streamer.stop()

# Mount in FastAPI:
handler = AsyncSlackRequestHandler(slack_app)

@fastapi_app.post("/slack/events")
async def slack_events(req: Request):
    return await handler.handle(req)
```

### Setup: `/connector setup slack`

```
User: /connector setup slack
System: "Slack Bot Token (xoxb-...): ********"
System: "Slack Signing Secret: ********"
System: "Slack Bot konfiguriert! Setze die Event-URL auf:
         https://your-domain/slack/events"
```

---

## Teil 3: Telegram Channel

### aiogram in FastAPI

```python
# src/mycelos/channels/telegram.py
from aiogram import Bot, Dispatcher, types

bot = Bot(token="BOT_TOKEN")
dp = Dispatcher()

@dp.message()
async def handle_message(message: types.Message):
    session_id = get_or_create_session(str(message.from_user.id))
    events = chat_service.handle_message(message.text, session_id)

    for e in events:
        if e.type == "text":
            await message.answer(e.data["content"], parse_mode="Markdown")

# Webhook mode for FastAPI:
@fastapi_app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    update = types.Update.model_validate(await request.json())
    await dp.feed_update(bot, update)
```

---

## Dependencies

```toml
# pyproject.toml — neue Dependencies
"mcp>=1.26",
"slack-bolt[async]>=1.20",
"aiogram>=3.26",
```

---

## Implementierungs-Plan

### Phase 1: MCP Client (Kern-Infrastruktur)

| Task | Was |
|------|-----|
| 1 | `pip install mcp` + MycelosMCPClient Wrapper |
| 2 | Credential-Injection via CredentialProxy → MCP Server env |
| 3 | Tool-Discovery → auto-register als Capabilities |
| 4 | Smart MCP: nur scoped Tools pro Agent im Prompt |
| 5 | `/connector add --mcp` CLI |
| 6 | Tests mit Mock MCP Server |

### Phase 2: Erster MCP-Connector (GitHub)

| Task | Was |
|------|-----|
| 7 | GitHub MCP Server registrieren + testen |
| 8 | E2E: Agent erstellt GitHub Issue ueber MCP |
| 9 | Filesystem MCP Server (fuer scoped Mounts) |

### Phase 3: Slack Channel

| Task | Was |
|------|-----|
| 10 | `src/mycelos/channels/slack.py` + FastAPI Mount |
| 11 | Streaming via `chat_stream()` |
| 12 | `/connector setup slack` mit Credential-Speicherung |
| 13 | E2E: User mentioned Bot → Mycelos antwortet |

### Phase 4: Telegram Channel

| Task | Was |
|------|-----|
| 14 | `src/mycelos/channels/telegram.py` + Webhook |
| 15 | `/connector setup telegram` |
| 16 | E2E: User schreibt Bot → Mycelos antwortet |

### Phase 5: Builtin Migration

| Task | Was |
|------|-----|
| 17 | DuckDuckGo → MCP Server wrappen oder offiziellen nutzen |
| 18 | HTTP → `@anthropic/mcp-server-fetch` |
| 19 | Google → MCP Server (wenn verfuegbar) oder behalten |

---

## Reihenfolge

```
Phase 1 (MCP Client)       ← Grundlage fuer alles
    ↓
Phase 2 (GitHub MCP)       ← Erster externer Connector
    ↓
Phase 3 (Slack) + Phase 4 (Telegram)  ← parallel
    ↓
Phase 5 (Migration)        ← Builtin-Connectors ersetzen
```

## Was wir NICHT machen

- Vercel Chat SDK (TypeScript-only)
- LangChain MCP Adapter (Framework-Abhaengigkeit)
- Web UI mit Next.js (spaeter, separater Plan)
- WhatsApp/Discord/Teams (nach Slack+Telegram)
