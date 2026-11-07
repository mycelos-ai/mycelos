# SecurityProxy — Design Specification

**Date:** 2026-03-25
**Status:** REVIEWED
**Scope:** Process isolation for credentials and external network access

---

## 1. Overview

The SecurityProxy is a separate child process that owns all credentials and is
the only process allowed to make external network requests. The Gateway
delegates all outbound operations (HTTP, MCP, LLM) to the proxy via a Unix
Domain Socket. Credentials never leave the proxy process.

### Core Principle

The Gateway decides **what** is allowed (Capabilities, Policies).
The SecurityProxy decides **how** it is executed securely (Credentials,
SSRF filter, audit trail).

### What This Solves

- Master Key exists only in the proxy process — a Gateway memory dump cannot
  leak credentials
- Single choke point for all external network access — SSRF filter, rate
  limiting, and audit in one place
- LLM API keys never touch the Gateway process — injected by the proxy into
  outbound requests
- MCP subprocess credentials are injected by the proxy — Gateway never sees
  the plaintext tokens

---

## 2. Architecture

```
mycelos gateway (one command starts both)
  |
  ├── SecurityProxy (child process, forked at startup)
  |   ├── MYCELOS_MASTER_KEY (only here)
  |   ├── EncryptedCredentialProxy (decrypt on demand)
  |   ├── SSRF filter (URL validation)
  |   ├── MCP subprocess launcher (credential injection into env)
  |   ├── HTTP outbound (credential injection into headers)
  |   ├── LLM proxy (streaming pass-through, key injection)
  |   └── Audit log (every external access)
  |   Listens on: Unix Socket $TMPDIR/mycelos-security-{pid}.sock
  |
  └── Gateway (main process)
      ├── FastAPI (Chat, SSE, API endpoints)
      ├── ChatService + Orchestrator
      ├── CapabilityTokenManager + PolicyEngine
      ├── BackgroundTaskRunner + Huey
      ├── Filesystem access (Mounts, direct)
      ├── SQLite storage (direct)
      └── SecurityProxyClient (HTTP over Unix Socket)
```

### Process Lifecycle

1. `mycelos gateway` starts
2. Generates a random 32-byte **session token**
3. Forks the SecurityProxy child process, passing:
   - `MYCELOS_MASTER_KEY` (env var)
   - `MYCELOS_PROXY_TOKEN` (env var, the session token)
   - `MYCELOS_PROXY_SOCKET` (env var, socket path)
   - `MYCELOS_DB_PATH` (env var, for audit writes)
4. Gateway clears `MYCELOS_MASTER_KEY` from its own env after fork
5. Gateway waits for proxy health check (max 5s)
6. Gateway starts FastAPI, Huey, Telegram, etc.
7. On Gateway shutdown: sends SIGTERM to proxy, waits for clean exit

### Fail-Closed + Auto-Restart

- If the proxy crashes, Gateway detects it (health check fails or process dies)
- Gateway restarts the proxy automatically (max 3 attempts)
- On restart: Gateway re-issues all `/mcp/start` calls to restore MCP sessions
- During proxy downtime: all external requests return error responses
- After 3 failed restarts: Gateway logs critical error, enters degraded mode
- Degraded mode: external requests denied, user sees "External services
  unavailable" in chat, audit event `proxy.degraded_mode` logged
- Constitution Rule 3 enforced: security check failure = deny

---

## 3. Authentication

### Session Token

- Generated once per Gateway start: `secrets.token_hex(32)`
- Passed to proxy as `MYCELOS_PROXY_TOKEN` env var
- Every request to the proxy must include `Authorization: Bearer {token}`
- Proxy rejects all requests without valid token (401)
- Token rotates on every restart — no persistence needed

### Unix Socket Security

- Socket path: `$TMPDIR/mycelos-security-{pid}.sock` (uses `tempfile.mkdtemp()`
  for the directory, unique per instance)
- Permissions: directory and socket `chmod 700`/`chmod 600`
- Not reachable from network — even with firewall misconfiguration
- Agent sandboxes do not know the socket path or token

### Attack Mitigation

| Attack | Defense |
|--------|---------|
| Rogue local process contacts proxy | Needs session token (not guessable) + socket dir permissions |
| Agent sandbox tries to reach proxy | No socket path in sandbox env |
| Memory dump of Gateway | No credentials in Gateway memory |
| Network attacker | Unix socket, not TCP |
| Proxy process compromised | Master key at risk, but proxy is minimal surface |

---

## 4. SecurityProxy API

FastAPI app running on Unix Domain Socket via uvicorn `--uds`.

Every request MUST include:
- `Authorization: Bearer {session_token}`
- `X-User-Id: {user_id}` — for audit attribution
- `X-Agent-Id: {agent_id}` (optional) — for audit attribution

### 4.1 POST /http

Outbound HTTP request with SSRF protection and optional credential injection.

```json
Request:
{
  "method": "GET",
  "url": "https://api.github.com/repos/user/repo",
  "headers": {},
  "body": null,
  "timeout": 30,
  "inject_credential": "github",
  "inject_as": "bearer"
}

Response:
{
  "status": 200,
  "headers": {"content-type": "application/json"},
  "body": "{...}",
  "url": "https://api.github.com/repos/user/repo"
}
```

`inject_credential` is optional. When set, the proxy looks up the credential
and injects it as:
- `"bearer"` → `Authorization: Bearer {token}`
- `"header:{name}"` → custom header

**Note:** Query parameter injection (`query:{param}`) is NOT supported.
Credentials in query strings would appear in access logs, violating
Constitution Rule 4 (Credentials Never Visible).

`timeout` is optional (default: 30 seconds).

SSRF filter runs before every request (private IPs, metadata endpoints,
non-HTTP schemes blocked).

### 4.2 POST /mcp/start

Start an MCP server subprocess with credential injection.

```json
Request:
{
  "connector_id": "github",
  "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
  "env_vars": {
    "GITHUB_PERSONAL_ACCESS_TOKEN": "credential:github"
  },
  "transport": "stdio"
}

Response:
{
  "session_id": "mcp-github-abc123",
  "tools": [{"name": "list_issues", "description": "...", "input_schema": {...}}]
}
```

The proxy resolves `credential:github` to the actual token and injects it
into the subprocess environment. The Gateway never sees the plaintext value.

### 4.3 POST /mcp/call

Call a tool on a running MCP server.

```json
Request:
{
  "session_id": "mcp-github-abc123",
  "tool": "list_issues",
  "arguments": {"repo": "user/repo"}
}

Response:
{
  "result": [...]
}
```

If `session_id` is unknown (e.g., after proxy restart), returns:
```json
{"error": "MCP session not found", "session_id": "mcp-github-abc123", "status": 0}
```
The Gateway must handle this by re-calling `/mcp/start`.

### 4.4 POST /mcp/stop

Stop an MCP server subprocess.

```json
Request:
{
  "session_id": "mcp-github-abc123"
}

Response:
{
  "ok": true
}
```

### 4.5 POST /llm/complete

LLM completion with streaming pass-through.

```json
Request:
{
  "model": "anthropic/claude-sonnet-4-6",
  "messages": [...],
  "tools": [...],
  "stream": true,
  "purpose": "chat"
}

Non-streaming response:
{
  "choices": [...],
  "usage": {"input_tokens": 100, "output_tokens": 50},
  "cost": 0.0023
}
```

#### Streaming Behavior

When `stream: true`:
1. Proxy resolves API key for the provider from credential store
2. Proxy calls the LLM provider using litellm (which runs inside the proxy)
3. Proxy returns `Content-Type: text/event-stream`
4. Each SSE chunk is forwarded as-is to the Gateway
5. Gateway reads chunks via `httpx.Client.stream()` using `response.iter_lines()`
6. At stream end, proxy appends a final `data: [USAGE]` event with token counts
7. Proxy logs usage to `llm_usage` table

The Gateway-side `SecurityProxyClient.llm_complete()` returns an iterator
of chunks when `stream=True`, matching the existing `LiteLLMBroker` interface.

#### Why litellm runs in the proxy

litellm needs the API keys to make LLM calls. Since credentials must stay
in the proxy process, litellm must run there. The proxy is not just a
pass-through for LLM calls — it is the LLM broker.

### 4.6 GET /health

Health check endpoint. Returns 200 if the proxy is running.

```json
Response:
{
  "status": "ok",
  "uptime_seconds": 42,
  "mcp_sessions": 2
}
```

Minimal information only — no credential counts or internal state exposed.

---

## 5. SecurityProxyClient

A thin synchronous client in the Gateway that replaces direct
credential/network access. Uses `httpx.Client` (sync) with Unix socket
transport via `httpx.HTTPTransport(uds=socket_path)`.

All methods are synchronous, matching the Gateway's current sync architecture.

```python
class SecurityProxyClient:
    """Gateway-side client for the SecurityProxy.

    Synchronous — uses httpx.Client with Unix socket transport.
    """

    def __init__(self, socket_path: str, token: str):
        self._socket_path = socket_path
        self._token = token
        self._client = httpx.Client(
            transport=httpx.HTTPTransport(uds=socket_path),
            headers={"Authorization": f"Bearer {token}"},
        )

    def http_get(self, url: str, headers: dict | None = None,
                 credential: str | None = None,
                 timeout: int = 30,
                 user_id: str = "default",
                 agent_id: str | None = None) -> dict: ...

    def http_post(self, url: str, body: Any = None,
                  headers: dict | None = None,
                  credential: str | None = None,
                  timeout: int = 30,
                  user_id: str = "default",
                  agent_id: str | None = None) -> dict: ...

    def mcp_start(self, connector_id: str, command: list[str],
                  env_vars: dict, transport: str = "stdio",
                  user_id: str = "default") -> dict: ...

    def mcp_call(self, session_id: str, tool: str,
                 arguments: dict,
                 user_id: str = "default",
                 agent_id: str | None = None) -> dict: ...

    def mcp_stop(self, session_id: str) -> None: ...

    def llm_complete(self, model: str, messages: list[dict],
                     tools: list[dict] | None = None,
                     stream: bool = False,
                     user_id: str = "default",
                     agent_id: str | None = None,
                     purpose: str = "chat") -> Any:
        """LLM completion.

        When stream=False: returns dict with choices + usage.
        When stream=True: returns iterator of SSE chunk strings,
        matching LiteLLMBroker.complete_stream() interface.
        """
        ...

    def health(self) -> dict: ...
```

### Protocol Definition

A `SecurityProxyProtocol` will be added to `protocols.py` so the proxy
client can be mocked in tests:

```python
@runtime_checkable
class SecurityProxyProtocol(Protocol):
    def http_get(self, url: str, ...) -> dict: ...
    def http_post(self, url: str, ...) -> dict: ...
    def mcp_start(self, connector_id: str, ...) -> dict: ...
    def mcp_call(self, session_id: str, ...) -> dict: ...
    def mcp_stop(self, session_id: str) -> None: ...
    def llm_complete(self, model: str, ...) -> Any: ...
    def health(self) -> dict: ...
```

---

## 6. What Changes in the Gateway

### Replaced

| Current | New |
|---------|-----|
| `EncryptedCredentialProxy` in Gateway | `SecurityProxyClient` — no more direct credential access |
| `http_get()`/`http_post()` in `http_tools.py` | Delegate to `proxy_client.http_get/post()` |
| `MCPConnectorManager` managing subprocesses | Delegate start/call/stop to proxy |
| `LiteLLMBroker` (moves to proxy) | `proxy_client.llm_complete()` in Gateway |
| `MYCELOS_MASTER_KEY` in Gateway env | Only in proxy process env, cleared from Gateway after fork |
| `App.load_all_credentials_to_env()` | Removed — proxy handles credential injection |
| `App.credentials` property | Removed from Gateway — credentials only in proxy |

### Changed

| Component | Change |
|-----------|--------|
| Telegram channel | Startup calls `proxy_client.http_get()` to fetch bot info instead of direct credential access. Bot token is passed from proxy via a one-time `/credential/bootstrap` call (see Section 6.1). |
| Huey scheduled tasks | Tasks that call LLM or MCP tools go through `App.proxy_client` like everything else |

### Unchanged

| Component | Reason |
|-----------|--------|
| CapabilityTokenManager | Authorization logic stays in Gateway |
| PolicyEngine | Policy decisions stay in Gateway |
| ChatService + Orchestrator | Business logic, no direct external access |
| BackgroundTaskRunner | Task scheduling, uses proxy via App container |
| Filesystem access (Mounts) | Local, no credentials needed |
| SQLite storage | Local, no credentials needed |
| SessionStore | Local filesystem |

### 6.1 Telegram Bootstrap

Telegram needs the bot token at startup to initialize the aiogram Bot object.
This is the one case where the Gateway needs a credential value.

Solution: The proxy exposes a restricted `POST /credential/bootstrap` endpoint
that returns a credential value **once** per session. It can only be called:
- Within the first 10 seconds after proxy startup
- With a valid session token
- Each credential can only be bootstrapped once per session

After the bootstrap window closes, the endpoint returns 403.

```json
Request:
{
  "service": "telegram"
}

Response:
{
  "api_key": "bot123456:ABC..."
}
```

The Gateway uses this during startup for Telegram bot initialization only.
The credential is held in memory for the aiogram Bot object but never written
to disk or logs.

---

## 7. Audit Trail

The proxy logs every external access to the SQLite `audit_events` table.
Every audit event includes `user_id` from the request header.

| Event | Details |
|-------|---------|
| `proxy.http_request` | method, url (sanitized), credential used, user_id, agent_id, status, duration |
| `proxy.mcp_started` | connector_id, command, transport, user_id |
| `proxy.mcp_call` | session_id, tool, user_id, agent_id, duration |
| `proxy.mcp_stopped` | session_id, user_id |
| `proxy.llm_call` | model, input_tokens, output_tokens, cost, user_id, agent_id, purpose, duration |
| `proxy.auth_failed` | reason |
| `proxy.ssrf_blocked` | url, reason, user_id |
| `proxy.started` | pid, socket_path |
| `proxy.credential_bootstrap` | service, user_id |
| `proxy.degraded_mode` | restart_count |

### Schema Ownership

The proxy writes to `audit_events` and `llm_usage` tables. It does NOT
run schema migrations — the Gateway owns the schema. The proxy assumes
these tables exist. If they don't (corrupted DB), the proxy logs to stderr
as fallback.

---

## 8. Startup Sequence

```
1. mycelos gateway
2.   generate session_token = secrets.token_hex(32)
3.   socket_dir = tempfile.mkdtemp(prefix="mycelos-sec-")
4.   socket_path = socket_dir + "/proxy.sock"
5.   fork SecurityProxy child process with env:
       MYCELOS_MASTER_KEY=...
       MYCELOS_PROXY_TOKEN={session_token}
       MYCELOS_PROXY_SOCKET={socket_path}
       MYCELOS_DB_PATH={data_dir}/mycelos.db
6.   Gateway clears MYCELOS_MASTER_KEY from os.environ
7.   SecurityProxy starts:
       - chmod 700 socket_dir, chmod 600 socket_path
       - uvicorn on unix socket
       - initializes EncryptedCredentialProxy
       - starts bootstrap window (10s)
       - waits for requests
8.   Gateway polls GET /health (max 5s, 100ms intervals)
9.   Gateway calls POST /credential/bootstrap for Telegram (if configured)
10.  Gateway starts:
       - SecurityProxyClient(socket_path, session_token)
       - FastAPI on :8080
       - Huey scheduler
       - MCP connectors (via proxy)
       - Telegram channel (with bootstrapped token)
11.  Ready
```

### Shutdown Sequence

```
1. Gateway receives SIGTERM/SIGINT
2. Stop accepting new requests
3. Disconnect MCP servers (via proxy)
4. Send SIGTERM to proxy child process
5. Wait max 5s for clean proxy exit
6. Clean up socket directory
7. Exit
```

### MCP Session Recovery After Proxy Restart

```
1. Proxy crashes / is restarted
2. Gateway detects proxy is down (health check or process exit)
3. Gateway restarts proxy (new process, same socket path)
4. Gateway waits for health check
5. Gateway re-bootstraps Telegram credential (if needed)
6. Gateway re-issues /mcp/start for each active connector
7. Gateway updates session_id mappings
8. Normal operation resumes
```

---

## 9. Error Handling

### Proxy Unreachable

```python
try:
    result = proxy_client.http_get(url)
except ProxyUnavailableError:
    # Fail-closed: return error, do not attempt direct network access
    return {"error": "SecurityProxy unavailable — external access denied"}
```

### Proxy Crash + Auto-Restart

```python
# In Gateway health monitor thread:
if not proxy_process.is_alive():
    if restart_count < 3:
        restart_proxy()  # includes MCP session recovery
        restart_count += 1
    else:
        logger.critical("SecurityProxy failed 3 restarts — degraded mode")
        audit.log("proxy.degraded_mode", details={"restart_count": 3})
```

### Credential Not Found

The proxy returns a clear error if a requested credential does not exist:

```json
{"error": "Credential 'slack' not found", "status": 0}
```

The Gateway handles this gracefully (e.g., "Slack connector not configured").

### MCP Session Not Found

After proxy restart, stale session IDs return an error. The Gateway
re-starts the MCP server and retries:

```python
result = proxy_client.mcp_call(session_id, tool, args)
if result.get("error") == "MCP session not found":
    # Re-start the connector
    new_session = proxy_client.mcp_start(connector_id, command, env_vars)
    session_id = new_session["session_id"]
    result = proxy_client.mcp_call(session_id, tool, args)
```

---

## 10. Scope Boundary

### In Scope (this spec)

- SecurityProxy process with Unix Socket API
- Session token authentication
- HTTP outbound with SSRF filter and credential injection
- MCP subprocess management with credential injection
- LLM call proxying with streaming (litellm inside proxy)
- SecurityProxyClient in Gateway (synchronous, httpx.Client)
- SecurityProxyProtocol in protocols.py
- Credential bootstrap endpoint for Telegram
- Refactor Gateway to use proxy for all external access
- Audit logging in proxy (user_id + agent_id scoped)
- Auto-restart on crash with MCP session recovery
- Socket path in $TMPDIR with secure permissions

### Out of Scope (future)

- Docker-based sandbox (Phase 2.4 of core spec)
- Network namespace isolation
- Multi-Gateway setups sharing one proxy
- Credential rotation API
- Rate limiting per agent/credential (future enhancement)

---

## 11. Testing Strategy

### Unit Tests

- SecurityProxyClient: mock proxy responses, verify request format
- Session token generation and validation
- SSRF filter (already exists, moves to proxy)
- Credential bootstrap window timing

### Integration Tests

- Start proxy + Gateway, verify credential never appears in Gateway logs
- HTTP call through proxy with credential injection
- MCP start/call/stop through proxy
- LLM streaming through proxy (verify chunks arrive correctly)
- LLM non-streaming through proxy
- Proxy crash → auto-restart → MCP session recovery
- Invalid session token → 401
- Bootstrap window closes after 10s → 403

### Security Tests (tests/security/)

- Gateway process env does not contain MYCELOS_MASTER_KEY after startup
- Credential plaintext never appears in Gateway audit events
- Proxy rejects requests without valid session token
- Proxy rejects requests to private IPs (SSRF)
- Agent sandbox cannot reach proxy socket
- Audit events include user_id and agent_id
- Bootstrap endpoint refuses after window closes
- No credential values in proxy audit log (sanitized)

### Gherkin Scenario Mapping

This spec maps to security scenarios SEC01-SEC20 in `docs/scenarios/security/`.
Key mappings:
- SEC04 (Credential Proxy isolation) → proxy process separation
- SEC09 (Capability token validation) → stays in Gateway
- SEC13 (Artifact sandbox) → filesystem stays in Gateway
- SEC14 (Response sanitization) → stays in Gateway
- SEC19 (Base64 credential detection) → stays in Gateway
