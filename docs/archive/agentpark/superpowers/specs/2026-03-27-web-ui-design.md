# Mycelos Web UI — "Neural Mycelium" Design Spec

> **Date:** 2026-03-27
> **Status:** Approved
> **Prototype:** `stitch/code.html`, `stitch/DESIGN.md`, `stitch/screen.png`

## 1. Overview

A full web UI for Mycelos replacing the current minimal Next.js static export. Built as multi-page HTML with Alpine.js for reactivity and Tailwind CSS (CDN) for styling. No build step — the Gateway serves files directly.

Design follows the "Neural Mycelium" system: dark mode, cyan accents (#6ad6ff), glassmorphism, no-border philosophy, Space Grotesk + Manrope + Inter typography.

## 2. Architecture

### Tech Stack
- **HTML** — Multi-page, one file per view
- **Alpine.js** — Reactive state (chat messages, live updates, toggles)
- **Tailwind CSS CDN** — Styling, with CSS Custom Properties for theming
- **Vanilla JS** — API calls (fetch, EventSource for SSE)
- **No build step** — Gateway serves static files from `src/mycelos/frontend/`
- **No auth** — Localhost only (LocalhostMiddleware in Gateway)

### Why Not SPA?
- 6 pages → manageable as separate files
- Each page is independently editable/testable
- Chat SSE reconnects cheaply on navigation
- No 2000+ line single file to maintain

## 3. File Structure

```
src/mycelos/frontend/
  shared/
    base.css            — CSS Variables (Design Tokens), shared styles
    layout.js           — Alpine.js shared state, sidebar include, navigation
    api.js              — API client (fetch wrappers, SSE helper)
    markdown.js         — Markdown → HTML renderer (lightweight)
  pages/
    chat.html           — Main chat with SSE streaming
    dashboard.html      — Overview: costs, sessions, agent status, health
    agents.html         — Registered agents: status, capabilities, details
    knowledge.html      — Knowledge base browser: notes, search, markdown
    connectors.html     — MCP server management: install, remove, status
    settings.html       — Models, credentials, language, timezone, config
  assets/
    logo.png            — Mycelos logo (existing)
    favicon.ico         — Browser tab icon (existing)
    apple-touch-icon.png — iOS icon (existing)
  index.html            — Redirect to pages/chat.html
```

## 4. CSS Custom Properties (Theming)

All colors, fonts, and radii are CSS variables. Users can override `base.css` for custom themes.

```css
:root {
  /* Surfaces */
  --surface: #0c0e12;
  --surface-dim: #0c0e12;
  --surface-container-lowest: #000000;
  --surface-container-low: #111318;
  --surface-container: #171a1f;
  --surface-container-high: #1d2025;
  --surface-container-highest: #23262c;
  --surface-bright: #292c33;
  --surface-variant: #23262c;

  /* Primary (Cyan) */
  --primary: #6ad6ff;
  --primary-dim: #00bcec;
  --primary-container: #00cbfe;
  --on-primary: #00485c;

  /* Secondary (Blue) */
  --secondary: #23bcfe;
  --secondary-container: #00658c;

  /* Tertiary (Violet) */
  --tertiary: #879dff;
  --tertiary-container: #778ff7;

  /* Text */
  --on-surface: #f6f6fc;
  --on-surface-variant: #aaabb0;
  --on-background: #f6f6fc;

  /* Borders & Outlines */
  --outline: #74757a;
  --outline-variant: #46484d;

  /* Error */
  --error: #ff716c;

  /* Typography */
  --font-headline: 'Space Grotesk', sans-serif;
  --font-body: 'Manrope', sans-serif;
  --font-label: 'Inter', sans-serif;

  /* Border Radius */
  --radius-sm: 0.25rem;
  --radius-md: 0.5rem;
  --radius-lg: 0.75rem;
  --radius-xl: 1rem;
  --radius-full: 9999px;
}
```

## 5. Shared Layout

Every page includes:
- **Left Sidebar** (w-64) — Logo, navigation links, "Deploy Agent" CTA, Docs/Support
- **Top Header** — Search bar, notifications, user info
- **Mobile Bottom Nav** — Responsive alternative to sidebar

The sidebar highlights the active page. Navigation links: Dashboard, Agents, Chat, Knowledge, Connectors, Settings.

Logo placement: Top-left of sidebar, `logo.png` + "Mycelos" in primary color + "Neural Mycelium" tagline.

## 6. Pages

### 6.1 Chat (pages/chat.html)

The primary interaction surface.

**Features:**
- SSE streaming via `EventSource` to `/api/chat`
- Session list (sidebar or dropdown) via `/api/sessions`
- Markdown rendering for LLM responses
- Agent attribution: shows which agent is responding (Mycelos, Builder)
- Step-progress events shown as subtle status pills
- Typing indicator (3 pulsing dots)
- Voice button (existing Whisper integration via `/api/voice`)
- Input: textarea with auto-resize, send button, mic button, attachment button

**Message Styles (from prototype):**
- User messages: right-aligned, `secondary-container` background, rounded
- Agent messages: left-aligned, glassmorphism card, cyan accent glow
- System messages: centered, muted text
- Tool progress: inline pills with step name + status

**Right Panel (desktop only, w-72):**
- Active agent info (name, status)
- Session metrics (tokens, cost, model used)
- Quick actions (new session, clear, export)

### 6.2 Dashboard (pages/dashboard.html)

Overview and monitoring.

**Sections:**
- **Cost Overview** — Cards: today, this week, this month (from `/api/cost`)
- **Recent Sessions** — Last 5 sessions with preview, message count, age
- **Agent Status** — Cards per agent: active/idle/paused, last used
- **System Health** — Gateway uptime, SecurityProxy status, Scheduler status (from `/api/health`)
- **Token Usage Chart** — Simple bar chart (CSS-only, no chart library)

### 6.3 Agents (pages/agents.html)

Agent registry browser.

**Features:**
- Card grid: each agent shows name, type, status badge, capabilities tags
- Click → detail view (expandable or modal):
  - Description, capabilities, model assignment
  - Gherkin scenarios (readonly, syntax highlighted)
  - Generated code (readonly)
  - Test results summary
- Status badges: active (green), proposed (yellow), paused (gray), deprecated (red)
- "Build New Agent" button → navigates to Chat with builder handoff

### 6.4 Knowledge (pages/knowledge.html)

Knowledge base browser.

**Features:**
- Left panel: note list, grouped by tags, searchable
- Main area: Markdown renderer for selected note
- Search: live search via `/api/knowledge/search`
- New note: inline editor (textarea + Markdown preview)
- Edit: toggle between view and edit mode
- Links between notes shown as subtle connections

### 6.5 Connectors (pages/connectors.html)

MCP server management.

**Features:**
- Installed connectors: cards with name, status badge, tool count, description
- Each card: expand to see discovered tools list
- "Add Connector" dialog:
  - Name input
  - Command input (npx, docker, etc.)
  - Optional secret input (password field)
  - MCP Registry search built-in (search → select → auto-fill command)
- Remove button (with confirmation)
- Status indicators: connected (green pulse), disconnected (gray), error (red)

### 6.6 Settings (pages/settings.html)

Configuration management.

**Sections:**
- **Models** — Table: ID, provider, tier, cost, status. Agent assignments below.
- **Credentials** — List: service, label, preview (first4...last4). Add/delete buttons. Keys never shown in full.
- **Language** — Dropdown: English, Deutsch (updates user.preference.language in memory)
- **Timezone** — Auto-detected, override dropdown
- **Config Generations** — List with generation ID, description, timestamp. Rollback button.
- **Data** — Export credentials button, Reset button (with big warning)

## 7. API Endpoints (New)

All new endpoints in `src/mycelos/gateway/routes.py`:

```
GET    /api/knowledge                 — List notes (paginated, filterable by tag)
GET    /api/knowledge/<path>          — Read single note (returns Markdown)
POST   /api/knowledge/search          — Full-text search {query, limit}
POST   /api/knowledge                 — Create/update note {path, content, tags}

GET    /api/connectors                — All connectors + status + tool_count
POST   /api/connectors                — Add connector {name, command, secret?}
DELETE /api/connectors/<id>           — Remove connector

GET    /api/agents                    — All agents + status + capabilities
GET    /api/agents/<id>               — Agent detail + code + tests + gherkin

GET    /api/models                    — All models + agent assignments
GET    /api/cost                      — Token usage {period: today|week|month}
GET    /api/cost/agents               — Per-agent cost breakdown

GET    /api/credentials               — List (service, label, preview — NO keys)
POST   /api/credentials               — Store credential {service, label, secret}
DELETE /api/credentials/<service>      — Delete credential

GET    /api/config                    — Active config + recent generations
POST   /api/config/rollback           — Rollback to generation {id}

GET    /api/workflows                 — List workflows + status
```

All endpoints return JSON. Error format: `{"error": "message"}`.

## 8. Design Rules (from DESIGN.md)

- **No-Line Rule:** No 1px borders for sectioning. Use background color shifts only.
- **Ghost Border Fallback:** `outline-variant` at 15% opacity where accessibility requires boundaries.
- **Glass & Gradient:** CTAs use gradient `primary` → `primary-container`. Modals use glassmorphism (semi-transparent + backdrop-blur).
- **No pure white:** Text uses `--on-surface` (#f6f6fc), never #FFFFFF.
- **No harsh shadows:** Only extra-diffused (30-60px blur), low-opacity (4-8%) ambient shadows.
- **Minimum radius:** 0.25rem on all corners.
- **Asymmetric layouts** for dashboard summaries.
- **Cyan glow** (primary at 10% opacity) behind active/processing elements.

## 9. Logo Usage

- **Sidebar:** Logo image (40x40) + "Mycelos" in `--primary` + "Neural Mycelium" tagline
- **Favicon:** favicon.ico in browser tab
- **Dashboard:** Logo in header area
- **Mobile:** Logo in top bar

## 10. Responsive Design

- **Desktop (>768px):** Full layout — left sidebar + main content + right panel (chat only)
- **Mobile (<768px):** No sidebars. Bottom navigation bar. Full-width content. Chat input fixed at bottom.

The prototype already includes mobile nav HTML.

## 11. Implementation Notes

- Markdown rendering: use a lightweight library (marked.js ~40KB) or a minimal custom renderer
- SSE handling: `EventSource` with reconnect logic on disconnect
- Alpine.js state: `x-data` on body for global state (activeSession, user), page-local state for content
- Code syntax highlighting: optional, use Prism.js or highlight.js only on agents page
- No external images: replace the Google-hosted placeholder images from the prototype with local assets or CSS-generated avatars
- The Gateway already serves static files from `src/mycelos/frontend/out/` — update the path to `src/mycelos/frontend/` to include the new structure

## 12. Non-Goals (for this iteration)

- Multi-user auth (localhost only)
- Real-time collaboration
- Mobile app
- Drag-and-drop workflow builder
- Custom theme editor UI (users edit CSS vars directly)
