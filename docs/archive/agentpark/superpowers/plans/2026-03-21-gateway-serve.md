# Gateway Phase A: `mycelos serve` — HTTP API mit SSE Streaming

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `mycelos serve` startet einen FastAPI-Server auf `localhost:9100`. Channels (Terminal, Slack, Telegram, Web UI) sprechen ueber HTTP mit dem Gateway. SSE Streaming fuer Live-Responses. `mycelos chat` erkennt ob Gateway laeuft und routet dorthin.

---

## Architektur

```
mycelos serve (FastAPI, uvicorn, localhost:9100)
│
├── POST /api/chat          ← Message rein, SSE Stream raus
│     Request:  { session_id?, user_id, message, channel }
│     Response: SSE stream (text-delta, agent-info, step-progress, done)
│
├── POST /api/chat/confirm  ← Plan bestaetigen (Workflow ausfuehren)
│     Request:  { session_id, task_id }
│     Response: SSE stream (step-progress, result, done)
│
├── GET  /api/health        ← Status Check
│     Response: { status, uptime, active_sessions, generation_id }
│
├── GET  /api/config        ← Aktuelle Config anzeigen
│     Response: StateManager.snapshot()
│
├── GET  /api/sessions      ← Aktive Sessions
│     Response: [{ session_id, user_id, message_count, last_active }]
│
└── Internal:
      ├── App (Service Container)
      ├── Credential Loading (all providers → env)
      ├── Session Management (create/resume/list)
      └── Audit Logging
```

## SSE Event Format

Angelehnt an Vercel AI SDK Data Stream Protocol, aber vereinfacht fuer Phase A:

```
event: agent
data: {"agent": "Planner-Agent"}

event: text-delta
data: {"delta": "Ich habe einen Plan erstellt..."}

event: text-delta
data: {"delta": " Soll ich das ausfuehren?"}

event: plan
data: {"task_id": "abc123", "plan": {...}}

event: step-progress
data: {"step_id": "search", "status": "done"}

event: done
data: {"tokens": 150, "model": "claude-sonnet-4-6", "cost": 0.0045}
```

Fuer Phase B (Web UI) erweitern wir das auf das volle Vercel Data Stream Protocol.

---

## Chat-Logik: Extraktion aus chat_cmd.py

Die gesamte Message-Verarbeitung aus `chat_cmd.py` (Zeilen 450-580) wird in einen `ChatService` extrahiert. Dieser ist channel-agnostisch — sowohl `chat_cmd` (Terminal) als auch der Gateway nutzen ihn.

```python
# src/mycelos/chat/service.py

class ChatService:
    """Channel-agnostic chat message handler.

    Handles the full message lifecycle:
    1. Session management (create/resume)
    2. Intent classification (Orchestrator)
    3. Plan confirmation detection
    4. Workflow execution
    5. LLM completion
    6. Response generation
    """

    def __init__(self, app: App):
        self.app = app

    async def handle_message(
        self,
        message: str,
        session_id: str | None = None,
        user_id: str = "default",
    ) -> AsyncIterator[ChatEvent]:
        """Process a message and yield response events.

        Yields ChatEvent objects that channels translate to their format:
        - Terminal: Rich console output
        - Gateway SSE: JSON events
        - Slack: chat_stream() calls
        """
        # ... extracted logic from chat_cmd.py
```

### ChatEvent Types

```python
@dataclass
class ChatEvent:
    type: str       # "agent", "text-delta", "plan", "step-progress",
                    # "system-response", "error", "done"
    data: dict
```

---

## Was sich fuer `mycelos chat` aendert

**Heute:** `chat_cmd.py` enthaelt die gesamte Chat-Logik (500+ Zeilen).

**Nachher:** `chat_cmd.py` wird ein duenner Terminal-Client:

```python
# Option A: Gateway laeuft → HTTP Client
if _is_gateway_running():
    _chat_via_gateway(data_dir, debug)

# Option B: Kein Gateway → direkt (wie bisher, aber via ChatService)
else:
    _chat_direct(data_dir, debug)
```

Die direkte Variante bleibt fuer Entwicklung und wenn kein Server laeuft.

---

## Gateway Discovery

`mycelos chat` prueft ob der Gateway laeuft:

```python
def _is_gateway_running(port: int = 9100) -> bool:
    """Check if the gateway is running by hitting /api/health."""
    try:
        import httpx
        resp = httpx.get(f"http://localhost:{port}/api/health", timeout=1)
        return resp.status_code == 200
    except Exception:
        return False
```

Alternativ: PID-File in `~/.mycelos/gateway.pid`.

---

## Tasks

### Task 1: Dependencies + ChatEvent Model

**Files:**
- Modify: `pyproject.toml` (add fastapi, uvicorn)
- Create: `src/mycelos/chat/__init__.py`
- Create: `src/mycelos/chat/events.py`
- Create: `tests/test_chat_events.py`

**Was:**
- `pip install fastapi uvicorn[standard]`
- `ChatEvent` dataclass mit allen Event-Types
- Serialisierung zu SSE-Format (`event: {type}\ndata: {json}\n\n`)
- Tests fuer Event-Erstellung und SSE-Serialisierung

```python
# src/mycelos/chat/events.py
from dataclasses import dataclass, field
from typing import Any
import json

@dataclass
class ChatEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        """Serialize to Server-Sent Events format."""
        return f"event: {self.type}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"

# Factory functions
def agent_event(agent_name: str) -> ChatEvent:
    return ChatEvent(type="agent", data={"agent": agent_name})

def text_delta_event(delta: str) -> ChatEvent:
    return ChatEvent(type="text-delta", data={"delta": delta})

def plan_event(task_id: str, plan: dict) -> ChatEvent:
    return ChatEvent(type="plan", data={"task_id": task_id, "plan": plan})

def step_progress_event(step_id: str, status: str) -> ChatEvent:
    return ChatEvent(type="step-progress", data={"step_id": step_id, "status": status})

def system_response_event(content: str) -> ChatEvent:
    return ChatEvent(type="system-response", data={"content": content})

def error_event(message: str) -> ChatEvent:
    return ChatEvent(type="error", data={"message": message})

def done_event(tokens: int = 0, model: str = "", cost: float = 0.0) -> ChatEvent:
    return ChatEvent(type="done", data={"tokens": tokens, "model": model, "cost": cost})
```

---

### Task 2: ChatService — Channel-agnostic Message Handler

**Files:**
- Create: `src/mycelos/chat/service.py`
- Create: `tests/test_chat_service.py`

**Was:**

Extraktion der Chat-Logik aus `chat_cmd.py` in einen wiederverwendbaren Service:

```python
class ChatService:
    def __init__(self, app: App):
        self.app = app
        self._pending_task_id: str | None = None
        self._pending_plan: dict | None = None
        self._pending_workflow_name: str | None = None

    def handle_message(
        self, message: str, session_id: str, user_id: str = "default"
    ) -> list[ChatEvent]:
        """Process a message and return response events.

        Handles: confirmation detection, intent routing, system commands,
        plan display, workflow execution, LLM completion.
        """

    def create_session(self, user_id: str = "default") -> str:
        """Create or resume a session."""

    def get_greeting(self, user_name: str | None) -> list[ChatEvent]:
        """Generate the initial greeting."""
```

Tests mit MockLLMBroker:
- Message → Orchestrator → LLM Response → Events
- System Command → Direct Response
- Plan Confirmation → Workflow Execution → Step Events
- Error Handling → Error Event

---

### Task 3: Gateway Server — FastAPI App

**Files:**
- Create: `src/mycelos/gateway/__init__.py`
- Create: `src/mycelos/gateway/server.py`
- Create: `src/mycelos/gateway/routes.py`
- Create: `tests/test_gateway.py`

**Was:**

```python
# src/mycelos/gateway/server.py
from fastapi import FastAPI
from mycelos.app import App
from mycelos.gateway.routes import setup_routes

def create_app(data_dir: Path) -> FastAPI:
    """Create the FastAPI gateway application."""
    api = FastAPI(title="Mycelos Gateway", version="0.1.0")

    mycelos_app = App(data_dir)
    mycelos_app.load_all_credentials_to_env()

    # Store app reference for routes
    api.state.mycelos = mycelos_app

    setup_routes(api)
    return api
```

```python
# src/mycelos/gateway/routes.py
from fastapi import Request
from starlette.responses import StreamingResponse

async def chat_endpoint(request: Request):
    """POST /api/chat — process message, stream SSE response."""
    body = await request.json()
    mycelos = request.app.state.mycelos
    service = ChatService(mycelos)

    events = service.handle_message(
        message=body["message"],
        session_id=body.get("session_id"),
        user_id=body.get("user_id", "default"),
    )

    async def event_stream():
        for event in events:
            yield event.to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

async def health_endpoint(request: Request):
    """GET /api/health"""
    mycelos = request.app.state.mycelos
    return {
        "status": "ok",
        "generation_id": mycelos.config.get_active_generation_id(),
    }

async def config_endpoint(request: Request):
    """GET /api/config"""
    mycelos = request.app.state.mycelos
    return mycelos.state_manager.snapshot()
```

Tests mit `httpx.AsyncClient` (FastAPI TestClient):
- POST /api/chat → SSE stream mit Events
- GET /api/health → 200 + generation_id
- GET /api/config → Snapshot JSON
- POST /api/chat mit session_id → Session fortgesetzt
- Error Handling → 400/500 mit error event

---

### Task 4: `mycelos serve` CLI Command

**Files:**
- Create: `src/mycelos/cli/serve_cmd.py`
- Modify: `src/mycelos/cli/main.py` (register serve command)
- Create: `tests/test_serve_cmd.py`

**Was:**

```python
@click.command()
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
@click.option("--port", type=int, default=9100)
@click.option("--host", type=str, default="127.0.0.1")
@click.option("--daemon", is_flag=True, help="Run in background")
@click.option("--stop", is_flag=True, help="Stop running gateway")
@click.option("--status", is_flag=True, help="Check gateway status")
def serve_cmd(data_dir, port, host, daemon, stop, status):
    """Start the Mycelos Gateway (HTTP API)."""
    if status:
        _show_status(port)
        return
    if stop:
        _stop_daemon(data_dir)
        return

    # Foreground mode
    import uvicorn
    from mycelos.gateway.server import create_app

    app = create_app(data_dir)
    console.print(f"[green]Mycelos Gateway starting on {host}:{port}[/green]")
    uvicorn.run(app, host=host, port=port)
```

Tests:
- `serve --status` wenn nicht laeuft → "not running"
- `serve --help` zeigt Optionen

---

### Task 5: `mycelos chat` Gateway-Modus

**Files:**
- Modify: `src/mycelos/cli/chat_cmd.py`
- Create: `tests/test_chat_gateway_mode.py`

**Was:**

`chat_cmd` prueft ob Gateway laeuft und routet entsprechend:

```python
def chat_cmd(data_dir, debug, continue_session):
    # ... master key loading ...

    if _is_gateway_running():
        console.print("[dim]Gateway detected — using HTTP mode.[/dim]")
        _chat_via_gateway(data_dir, debug, continue_session)
    else:
        _chat_direct(data_dir, debug, continue_session)

def _chat_via_gateway(data_dir, debug, continue_session):
    """Chat via Gateway HTTP API — thin SSE client."""
    import httpx

    session_id = None  # ... session logic ...

    while True:
        user_input = console.input("[bold cyan]You>[/bold cyan] ").strip()
        if user_input in {"exit", "quit", "q"}:
            break

        with httpx.stream("POST", "http://localhost:9100/api/chat",
                          json={"message": user_input, "session_id": session_id}) as resp:
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    _render_event(event_type, data)

def _render_event(event_type, data):
    """Render a gateway SSE event to the terminal."""
    if event_type == "agent":
        console.print(f"\n[bold magenta]{data['agent']}>[/bold magenta]")
    elif event_type == "text-delta":
        console.print(data["delta"], end="")
    elif event_type == "done":
        console.print(f"\n[dim]({data['tokens']} tokens | {data['model']})[/dim]\n")
    # ... etc
```

`_chat_direct` wird die bestehende Logik (mit ChatService statt inline-Code).

Tests:
- Gateway running → HTTP mode used
- Gateway not running → direct mode used
- SSE event rendering

---

### Task 6: Integration Tests

**Files:**
- Create: `tests/test_gateway_integration.py`

**Was:**
- Starte Gateway (in-process via TestClient)
- Sende Chat-Message via HTTP
- Empfange SSE Events
- Verifiziere: Session erstellt, Tokens gezaehlt, Audit geloggt
- Health Endpoint liefert korrekte Generation
- Config Endpoint liefert vollen Snapshot

---

## Reihenfolge

```
Task 1 (Events + Dependencies)
    ↓
Task 2 (ChatService)
    ↓
Task 3 (Gateway Server + Routes) + Task 4 (serve CLI) — parallel
    ↓
Task 5 (chat Gateway-Modus)
    ↓
Task 6 (Integration Tests)
```

## Dependencies (pyproject.toml)

```toml
dependencies = [
    # ... existing ...
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
]
```

## Was wir NICHT machen in Phase A

- Vercel AI SDK Data Stream Protocol (Phase B — Web UI)
- Slack/Telegram Integration (Phase B — Channels)
- Daemon-Modus mit PID-File (spaeter — erstmal Foreground)
- WebSocket Support (SSE reicht fuer Phase A)
- Authentication/API Keys (localhost-only, spaeter)
- Rate Limiting (spaeter)
- CORS (localhost-only)
