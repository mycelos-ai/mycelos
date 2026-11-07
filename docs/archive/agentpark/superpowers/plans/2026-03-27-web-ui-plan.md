# Web UI "Neural Mycelium" — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full 6-page web UI for Mycelos with Alpine.js + Tailwind CDN, connecting to the Gateway API.

**Architecture:** Multi-page HTML served by the Gateway. Alpine.js for reactivity, Tailwind CDN for styling, CSS Custom Properties for theming. No build step. Each page is an independent HTML file sharing CSS and JS via `shared/`.

**Tech Stack:** HTML, Alpine.js 3.x, Tailwind CSS CDN, vanilla JS (fetch + EventSource), marked.js (Markdown)

**Spec:** `docs/superpowers/specs/2026-03-27-web-ui-design.md`

**Prototype:** `stitch/code.html` (layout, colors, glassmorphism)

---

## File Structure

### New Files
- `src/mycelos/frontend/shared/base.css` — CSS Variables (Design Tokens), shared styles, glassmorphism
- `src/mycelos/frontend/shared/layout.js` — Loads sidebar/header fragments, navigation state, Alpine.js init
- `src/mycelos/frontend/shared/api.js` — API client (fetch wrappers, SSE helper)
- `src/mycelos/frontend/shared/markdown.js` — Markdown rendering wrapper
- `src/mycelos/frontend/shared/sidebar.html` — Sidebar fragment (loaded by layout.js into every page)
- `src/mycelos/frontend/shared/header.html` — Header fragment (search bar, notifications, user)
- `src/mycelos/frontend/shared/mobile-nav.html` — Mobile bottom navigation fragment
- `src/mycelos/frontend/pages/chat.html` — Chat page
- `src/mycelos/frontend/pages/dashboard.html` — Dashboard page
- `src/mycelos/frontend/pages/agents.html` — Agents page
- `src/mycelos/frontend/pages/knowledge.html` — Knowledge page
- `src/mycelos/frontend/pages/connectors.html` — Connectors page
- `src/mycelos/frontend/pages/settings.html` — Settings page
- `src/mycelos/frontend/index.html` — Redirect to pages/chat.html

### Modified Files
- `src/mycelos/gateway/server.py` — Update static file path from `frontend/out` to `frontend`
- `src/mycelos/gateway/routes.py` — Add missing API endpoints
- `src/mycelos/gateway/spa.py` — Adapt for new directory structure

---

## Task 1: Foundation — CSS + Layout + API Client

**Files:**
- Create: `src/mycelos/frontend/shared/base.css`
- Create: `src/mycelos/frontend/shared/layout.js`
- Create: `src/mycelos/frontend/shared/api.js`
- Create: `src/mycelos/frontend/index.html`
- Modify: `src/mycelos/gateway/server.py` (static file mount path)

- [ ] **Step 1: Create base.css with Design Tokens**

All CSS Custom Properties from the spec. Includes glassmorphism utilities, typography, shared component styles (sidebar, header, buttons, cards, inputs).

Key classes: `.glass-card`, `.glow-text`, `.mycelium-line`, `.btn-primary`, `.btn-secondary`, `.card`, `.status-badge`

- [ ] **Step 2: Create layout.js**

Alpine.js component that:
- Determines active page from `window.location.pathname`
- Renders sidebar HTML via `x-html` or template literal
- Handles mobile nav toggle
- Exposes `$store.app` with global state (user name, active agent)

- [ ] **Step 3: Create api.js**

API client module:
- `api.get(path)` → `fetch(path).then(r => r.json())`
- `api.post(path, data)` → `fetch(path, {method:'POST', body: JSON.stringify(data), ...})`
- `api.delete(path)` → `fetch(path, {method:'DELETE'})`
- `api.stream(path, data, onEvent)` → EventSource/fetch SSE wrapper
- Error handling: network errors, JSON parse errors
- Base URL: relative (same origin)

- [ ] **Step 4: Create index.html redirect**

Simple meta-redirect to `/pages/chat.html`.

- [ ] **Step 5: Update Gateway static file mount**

Change `frontend/out` to `frontend` in `server.py`. Ensure both old path (`/out/`) and new path work. Keep the SPA fallback but adapt it for multi-page (return 404 instead of index.html for unknown API paths).

- [ ] **Step 6: Test — verify Gateway serves new files**

Start gateway, confirm `http://localhost:9100/` redirects to chat, CSS loads, Alpine.js initializes.

- [ ] **Step 7: Commit**

```
feat: web UI foundation — CSS tokens, layout.js, api.js
```

---

## Task 2: Chat Page

**Files:**
- Create: `src/mycelos/frontend/pages/chat.html`
- Create: `src/mycelos/frontend/shared/markdown.js`

- [ ] **Step 1: Create chat.html with layout**

Full page with sidebar, header, chat area, input footer. Use the stitch prototype as base — adapt the HTML structure, replace Google-hosted images with local logo, replace placeholder text.

Alpine.js state:
```javascript
{
  sessions: [],
  activeSession: null,
  messages: [],
  input: '',
  sending: false,
  activeAgent: 'Mycelos',
  tokenCount: 0,
  modelUsed: '',
}
```

- [ ] **Step 2: Implement SSE streaming**

On send: call `api.stream('/api/chat', {message, session_id}, handler)`.
Handler processes events:
- `session` → store session_id
- `agent` → update activeAgent display
- `text` → append to messages, render Markdown
- `step-progress` → show tool execution pills
- `system-response` → show system message
- `error` → show error toast
- `done` → update token count, re-enable input

- [ ] **Step 3: Create markdown.js**

Wrapper around marked.js CDN. Function: `renderMarkdown(text)` → HTML string. Configure: breaks enabled, sanitize (basic XSS prevention), code block styling.

- [ ] **Step 4: Session management**

On page load: fetch `/api/sessions`, show list. Click session → load messages. New session button. Session list in sidebar or dropdown.

- [ ] **Step 5: Input area**

Auto-resizing textarea, send on Enter (Shift+Enter for newline), send button, mic button (calls `/api/audio`), disabled state while sending.

- [ ] **Step 6: Right panel (desktop)**

Agent info, session metrics (tokens, cost, model). Shown via Alpine.js `x-show` on desktop breakpoint.

- [ ] **Step 7: Test end-to-end**

Open chat, send message, verify SSE streaming works, messages render as Markdown, agent name shows, tokens counted.

- [ ] **Step 8: Commit**

```
feat: chat page — SSE streaming, sessions, markdown rendering
```

---

## Task 3: Missing API Endpoints

**Files:**
- Modify: `src/mycelos/gateway/routes.py`

Existing endpoints already cover: knowledge notes, knowledge graph, health, config, sessions, chat, audio, upload.

Missing endpoints to add:

- [ ] **Step 1: GET /api/connectors**

```python
@api.get("/api/connectors")
async def list_connectors():
    connectors = api.state.mycelos.connector_registry.list_connectors()
    mcp_mgr = api.state.mycelos.mcp_manager
    result = []
    for c in connectors:
        tool_count = 0
        if mcp_mgr:
            tool_count = len([t for t in mcp_mgr.list_tools() if t["name"].startswith(f"{c['id']}.")])
        result.append({**c, "tool_count": tool_count})
    return result
```

- [ ] **Step 2: POST /api/connectors + DELETE /api/connectors/{id}**

POST: accepts `{name, command, secret?}` → calls `_connector_add_smart` logic.
DELETE: calls `connector_registry.remove(id)`.

- [ ] **Step 3: GET /api/agents + GET /api/agents/{id}**

List all agents with status, capabilities. Detail endpoint returns code, tests, gherkin from ObjectStore.

- [ ] **Step 4: GET /api/models**

Returns all models + agent assignments from model_registry.

- [ ] **Step 5: GET /api/cost + GET /api/cost/agents**

Query `llm_usage` table. Aggregate by period (today/week/month) and by agent.

- [ ] **Step 6: GET /api/credentials + POST + DELETE**

List: returns service, label, description, created_at (NO keys).
Store: accepts `{service, label, secret, description?}`.
Delete: by service+label.

- [ ] **Step 7: POST /api/config/rollback**

Accepts `{generation_id}`, calls `config.rollback(generation_id)`.

- [ ] **Step 8: GET /api/workflows**

Returns all workflows from workflow_registry.

- [ ] **Step 9: Commit**

```
feat: REST API endpoints — connectors, agents, models, cost, credentials, workflows
```

---

## Task 4: Dashboard Page

**Files:**
- Create: `src/mycelos/frontend/pages/dashboard.html`

- [ ] **Step 1: Create dashboard.html**

Layout with sidebar + main content. Sections:

**Cost Overview** — 3 cards (Today, Week, Month). Fetch from `/api/cost?period=today` etc.

**Recent Sessions** — Last 5 from `/api/sessions`. Show message count, age, preview.

**Agent Status** — Cards from `/api/agents`. Status badges: active (green pulse), paused (gray).

**System Health** — From `/api/health`. Uptime, scheduler status, proxy status.

Alpine.js fetches all data on page load (`x-init`), renders into cards.

- [ ] **Step 2: CSS-only bar chart for token usage**

Simple horizontal bars using CSS width percentages. No chart library needed.

- [ ] **Step 3: Test**

Verify all sections load data, costs display correctly, health endpoint reflects real state.

- [ ] **Step 4: Commit**

```
feat: dashboard page — costs, sessions, agents, health
```

---

## Task 5: Settings Page

**Files:**
- Create: `src/mycelos/frontend/pages/settings.html`

- [ ] **Step 1: Create settings.html**

Sections:

**Models** — Table from `/api/models`. Columns: ID, Provider, Tier, Input Cost, Output Cost, Status.

**Credentials** — List from `/api/credentials`. Add button opens form (service, label, secret). Delete button with confirmation. Keys never shown.

**Language** — Dropdown (en, de). Updates via memory API.

**Timezone** — Auto-detected, override dropdown.

**Config Generations** — List from `/api/config`. Rollback button calls POST `/api/config/rollback`.

**Data Management** — Export credentials button, Reset warning.

- [ ] **Step 2: Form interactions**

Add credential: inline form with service/label/secret inputs, submit via `/api/credentials`.
Delete: confirmation dialog, then DELETE call.
Rollback: confirmation, then POST.

- [ ] **Step 3: Test**

Add/delete credential via UI, verify in DB. Rollback config generation.

- [ ] **Step 4: Commit**

```
feat: settings page — models, credentials, language, config
```

---

## Task 6: Agents Page

**Files:**
- Create: `src/mycelos/frontend/pages/agents.html`

- [ ] **Step 1: Create agents.html**

Card grid from `/api/agents`. Each card: name, type badge, status badge, capability tags.

Click → expand detail panel:
- Description
- Capabilities list
- Model assignment
- Gherkin (collapsible, code block)
- Code (collapsible, code block)
- Test results

"Build New Agent" button → redirects to `/pages/chat.html` (could pre-fill a message).

- [ ] **Step 2: Status badges**

CSS classes: `.badge-active` (green), `.badge-proposed` (yellow), `.badge-paused` (gray), `.badge-deprecated` (red). Defined in base.css.

- [ ] **Step 3: Commit**

```
feat: agents page — card grid, detail view, status badges
```

---

## Task 7: Knowledge Page

**Files:**
- Create: `src/mycelos/frontend/pages/knowledge.html`

- [ ] **Step 1: Create knowledge.html**

Two-panel layout:
- Left: note list (from `/api/knowledge/notes`), grouped by tags, search input
- Right: Markdown-rendered content of selected note

Search: live search on input, debounced 300ms, calls `/api/knowledge/notes?query=...`.

- [ ] **Step 2: Note editing**

Toggle between view and edit mode. Edit: textarea with Markdown content. Save: POST to `/api/knowledge` (note endpoint already exists via `/api/knowledge/notes`).

- [ ] **Step 3: New note**

"New Note" button → blank editor with title + content inputs. Save creates note.

- [ ] **Step 4: Commit**

```
feat: knowledge page — note browser, search, edit, create
```

---

## Task 8: Connectors Page

**Files:**
- Create: `src/mycelos/frontend/pages/connectors.html`

- [ ] **Step 1: Create connectors.html**

Cards from `/api/connectors`. Each: name, type, status badge (connected/disconnected), tool count, description.

Expand card → tool list (fetch from `/api/chat` with `connector_tools` — or add a dedicated endpoint).

- [ ] **Step 2: Add Connector dialog**

Form: name, command (textarea), secret (password input, optional). MCP Registry search: input + button, results shown as selectable cards that auto-fill the command.

Submit → POST `/api/connectors`. Show success/error.

- [ ] **Step 3: Remove connector**

Delete button with confirmation modal. DELETE `/api/connectors/{id}`.

- [ ] **Step 4: Commit**

```
feat: connectors page — manage MCP servers, add/remove, search registry
```

---

## Task 9: Polish + Mobile

**Files:**
- All pages

- [ ] **Step 1: Mobile bottom navigation**

From prototype: fixed bottom bar with icons for Dashboard, Chat, Agents, Profile. Shown < 768px, sidebar hidden.

- [ ] **Step 2: Responsive adjustments**

Chat: hide right panel on mobile. Full-width content. Input pinned to bottom.
Dashboard: stack cards vertically.
Knowledge: collapse to single panel with back-navigation.

- [ ] **Step 3: Logo integration**

Sidebar: logo.png (40x40) + "Mycelos" in primary + tagline.
Replace all Google-hosted placeholder images.

- [ ] **Step 4: Final testing**

Test all 6 pages on desktop and mobile viewport. Verify:
- Navigation works between all pages
- Chat SSE streaming
- Knowledge search + edit
- Connector add/remove
- Settings credential management
- Dashboard data loads
- Agents detail view

- [ ] **Step 5: Commit + push**

```
feat: Web UI "Neural Mycelium" — 6 pages, fully functional

Multi-page HTML + Alpine.js + Tailwind. No build step.
Chat with SSE, Knowledge browser, Connector management,
Cost dashboard, Agent registry, Settings with credentials.
CSS Custom Properties for theming.
```

---

## Summary

| Task | Description | Estimated Effort |
|------|-------------|------------------|
| 1 | Foundation — CSS, Layout, API Client | Medium |
| 2 | Chat Page — SSE, Sessions, Markdown | Large |
| 3 | API Endpoints — 10+ new REST routes | Medium |
| 4 | Dashboard — Costs, Health, Status | Medium |
| 5 | Settings — Models, Credentials, Config | Medium |
| 6 | Agents — Card Grid, Detail View | Small |
| 7 | Knowledge — Browser, Search, Edit | Medium |
| 8 | Connectors — MCP Management, Registry | Medium |
| 9 | Polish + Mobile | Small |
