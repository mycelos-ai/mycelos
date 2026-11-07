# MCP Connector Architecture — Von Hardcoded zu Universal Protocol

**Status:** Specification
**Date:** 2026-03-22

---

## Aktueller Stand

Unsere Connectors sind hardcoded Python-Funktionen:

```python
# Jeder Connector = eine Python-Funktion
search_web()     → DuckDuckGo via ddgs
http_get()       → httpx
gmail_search()   → gog CLI subprocess
```

Neuer Connector = neue Python-Datei + neue Registrierung. Kein Standard-Protokoll.

## Ziel

MCP (Model Context Protocol) als universelles Connector-Protokoll. Jeder Connector ist ein MCP-Server der Tools + Schemas mitbringt. Mycelos braucht nur einen MCP-Client.

```
Mycelos (MCP Client)
    ↓ JSON-RPC over stdio/HTTP/SSE
MCP Server "brave-search"     ← npm install @anthropic/mcp-brave
MCP Server "github"           ← npm install @anthropic/mcp-github
MCP Server "slack"            ← npm install @anthropic/mcp-slack
MCP Server "filesystem"       ← eigener, scoped per Mount
MCP Server "custom-api"       ← User-definiert
```

---

## Was ist MCP?

**Model Context Protocol** — von Anthropic entwickeltes offenes Protokoll:
- Standardisierte Tool-Discovery (JSON Schema)
- Drei Transports: **stdio** (Subprocess), **HTTP**, **SSE** (Server-Sent Events)
- Server exposen `tools` mit `name`, `description`, `inputSchema`
- Client ruft Tools auf, Server fuehrt aus
- Ressourcen + Prompts als zusaetzliche Primitives

### Python MCP SDK

Es gibt ein offizielles Python MCP SDK: `mcp` (PyPI). DeerFlow nutzt `langchain-mcp-adapters`, aber wir wollen **kein LangChain** (steht in CLAUDE.md).

Stattdessen: Eigener leichtgewichtiger MCP-Client oder das offizielle `mcp` SDK direkt.

```python
# Offizielles SDK:
pip install mcp

# Oder minimaler Client:
# JSON-RPC ueber stdio — wir haben schon IPC in execution/ipc.py!
```

---

## Architektur

### Drei Ebenen

```
1. CONNECTOR REGISTRY (DB, Teil des NixOS State)
   "Welche MCP-Server sind konfiguriert?"
   → connectors Tabelle: id, name, type, transport, command/url, status

2. MCP CLIENT (Runtime)
   "Starte Server, entdecke Tools, fuehre aus"
   → MCPClient: connect(), list_tools(), call_tool()

3. TOOL BRIDGE (Security Layer)
   "MCP-Tools → Mycelos-Tools mit Capability Checks"
   → Jedes MCP-Tool wird als Mycelos-Capability registriert
   → Policy Engine entscheidet ob Agent es nutzen darf
   → Credential Proxy injiziert Auth-Header
```

### Flow: MCP-Tool aufrufen

```
Agent will "github.list_repos" aufrufen
    ↓
1. Capability Check: Hat Agent die Capability "github.list_repos"?
2. Policy Check: Darf Agent diese Resource nutzen?
3. MCP Client: Startet GitHub MCP Server (falls nicht laufend)
4. Tool Call: JSON-RPC → MCP Server → GitHub API → Response
5. Response Sanitizer: Prueft auf Credential-Leaks
6. Agent bekommt sanitized Result
```

### Transport-Optionen

| Transport | Wann | Beispiel |
|-----------|------|---------|
| **stdio** | Lokale Tools, Subprocess | Filesystem, Shell, lokale APIs |
| **HTTP** | Remote APIs, Cloud Services | GitHub, Slack, Notion |
| **SSE** | Streaming-Responses | LLM-Dienste, Live-Daten |

---

## Smart MCP: Scoped Tool Injection

### Das Problem

Ein MCP-Server wie GitHub hat 15+ Tools. Wenn alle Tools in jeden LLM-Prompt
kommen, passiert Folgendes:
- **Prompt-Bloat**: 15 Tool-Definitionen = ~2000 Tokens nur fuer Schemas
- **Confusion**: LLM waehlt falsche Tools weil zu viele zur Auswahl
- **Security**: Agent hat Zugriff auf Tools die er nicht braucht
- **Kosten**: Mehr Tokens = mehr Kosten pro Call

### Die Loesung: Capability-basierte Tool-Filterung

Nur die Tools die der Agent/Workflow-Step tatsaechlich braucht werden
in den LLM-Prompt injiziert. Das ist **kein neues Feature** — es nutzt
unser bestehendes Capability-System:

```
LLM-Call fuer Agent "news-agent":
  1. Agent hat Capabilities: [search.web, search.news]
  2. Tool Registry hat 50+ Tools (alle MCP-Server)
  3. Filter: nur Tools deren required_capability in Agent-Capabilities ist
  4. LLM sieht: 2 Tools (search_web, search_news)
  → Kein GitHub, kein Slack, kein Filesystem im Prompt
```

### Implementation

```python
def get_tools_for_agent(agent_id: str, app: App) -> list[dict]:
    """Get ONLY the tools this agent is allowed to use.

    Filters the full tool registry by the agent's capabilities,
    ensuring minimal prompt size and least-privilege access.
    """
    agent = app.agent_registry.get(agent_id)
    if not agent:
        return []

    allowed_caps = set(agent.get("capabilities", []))
    all_tools = app.tool_registry.list_tools()

    return [
        tool.to_openai_schema()
        for tool in all_tools
        if tool.required_capability in allowed_caps
    ]
```

### Pro Workflow-Step

Bei Multi-Agent Workflows hat jeder Step seinen eigenen Tool-Scope:

```yaml
workflow: invoice-scanner
steps:
  - id: read-emails
    agent: email-reader
    # LLM sieht: google.gmail.search (1 Tool)

  - id: fetch-pdf
    agent: http-fetcher
    # LLM sieht: http.get (1 Tool)

  - id: extract-data
    agent: pdf-extractor
    # LLM sieht: filesystem.read (1 Tool)

  - id: write-csv
    agent: file-writer
    # LLM sieht: filesystem.write (1 Tool)
```

Jeder Step hat **minimale Tool-Exposition**. Der Email-Reader sieht keine
Filesystem-Tools. Der File-Writer sieht keine Gmail-Tools.

### Vorteile

| Aspekt | Ohne Scoping | Mit Scoping |
|--------|-------------|-------------|
| Tools im Prompt | 50+ | 1-5 pro Agent |
| Tokens fuer Schemas | ~5000 | ~200-500 |
| Falsche Tool-Wahl | Haeufig | Selten |
| Security | Agent sieht alles | Least Privilege |
| Kosten | Hoch (Token-Overhead) | Minimal |

### Dynamic Tool-Discovery bei neuen MCP-Servern

Wenn ein neuer MCP-Server hinzugefuegt wird:
1. Auto-Discovery: Alle Tools werden entdeckt
2. **KEIN Agent bekommt automatisch Zugriff**
3. User muss explizit freigeben: `/agent news-agent grant github.list_repos`
4. Oder Workflow-Definition weist Tools zu
5. Erst dann erscheinen die Tools im Agent-Prompt

Das ist "Opt-In" statt "Opt-Out" — sicherer und sauberer.

---

## Connector-Konfiguration

### In der DB (connectors Tabelle, erweitert)

```sql
-- Erweiterung der bestehenden connectors Tabelle:
ALTER TABLE connectors ADD COLUMN transport TEXT;      -- stdio, http, sse
ALTER TABLE connectors ADD COLUMN command TEXT;         -- fuer stdio: "npx @anthropic/mcp-github"
ALTER TABLE connectors ADD COLUMN url TEXT;             -- fuer http/sse: "https://api.example.com/mcp"
ALTER TABLE connectors ADD COLUMN env_vars TEXT;        -- JSON: {"GITHUB_TOKEN": "credential:github"}
ALTER TABLE connectors ADD COLUMN auto_start BOOLEAN DEFAULT 1;
```

### Im NixOS State Snapshot

```python
"connectors": {
    "github": {
        "name": "GitHub",
        "connector_type": "mcp",
        "transport": "stdio",
        "command": "npx @anthropic/mcp-github",
        "env_vars": {"GITHUB_TOKEN": "credential:github"},
        "status": "active",
        "capabilities": ["github.list_repos", "github.create_issue", ...]
    },
    "brave-search": {
        "name": "Brave Search",
        "connector_type": "mcp",
        "transport": "stdio",
        "command": "npx @anthropic/mcp-brave-search",
        "env_vars": {"BRAVE_API_KEY": "credential:brave"},
        "status": "active",
        "capabilities": ["brave_search"]
    },
    "web-search-duckduckgo": {
        "name": "DuckDuckGo",
        "connector_type": "builtin",    ← Legacy, kein MCP
        "status": "active",
        "capabilities": ["search.web", "search.news"]
    }
}
```

---

## Migration: Builtin → MCP

### Phase 1: MCP-Client + Parallelbetrieb

Behalte die Builtin-Connectors (DuckDuckGo, HTTP, Google via gog).
Fuege MCP-Support hinzu — neue Connectors koennen als MCP-Server registriert werden.
Beide Typen koexistieren.

### Phase 2: Builtin-Connectors als MCP-Server wrappen

Wrapp die bestehenden Python-Funktionen als MCP-Server:
```python
# src/mycelos/connectors/mcp_servers/search_server.py
# Startet als stdio MCP-Server, exposed search_web() + search_news()
```

### Phase 3: Externe MCP-Server bevorzugen

Wenn ein offizieller MCP-Server fuer einen Service existiert (z.B. @anthropic/mcp-github), diesen bevorzugen statt einer eigenen Implementierung.

---

## MCP-Client Implementation

### Minimaler Client (kein LangChain)

```python
# src/mycelos/connectors/mcp_client.py

class MCPClient:
    """Minimal MCP client — connects to MCP servers via stdio/HTTP."""

    def __init__(self, connector_config: dict):
        self.transport = connector_config["transport"]
        self.command = connector_config.get("command")
        self.url = connector_config.get("url")
        self._process: subprocess.Popen | None = None

    async def connect(self) -> None:
        """Start the MCP server process (stdio) or connect (HTTP)."""
        if self.transport == "stdio":
            self._process = subprocess.Popen(
                self.command.split(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._build_env(),
            )
            # Initialize protocol
            await self._send_initialize()

    async def list_tools(self) -> list[dict]:
        """Discover available tools from the MCP server."""
        response = await self._send_request("tools/list")
        return response.get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """Call a tool on the MCP server."""
        response = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        return response

    def _build_env(self) -> dict:
        """Build environment with injected credentials."""
        env = os.environ.copy()
        for key, value in self.env_vars.items():
            if value.startswith("credential:"):
                # Resolve from Credential Proxy
                service = value[11:]
                cred = credential_proxy.get_credential(service)
                if cred and "api_key" in cred:
                    env[key] = cred["api_key"]
            else:
                env[key] = value
        return env
```

### Tool-Discovery → Capability Registration

Wenn ein MCP-Server connected wird, werden seine Tools automatisch als Capabilities registriert:

```python
async def register_mcp_connector(app, connector_id):
    """Connect to MCP server, discover tools, register as capabilities."""
    config = app.connector_registry.get(connector_id)
    client = MCPClient(config)
    await client.connect()

    tools = await client.list_tools()
    capabilities = []
    for tool in tools:
        cap_name = f"{connector_id}.{tool['name']}"
        capabilities.append(cap_name)
        # Register tool definition for the Chat Agent
        app.tool_registry.register(ToolDefinition(
            name=cap_name,
            description=tool.get("description", ""),
            handler=lambda args, t=tool["name"]: client.call_tool(t, args),
            required_capability=cap_name,
        ))

    # Update connector capabilities in DB
    app.connector_registry.set_capabilities(connector_id, capabilities)
```

---

## Beliebte MCP-Server (Oekosystem)

| Server | Transport | Was es tut |
|--------|-----------|-----------|
| @anthropic/mcp-brave-search | stdio | Web-Suche via Brave |
| @anthropic/mcp-github | stdio | Repos, Issues, PRs |
| @anthropic/mcp-slack | stdio | Messages, Channels |
| @anthropic/mcp-google-drive | stdio | Drive Files |
| @anthropic/mcp-google-maps | stdio | Geocoding, Directions |
| @anthropic/mcp-filesystem | stdio | File Read/Write (scoped!) |
| @anthropic/mcp-memory | stdio | Persistent Memory |
| @anthropic/mcp-puppeteer | stdio | Browser Automation |
| @anthropic/mcp-postgres | stdio | SQL Queries |
| @anthropic/mcp-sqlite | stdio | SQLite Access |
| Community: mcp-notion | stdio | Notion Pages, Databases |
| Community: mcp-jira | stdio | Jira Issues |
| Community: mcp-linear | stdio | Linear Issues |

---

## Connector Setup UX

### Via Chat (LLM schlaegt vor, Slash-Command fuehrt aus)

```
User: "Ich will GitHub anbinden"
Mycelos: "Dafuer brauche ich einen GitHub Token. Richte es ein mit:
         /connector add github --mcp @anthropic/mcp-github"

User: /connector add github --mcp @anthropic/mcp-github
System: "GitHub Personal Access Token eingeben: ********"
System: "MCP Server wird getestet..."
System: "GitHub verbunden! 15 Tools verfuegbar:
         github.list_repos, github.create_issue, github.search_code, ..."
```

### Via CLI

```bash
mycelos connector add github \
    --mcp "@anthropic/mcp-github" \
    --env "GITHUB_TOKEN=credential:github"

mycelos connector add notion \
    --mcp "npx @anthropic/mcp-notion" \
    --env "NOTION_TOKEN=credential:notion"

mycelos connector add custom-api \
    --mcp-http "https://api.example.com/mcp" \
    --env "API_KEY=credential:custom"

mycelos connector list   # Zeigt alle inkl. MCP-Tools
mycelos connector test github   # Testet Verbindung
```

---

## Vergleich: OpenClaw vs Mycelos

| Aspekt | OpenClaw | Mycelos |
|--------|----------|--------|
| Connector-Protokoll | Eigenes Plugin SDK (TypeScript) | MCP (offener Standard) |
| Tool-Discovery | Manuell per Plugin-Code | Automatisch via MCP Schema |
| Credential-Handling | Plugin hat Zugriff | Credential Proxy, Agent sieht nichts |
| Erweiterbarkeit | npm install + Plugin SDK | npm install MCP-Server (oder eigener) |
| Security | Import-Boundaries | Capability Tokens + Policy Engine |
| Cross-Language | TypeScript only | MCP ist language-agnostic |

---

## Implementierungs-Phasen

### Phase 1: MCP-Client Kern (4-5 Tasks)
1. MCPClient (stdio Transport)
2. Tool-Discovery + Capability Registration
3. Security Integration (Credential Proxy Env-Injection)
4. `mycelos connector add --mcp` CLI
5. Tests mit Mock MCP-Server

### Phase 2: HTTP/SSE Transport + OAuth (3 Tasks)
6. HTTP Transport
7. SSE Transport
8. OAuth Token Management (wie DeerFlow)

### Phase 3: Builtin-Migration (2 Tasks)
9. Bestehende Connectors als MCP-Server wrappen
10. DuckDuckGo, HTTP, Google via MCP statt hardcoded

### Phase 4: Oekosystem (ongoing)
- Beliebte MCP-Server dokumentieren
- One-Click Setup fuer die haeufigsten (GitHub, Slack, Notion)
- Custom MCP-Server Template
