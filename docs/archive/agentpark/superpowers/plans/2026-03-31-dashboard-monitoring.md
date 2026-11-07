# Dashboard Monitoring Widgets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four monitoring widgets to the dashboard, fix connector 422 and MCP registry search, resolve user ID from header/DB instead of hardcoded "default".

**Architecture:** New `GET /api/audit` endpoint for audit events, user caching at startup, `_resolve_user_id()` helper for all routes, four new dashboard widgets fetching existing + new API endpoints.

**Tech Stack:** FastAPI, Pydantic, SQLite, Alpine.js, Tailwind CSS

**Spec:** `docs/superpowers/specs/2026-03-31-dashboard-monitoring-design.md`

**Already implemented (verify only):**
- Pydantic models moved to module level (422 fix)
- MCP registry search result parsing (connectors.html)

---

### Task 1: User ID Resolution — Backend

**Files:**
- Modify: `src/mycelos/gateway/routes.py` (add `_resolve_user_id` helper, update endpoints)
- Modify: `src/mycelos/gateway/server.py:394-398` (cache default user at startup)
- Test: `tests/test_gateway.py`

- [ ] **Step 1: Write failing tests for user resolution**

Add to `tests/test_gateway.py`:

```python
def test_health_includes_user_info(client: TestClient):
    """GET /api/health should include user object."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "user" in data
    assert data["user"]["id"] == "default"
    assert "name" in data["user"]


def test_user_id_from_header(client: TestClient):
    """X-User-Id header should override default user."""
    resp = client.get("/api/health", headers={"X-User-Id": "custom-user"})
    assert resp.status_code == 200
    # Health endpoint itself doesn't vary by user, but the header is accepted
    # The real test is that _resolve_user_id reads the header


def test_connector_add_uses_resolved_user(client: TestClient):
    """POST /api/connectors should use resolved user_id in audit log."""
    resp = client.post("/api/connectors", json={
        "name": "test-user-connector",
        "command": "npx -y @test/mcp",
    })
    assert resp.status_code == 200

    # Verify audit event was logged
    mycelos = client.app.state.mycelos
    events = mycelos.storage.fetchall(
        "SELECT * FROM audit_events WHERE event_type = 'connector.added' ORDER BY created_at DESC LIMIT 1"
    )
    assert len(events) >= 1
    assert events[0]["user_id"] == "default"  # Default user from DB
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gateway.py::test_health_includes_user_info -v`
Expected: FAIL — `"user" not in data`

- [ ] **Step 3: Add `_resolve_user_id` helper and cache default user**

In `src/mycelos/gateway/routes.py`, add after the Pydantic model classes (after line ~88):

```python
def _resolve_user_id(request: Request) -> str:
    """Resolve user ID from X-User-Id header, falling back to default user."""
    header_value = request.headers.get("X-User-Id")
    if header_value:
        return header_value
    return getattr(request.app.state, "default_user_id", "default")
```

In `src/mycelos/gateway/server.py`, after `api.state.debug = debug` (line 398), add:

```python
# Cache default user for _resolve_user_id and /api/health
row = mycelos.storage.fetchone(
    "SELECT id, name, email, language, timezone FROM users WHERE status = 'active' ORDER BY created_at LIMIT 1"
)
api.state.default_user_id = row["id"] if row else "default"
api.state.default_user = dict(row) if row else {"id": "default", "name": "Default User"}
```

- [ ] **Step 4: Update health endpoint to include user**

In `src/mycelos/gateway/routes.py`, modify the health endpoint:

```python
@api.get("/api/health")
async def health() -> dict[str, Any]:
    """Health check endpoint."""
    mycelos = api.state.mycelos
    uptime = time.time() - api.state.start_time
    return {
        "status": "ok",
        "uptime_seconds": round(uptime, 1),
        "generation_id": mycelos.config.get_active_generation_id(),
        "scheduler": getattr(api.state, "scheduler_running", False),
        "user": getattr(api.state, "default_user", {"id": "default", "name": "Default User"}),
    }
```

- [ ] **Step 5: Update endpoints that hardcode user_id="default"**

In `src/mycelos/gateway/routes.py`:

1. **`/api/transcribe`** (line ~415): Change `user_id="default"` to `user_id=_resolve_user_id(request)`. This endpoint needs a `request: Request` parameter added to the function signature.

2. **`/api/audio`** (line ~432): Change the `user_id: str = "default"` form parameter. Replace with resolving from the raw request. Add `request: Request` parameter if not already present (it is already present — use it). Remove `user_id: str = "default"` from the function params and use `user_id = _resolve_user_id(request)` in the body.

3. **`/api/upload`** (line ~522): Same as audio — remove `user_id: str = "default"` param, add `user_id = _resolve_user_id(request)` in body.

4. **`/api/connectors` POST** (line ~608): Add `request: Request` to `add_connector()` signature, pass `user_id=_resolve_user_id(request)` to `audit.log()`.

5. **`/api/connectors` DELETE**: Same — add `request: Request`, pass resolved user to audit.log.

6. **`/api/credentials` POST and DELETE**: Same pattern.

7. **`/api/config/rollback` POST**: Same pattern.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_gateway.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/gateway/routes.py src/mycelos/gateway/server.py tests/test_gateway.py
git commit -m "feat: resolve user_id from X-User-Id header + include user in /api/health"
```

---

### Task 2: Audit Events API Endpoint

**Files:**
- Modify: `src/mycelos/gateway/routes.py` (add GET /api/audit inside setup_routes)
- Test: `tests/test_gateway.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_gateway.py`:

```python
def test_audit_endpoint_returns_events(client: TestClient):
    """GET /api/audit returns recent audit events."""
    # gateway.started is logged automatically during create_app
    resp = client.get("/api/audit")
    assert resp.status_code == 200
    events = resp.json()
    assert isinstance(events, list)
    assert len(events) >= 1
    # Check structure
    event = events[0]
    assert "event_type" in event
    assert "created_at" in event
    assert "details" in event


def test_audit_endpoint_respects_limit(client: TestClient):
    """GET /api/audit?limit=1 returns at most 1 event."""
    resp = client.get("/api/audit", params={"limit": 1})
    assert resp.status_code == 200
    assert len(resp.json()) <= 1


def test_audit_endpoint_filters_by_event_type(client: TestClient):
    """GET /api/audit?event_type=gateway filters by prefix."""
    resp = client.get("/api/audit", params={"event_type": "gateway"})
    assert resp.status_code == 200
    events = resp.json()
    for event in events:
        assert event["event_type"].startswith("gateway")


def test_audit_details_parsed_as_dict(client: TestClient):
    """The details field should be a dict, not a JSON string."""
    resp = client.get("/api/audit")
    events = resp.json()
    if events:
        assert isinstance(events[0]["details"], (dict, type(None)))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gateway.py::test_audit_endpoint_returns_events -v`
Expected: FAIL — 404

- [ ] **Step 3: Implement the audit endpoint**

Add inside `setup_routes()` in `src/mycelos/gateway/routes.py`, after the health endpoint:

```python
@api.get("/api/audit")
async def audit_events(limit: int = 10, event_type: str | None = None) -> list[dict[str, Any]]:
    """Return recent audit events, newest first."""
    import json as _json
    mycelos = api.state.mycelos
    limit = min(limit, 100)

    if event_type:
        rows = mycelos.storage.fetchall(
            "SELECT * FROM audit_events WHERE event_type LIKE ? ORDER BY created_at DESC LIMIT ?",
            (event_type + "%", limit),
        )
    else:
        rows = mycelos.storage.fetchall(
            "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    result = []
    for row in rows:
        entry = dict(row)
        # Parse JSON details string into dict
        if entry.get("details") and isinstance(entry["details"], str):
            try:
                entry["details"] = _json.loads(entry["details"])
            except (ValueError, TypeError):
                pass
        result.append(entry)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gateway.py::test_audit_endpoint_returns_events tests/test_gateway.py::test_audit_endpoint_respects_limit tests/test_gateway.py::test_audit_endpoint_filters_by_event_type tests/test_gateway.py::test_audit_details_parsed_as_dict -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/gateway/routes.py tests/test_gateway.py
git commit -m "feat: add GET /api/audit endpoint for audit event timeline"
```

---

### Task 3: Dashboard i18n Keys

**Files:**
- Modify: `src/mycelos/i18n/locales/en.yaml`
- Modify: `src/mycelos/i18n/locales/de.yaml`

- [ ] **Step 1: Add English translation keys**

In `src/mycelos/i18n/locales/en.yaml`, add under the `dashboard:` section (after `no_sessions`):

```yaml
    user: "User"
    recent_activity: "Recent Activity"
    connector_health: "Connector Health"
    workflow_status: "Workflow Status"
    scheduled_tasks: "Scheduled Tasks"
    no_activity: "No recent activity"
    no_connectors: "No connectors configured"
    no_workflows: "No workflows defined"
    no_schedules: "No scheduled tasks"
```

- [ ] **Step 2: Add German translation keys**

In `src/mycelos/i18n/locales/de.yaml`, add under the `dashboard:` section (after `no_sessions`):

```yaml
    user: "Benutzer"
    recent_activity: "Letzte Aktivitaet"
    connector_health: "Connector-Status"
    workflow_status: "Workflow-Status"
    scheduled_tasks: "Geplante Aufgaben"
    no_activity: "Keine Aktivitaet"
    no_connectors: "Keine Konnektoren"
    no_workflows: "Keine Workflows"
    no_schedules: "Keine geplanten Aufgaben"
```

- [ ] **Step 3: Commit**

```bash
git add src/mycelos/i18n/locales/en.yaml src/mycelos/i18n/locales/de.yaml
git commit -m "feat: add i18n keys for dashboard monitoring widgets"
```

---

### Task 4: Dashboard Widgets — Frontend

**Files:**
- Modify: `src/mycelos/frontend/pages/dashboard.html`

- [ ] **Step 1: Add new data properties and API calls to init()**

In `dashboard.html`, add new data properties to the `x-data` object (after `error: null,`):

```javascript
audit: [],
connectorList: [],
workflowList: [],
scheduleList: [],
```

Update the `init()` Promise.all to include the four new calls:

```javascript
async init() {
  try {
    const [today, week, month, sessions, agents, health, audit, connectorList, workflowList, scheduleList] = await Promise.all([
      MycelosAPI.get('/api/cost?period=today'),
      MycelosAPI.get('/api/cost?period=week'),
      MycelosAPI.get('/api/cost?period=month'),
      MycelosAPI.get('/api/sessions'),
      MycelosAPI.get('/api/agents'),
      MycelosAPI.get('/api/health'),
      MycelosAPI.get('/api/audit?limit=8'),
      MycelosAPI.get('/api/connectors'),
      MycelosAPI.get('/api/workflows'),
      MycelosAPI.get('/api/schedules'),
    ]);
    this.todayCost = today;
    this.weekCost = week;
    this.monthCost = month;
    this.sessions = (Array.isArray(sessions) ? sessions : []).slice(0, 5);
    this.agents = Array.isArray(agents) ? agents : [];
    this.health = health;
    this.audit = Array.isArray(audit) ? audit : [];
    this.connectorList = Array.isArray(connectorList) ? connectorList : [];
    this.workflowList = (Array.isArray(workflowList) ? workflowList : []).slice(0, 5);
    this.scheduleList = (Array.isArray(scheduleList) ? scheduleList : []).slice(0, 5);
  } catch (e) {
    this.error = e.message;
  } finally {
    this.loading = false;
  }
},
```

- [ ] **Step 2: Add helper functions for the widgets**

Add these helpers to the `x-data` object (after `tokenBarModels()`):

```javascript
auditIcon(eventType) {
  const map = {
    'connector.added': 'add_circle',
    'connector.removed': 'remove_circle',
    'config.rollback': 'history',
    'credential.stored': 'key',
    'credential.deleted': 'key_off',
    'gateway.reloaded': 'refresh',
    'gateway.started': 'power_settings_new',
  };
  if (map[eventType]) return map[eventType];
  if (eventType.startsWith('task')) return 'task_alt';
  if (eventType.startsWith('workflow')) return 'account_tree';
  return 'info';
},
auditText(event) {
  const type = event.event_type || '';
  const d = event.details || {};
  if (type === 'connector.added') return 'Connector added: ' + (d.connector || '');
  if (type === 'connector.removed') return 'Connector removed: ' + (d.connector || '');
  if (type === 'config.rollback') return 'Config rollback to #' + (d.target_generation || '');
  if (type === 'credential.stored') return 'Credential stored: ' + (d.service || '');
  if (type === 'credential.deleted') return 'Credential deleted: ' + (d.service || '');
  if (type === 'gateway.started') return 'Gateway started';
  if (type === 'gateway.reloaded') return 'Gateway reloaded';
  return type.replace(/\./g, ' ');
},
```

- [ ] **Step 3: Add user row to System Health widget**

In the System Health card, add a user row as the first entry (before the Uptime row):

```html
<div class="flex items-center justify-between" x-show="health.user">
  <span class="text-sm text-[var(--on-surface-variant)]" x-text="t('dashboard.user')">User</span>
  <span class="text-sm font-headline font-bold" x-text="health.user?.name || health.user?.id || '—'"></span>
</div>
```

- [ ] **Step 4: Add Recent Activity + Connector Health widgets (row 1)**

Insert after the Agent Status section closing `</div>` (before `</div><!-- end dashboard grid -->`):

```html
<!-- Two-column: Activity + Connectors -->
<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">

  <!-- Recent Activity -->
  <div>
    <h2 class="font-headline text-lg font-bold tracking-tight mb-4" x-text="t('dashboard.recent_activity')">Recent Activity</h2>
    <div class="card p-4">
      <template x-if="audit.length === 0">
        <p class="text-sm text-[var(--on-surface-variant)] py-4 text-center" x-text="t('dashboard.no_activity')">No recent activity</p>
      </template>
      <div class="space-y-1">
        <template x-for="ev in audit" :key="ev.id || ev.created_at">
          <div class="flex items-center gap-3 py-2.5 px-3 rounded-lg hover:bg-[var(--surface-container-high)] transition-colors">
            <span class="material-symbols-outlined text-[var(--on-surface-variant)] text-lg" x-text="auditIcon(ev.event_type)"></span>
            <div class="flex-1 min-w-0">
              <p class="text-xs text-[var(--on-surface)] truncate" x-text="auditText(ev)"></p>
            </div>
            <span class="text-[10px] text-[var(--on-surface-variant)] whitespace-nowrap" x-text="timeAgo(ev.created_at)"></span>
          </div>
        </template>
      </div>
    </div>
  </div>

  <!-- Connector Health -->
  <div>
    <h2 class="font-headline text-lg font-bold tracking-tight mb-4" x-text="t('dashboard.connector_health')">Connector Health</h2>
    <div class="card p-4">
      <template x-if="connectorList.length === 0">
        <p class="text-sm text-[var(--on-surface-variant)] py-4 text-center" x-text="t('dashboard.no_connectors')">No connectors configured</p>
      </template>
      <div class="space-y-1">
        <template x-for="c in connectorList" :key="c.id || c.name">
          <div class="flex items-center justify-between py-2.5 px-3 rounded-lg hover:bg-[var(--surface-container-high)] transition-colors">
            <div class="flex items-center gap-3">
              <span class="material-symbols-outlined text-[var(--on-surface-variant)] text-lg">extension</span>
              <span class="text-xs text-[var(--on-surface)]" x-text="c.name || c.id"></span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-[10px] text-[var(--on-surface-variant)]" x-text="(c.tool_count || 0) + ' tools'"></span>
              <span :class="statusClass(c.status)" x-text="c.status || 'unknown'"></span>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>

</div>
```

- [ ] **Step 5: Add Workflow Status + Scheduled Tasks widgets (row 2)**

Insert right after the Activity + Connectors row:

```html
<!-- Two-column: Workflows + Schedules -->
<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">

  <!-- Workflow Status -->
  <div>
    <h2 class="font-headline text-lg font-bold tracking-tight mb-4" x-text="t('dashboard.workflow_status')">Workflow Status</h2>
    <div class="card p-4">
      <template x-if="workflowList.length === 0">
        <p class="text-sm text-[var(--on-surface-variant)] py-4 text-center" x-text="t('dashboard.no_workflows')">No workflows defined</p>
      </template>
      <div class="space-y-1">
        <template x-for="w in workflowList" :key="w.id || w.name">
          <div class="flex items-center justify-between py-2.5 px-3 rounded-lg hover:bg-[var(--surface-container-high)] transition-colors">
            <div class="flex items-center gap-3 min-w-0">
              <span class="material-symbols-outlined text-[var(--on-surface-variant)] text-lg">account_tree</span>
              <div class="min-w-0">
                <p class="text-xs text-[var(--on-surface)] truncate" x-text="w.name || w.id"></p>
                <p class="text-[10px] text-[var(--on-surface-variant)] truncate" x-text="truncate(w.description || '', 40)"></p>
              </div>
            </div>
            <span :class="statusClass(w.status)" x-text="w.status || 'draft'"></span>
          </div>
        </template>
      </div>
    </div>
  </div>

  <!-- Scheduled Tasks -->
  <div>
    <h2 class="font-headline text-lg font-bold tracking-tight mb-4" x-text="t('dashboard.scheduled_tasks')">Scheduled Tasks</h2>
    <div class="card p-4">
      <template x-if="scheduleList.length === 0">
        <p class="text-sm text-[var(--on-surface-variant)] py-4 text-center" x-text="t('dashboard.no_schedules')">No scheduled tasks</p>
      </template>
      <div class="space-y-1">
        <template x-for="s in scheduleList" :key="s.id">
          <div class="flex items-center justify-between py-2.5 px-3 rounded-lg hover:bg-[var(--surface-container-high)] transition-colors">
            <div class="flex items-center gap-3 min-w-0">
              <span class="material-symbols-outlined text-[var(--on-surface-variant)] text-lg">schedule</span>
              <div class="min-w-0">
                <p class="text-xs text-[var(--on-surface)] truncate" x-text="s.workflow_id || s.id"></p>
                <p class="text-[10px] text-[var(--on-surface-variant)] font-mono" x-text="s.schedule || ''"></p>
              </div>
            </div>
            <div class="text-right">
              <span :class="statusClass(s.status)" x-text="s.status || 'active'"></span>
              <p class="text-[10px] text-[var(--on-surface-variant)] mt-0.5" x-show="s.next_run" x-text="timeAgo(s.next_run)"></p>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>

</div>
```

- [ ] **Step 6: Verify dashboard loads in browser**

Open `http://127.0.0.1:9100/pages/dashboard.html` and verify:
- All four new widgets render
- User name shows in System Health
- Empty states display correctly if no data

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/frontend/pages/dashboard.html
git commit -m "feat: add monitoring widgets to dashboard (activity, connectors, workflows, schedules)"
```

---

### Task 5: Verify Already-Implemented Fixes

**Files:**
- Verify: `src/mycelos/gateway/routes.py` (Pydantic models at module level)
- Verify: `src/mycelos/frontend/pages/connectors.html` (MCP registry parsing)
- Test: `tests/test_gateway.py`

- [ ] **Step 1: Write test for connector add (422 fix)**

Add to `tests/test_gateway.py`:

```python
def test_connector_add_returns_200(client: TestClient):
    """POST /api/connectors should accept valid connector (not 422)."""
    resp = client.post("/api/connectors", json={
        "name": "test-fix-422",
        "command": "npx -y @test/mcp-server",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "registered"
    assert data["connector"] == "test-fix-422"


def test_connector_add_duplicate_returns_409(client: TestClient):
    """POST /api/connectors with duplicate name returns 409."""
    client.post("/api/connectors", json={"name": "dup-test", "command": "npx -y @test/mcp"})
    resp = client.post("/api/connectors", json={"name": "dup-test", "command": "npx -y @test/mcp"})
    assert resp.status_code == 409


def test_credential_add_returns_200(client: TestClient):
    """POST /api/credentials should accept valid credential (not 422)."""
    resp = client.post("/api/credentials", json={
        "service": "test-service",
        "secret": "test-secret-value",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "stored"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_gateway.py::test_connector_add_returns_200 tests/test_gateway.py::test_connector_add_duplicate_returns_409 tests/test_gateway.py::test_credential_add_returns_200 -v`
Expected: All PASS (fixes already applied)

- [ ] **Step 3: Commit tests**

```bash
git add tests/test_gateway.py
git commit -m "test: add regression tests for connector/credential 422 fix"
```

---

### Task 6: Run Full Test Suite and Clean Up

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -x -q --tb=short`
Expected: All pass (except pre-existing DNS/credential failures unrelated to this work)

- [ ] **Step 2: Verify no hardcoded "default" user_id remains in routes.py**

Run: `grep -n 'user_id.*"default"' src/mycelos/gateway/routes.py`
Expected: Only `ChatRequest.user_id` default should remain (it's overridden by header in the endpoint logic)

- [ ] **Step 3: Final commit if any cleanup needed**

```bash
git add -A
git commit -m "chore: final cleanup for dashboard monitoring widgets"
```
