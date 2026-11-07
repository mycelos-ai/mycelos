# Dashboard Monitoring Widgets + Connector Fixes

**Date:** 2026-03-31
**Status:** Approved
**Scope:** Backend API + Frontend Dashboard + Connector bugfixes

## Overview

Three changes in one spec:

1. **Dashboard Monitoring Widgets** — four new widgets on the existing dashboard page
2. **Connector Add 422 Fix** — Pydantic models moved to module level (already implemented)
3. **MCP Registry Search Fix** — frontend parses actual registry API response format (already implemented)

---

## 1. Dashboard Monitoring Widgets

### New API Endpoint

**`GET /api/audit`**

Query parameters:
- `limit` (int, default 10, max 100) — number of events to return
- `event_type` (str, optional) — filter by event type prefix (e.g. `connector` matches `connector.added`, `connector.removed`)

Response: JSON array of audit event objects, ordered by `created_at DESC`:

```json
[
  {
    "id": 1,
    "event_type": "connector.added",
    "agent_id": null,
    "task_id": null,
    "user_id": "default",
    "details": {"connector": "playwright", "command": "npx -y @playwright/mcp-server"},
    "generation_id": null,
    "created_at": "2026-03-31T10:15:00.000Z"
  }
]
```

The `details` field is stored as JSON text in SQLite — the endpoint must parse it to a dict before returning.

Implementation: Add to `setup_routes()` in `src/mycelos/gateway/routes.py`. Query uses the existing `(event_type, created_at)` index.

### Dashboard Widgets

All four widgets are added to `src/mycelos/frontend/pages/dashboard.html`, below the existing "Agent Status" section.

#### Layout

```
[Existing: Cost Overview          ]
[Existing: Token Usage by Model   ]
[Existing: Sessions | Health      ]
[Existing: Agent Status grid      ]
[NEW: Recent Activity | Connector Health ]
[NEW: Workflows      | Schedules        ]
```

Two new 2-column rows using `grid grid-cols-1 lg:grid-cols-2 gap-6`.

#### Widget 1: Recent Activity

- Fetches `GET /api/audit?limit=8`
- Vertical timeline list (no pagination)
- Each row: icon + event description + relative timestamp
- Icon mapping by event_type prefix:
  - `connector.added` → `add_circle`
  - `connector.removed` → `remove_circle`
  - `config.rollback` → `history`
  - `credential.stored` → `key`
  - `credential.deleted` → `key_off`
  - `gateway.reloaded` → `refresh`
  - `task.*` → `task_alt`
  - `workflow.*` → `account_tree`
  - default → `info`
- Event description: human-readable text derived from `event_type` + `details` (e.g. "Connector added: playwright")
- Empty state: "No recent activity"

#### Widget 2: Connector Health

- Fetches `GET /api/connectors` (existing endpoint)
- Compact grid of connector status indicators
- Each entry: name + status badge (active/inactive) + tool count
- Reuses existing `statusClass()` helper
- Empty state: "No connectors configured"

#### Widget 3: Workflow Status

- Fetches `GET /api/workflows` (existing endpoint)
- List of workflows with status badges
- Shows: name + status (active/draft/archived) + description (truncated)
- Only shows first 5 workflows
- Empty state: "No workflows defined"

#### Widget 4: Scheduled Tasks

- Fetches `GET /api/schedules` (existing endpoint)
- List of scheduled tasks sorted by `next_run`
- Shows: workflow name/ID + schedule (cron expression) + next run (relative time) + status badge
- Only shows first 5 schedules
- Empty state: "No scheduled tasks"

### Init Changes

The `init()` function adds four new API calls to the existing `Promise.all()`:

```javascript
const [today, week, month, sessions, agents, health, audit, connectors, workflows, schedules] = await Promise.all([
  // ... existing 6 calls ...
  MycelosAPI.get('/api/audit?limit=8'),
  MycelosAPI.get('/api/connectors'),
  MycelosAPI.get('/api/workflows'),
  MycelosAPI.get('/api/schedules'),
]);
```

### i18n Keys

New translation keys in `en.yaml` and `de.yaml`:
- `dashboard.recent_activity` — "Recent Activity" / "Letzte Aktivitaet"
- `dashboard.connector_health` — "Connector Health" / "Connector-Status"
- `dashboard.workflow_status` — "Workflow Status" / "Workflow-Status"
- `dashboard.scheduled_tasks` — "Scheduled Tasks" / "Geplante Aufgaben"
- `dashboard.no_activity` — "No recent activity" / "Keine Aktivitaet"
- `dashboard.no_connectors` — "No connectors configured" / "Keine Connectors"
- `dashboard.no_workflows` — "No workflows defined" / "Keine Workflows"
- `dashboard.no_schedules` — "No scheduled tasks" / "Keine geplanten Aufgaben"

---

## 2. Connector Add 422 Fix (already implemented)

### Root Cause

`ConnectorAddRequest`, `CredentialAddRequest`, and `RollbackRequest` were defined as classes **inside** the `setup_routes()` function. FastAPI could not resolve them as request body models and interpreted the `body` parameter as a query parameter instead, returning:

```json
{"detail":[{"type":"missing","loc":["query","body"],"msg":"Field required","input":null}]}
```

### Fix

Moved all three Pydantic model classes to **module level** in `src/mycelos/gateway/routes.py`, next to the existing `ChatRequest` and `ConfirmRequest` which already worked correctly.

This also fixes `POST /api/credentials` and `POST /api/config/rollback` which had the same bug.

---

## 3. MCP Registry Search Fix (already implemented)

### Root Cause

The frontend `searchMCP()` function in `connectors.html` assumed the MCP registry API (`https://registry.modelcontextprotocol.io/v0/servers`) returns fields like `install_command`, `command`, or `npm_command` on each server entry. The actual API returns:

```json
{
  "servers": [
    {
      "name": "io.github.user/package-name",
      "description": "...",
      "packages": [
        {
          "registryType": "npm",
          "identifier": "@user/package-name",
          "version": "1.0.0",
          "transport": { "type": "stdio" }
        }
      ]
    }
  ]
}
```

Two problems:
1. No `command` field — the install command must be built from `packages[].identifier`
2. Server `name` uses `io.github.user/name` format, not a simple name

### Fix

Updated the search result mapping in `connectors.html`:

```javascript
const npmPkg = (s.packages || []).find(p => p.registryType === 'npm');
const command = npmPkg ? `npx -y ${npmPkg.identifier}` : '';
const shortName = fullName.includes('/') ? fullName.split('/').pop() : fullName;
```

- Finds the first npm package and builds `npx -y <identifier>` command
- Extracts short name from the `org/name` format

---

## 4. User ID from Header

### Root Cause

Multiple places in `routes.py` hardcode `user_id="default"` instead of resolving the actual user. The `users` table exists and is used, but the gateway ignores it.

### Fix

User ID comes from the `X-User-Id` HTTP header. If the header is missing, fall back to the first active user from the `users` table (queried once at startup and cached on `api.state`).

#### Helper function (module level in `routes.py`):

```python
def _resolve_user_id(request: Request) -> str:
    """Resolve user ID from X-User-Id header, falling back to default user."""
    header_value = request.headers.get("X-User-Id")
    if header_value:
        return header_value
    return getattr(request.app.state, "default_user_id", "default")
```

#### Startup (in `server.py`):

Cache the default user on `api.state` during server init:

```python
row = mycelos.storage.fetchone("SELECT id, name, email, language, timezone FROM users WHERE status = 'active' ORDER BY created_at LIMIT 1")
api.state.default_user_id = row["id"] if row else "default"
api.state.default_user = dict(row) if row else {"id": "default", "name": "Default User"}
```

#### Places to update in `routes.py`:

1. `ChatRequest.user_id` default → still accept from JSON body, but override with header if present
2. Line ~415: `user_id="default"` in audio upload → `_resolve_user_id(request)`
3. Line ~432: `user_id="default"` in transcribe → `_resolve_user_id(request)`
4. Line ~522: `user_id="default"` in upload → `_resolve_user_id(request)`
5. `audit.log()` calls that pass no user_id → pass `_resolve_user_id(request)`

#### Health Endpoint: Include User Info

Extend `GET /api/health` response to include the resolved user:

```json
{
  "status": "ok",
  "uptime_seconds": 3600,
  "generation_id": 5,
  "scheduler": true,
  "user": {
    "id": "stefan",
    "name": "Stefan",
    "language": "de",
    "timezone": "Europe/Vienna"
  }
}
```

The user is resolved from `api.state.default_user` (cached at startup). This avoids an extra API call from the frontend.

#### Dashboard: Show User

The dashboard displays the user name in the System Health widget:

```
User         Stefan
Uptime       2h 15m
Gateway      ok
Scheduler    running
Config Gen   #5
```

No greeting/avatar — just a factual row in the health card.

#### Frontend:

The web frontend does not need to send `X-User-Id` — the fallback handles single-user setups. Multi-user setups (e.g. Telegram proxy) set the header.

---

## Files Changed

| File | Change |
|------|--------|
| `src/mycelos/gateway/routes.py` | Move 3 Pydantic models to module level; add `GET /api/audit` endpoint |
| `src/mycelos/frontend/pages/dashboard.html` | Add 4 monitoring widgets + 4 new API calls in init |
| `src/mycelos/frontend/pages/connectors.html` | Fix MCP registry search result parsing |
| `src/mycelos/i18n/locales/en.yaml` | Add dashboard widget translation keys |
| `src/mycelos/i18n/locales/de.yaml` | Add dashboard widget translation keys |
| `tests/test_gateway_routes.py` (new or extend) | Test `GET /api/audit` endpoint |
