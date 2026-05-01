# Routes Monolith Split — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 3588-line `src/mycelos/gateway/routes.py` monolith — currently 85 routes inside one `setup_routes()` closure — into one `APIRouter` per HTTP domain. End state: `routes.py` keeps only `setup_routes()` (mounts the routers), the middleware classes (`LocalhostMiddleware`, `CSRFMiddleware`), and pure helpers used by tests.

**Why:** Every code change to any HTTP boundary currently touches a 3500-line file. Domain isolation makes review smaller, parallel work safer, and lets us delete dead routes per-domain without scanning the whole monolith. Audit hotspot #2 from the 2026-05-01 simplification pass.

**Architecture:** New package `src/mycelos/gateway/routers/` with one module per domain. Each module exposes a single `router = APIRouter()` and registers its handlers with `@router.<method>(...)`. Handlers swap closure access (`api.state.X`) for parameter access (`request.app.state.X`). `setup_routes(api)` becomes a thin orchestrator: import each router, call `api.include_router(router)`. Pydantic request models and shared helpers move into `src/mycelos/gateway/routers/_helpers.py` (or stay in `routes.py` if tests import them directly — verify before moving).

**Tech Stack:** Python 3.12+, FastAPI, pytest.

**Baseline rule:** After every task, `PYTHONPATH=src pytest tests/ --ignore=tests/e2e -q` must pass. Zero new failures vs. the pre-split baseline. The single known-flaky `tests/security/test_policy_engine.py::test_policy_roundtrip_property` is allowed to flap (rerun if it fails).

**Out of scope:**
- Fixing `api.state.oauth_pending_states` (the global dict that breaks under multi-worker — the audit flagged this; tackle separately).
- Refactoring individual handler bodies (e.g. the 245-line `add_connector` handler). Move them as-is in this pass.
- Renaming routes or changing response shapes.

---

## File Structure

New files (one per domain — split derived from the route inventory at `src/mycelos/gateway/routes.py:328-3588`):

- `src/mycelos/gateway/routers/__init__.py`
- `src/mycelos/gateway/routers/_helpers.py` — `resolve_user_id`, `sse_error`, `parse_frontmatter`, `list_docs`, `get_doc`, `render_session_markdown`, plus the Pydantic models (`ChatRequest`, `ConfirmRequest`, `ConnectorAddRequest`, `CredentialAddRequest`, `SessionUpdateRequest`, `RollbackRequest`).
- `src/mycelos/gateway/routers/chat.py` — `/api/chat`, `/api/health` (lines 328-509)
- `src/mycelos/gateway/routers/audit.py` — `/api/audit/*` (lines 511-641)
- `src/mycelos/gateway/routers/config.py` — `/api/config`, `/api/i18n`, `/api/config/rollback`, `/api/config/generations` (lines 642-655, 3432-3537)
- `src/mycelos/gateway/routers/sessions.py` — `/api/sessions/*` and `/api/sessions/{sid}/attachments/*` (lines 657-731, 1158-1183)
- `src/mycelos/gateway/routers/admin.py` — `/api/admin/*`, `/api/notifications/*`, `/api/reminders/*` (lines 733-855)
- `src/mycelos/gateway/routers/knowledge.py` — `/api/knowledge/*`, `/api/organizer/*` (lines 857-1448)
- `src/mycelos/gateway/routers/media.py` — `/api/transcribe`, `/api/audio`, `/api/upload`, `/api/reload` (lines 1449-1721)
- `src/mycelos/gateway/routers/connectors.py` — `/api/connectors/*` including OAuth start/callback (lines 1722-2527)
- `src/mycelos/gateway/routers/channels.py` — `/api/channels` (lines 2528-2600)
- `src/mycelos/gateway/routers/agents.py` — `/api/agents/*` (lines 2601-2692)
- `src/mycelos/gateway/routers/models.py` — `/api/models/*`, `/api/tools`, `/api/system/*` (lines 2693-3135)
- `src/mycelos/gateway/routers/cost.py` — `/api/cost` (lines 3136-3166)
- `src/mycelos/gateway/routers/setup.py` — `/api/setup/*`, `/api/credentials/*`, `/api/telegram/*`, `/api/memory` (lines 3167-3431)
- `src/mycelos/gateway/routers/workflows.py` — `/api/workflows/*`, `/api/workflow-runs/*`, `/api/schedules` (lines 3451-3549)
- `src/mycelos/gateway/routers/telegram_webhook.py` — `/telegram/webhook` (lines 3550-3574)
- `src/mycelos/gateway/routers/docs.py` — `/api/docs/*` (lines 3575-3588)

Files touched (existing):
- `src/mycelos/gateway/routes.py` — shrinks from 3588 to ~250 LOC. Keeps `setup_routes()` (now an orchestrator), `LocalhostMiddleware`, `CSRFMiddleware`. Helpers/models moved to `_helpers.py` but **must remain importable from `mycelos.gateway.routes`** for backwards compat with `tests/test_docs_api.py` (imports `_list_docs`, `_get_doc`) and `tests/test_stt.py` (imports `setup_routes`). Re-export with `from mycelos.gateway.routers._helpers import list_docs as _list_docs, get_doc as _get_doc`.
- `src/mycelos/gateway/server.py:15,368,600` — no changes needed; `setup_routes` and middleware imports stay valid.

Tests stay green without modification.

---

## Migration recipe (apply per domain)

For each handler in the source range:

1. Replace `@api.<method>(...)` with `@router.<method>(...)`.
2. Add `request: Request` to the signature if it doesn't already have one (FastAPI passes it automatically).
3. Replace every `api.state.X` with `request.app.state.X`.
4. Replace closure-imported helpers with module-level imports from `._helpers`.
5. Pydantic body models stay imported from `_helpers` (or keep local if used only in one router).
6. Top-of-file imports: copy whatever the original block imported (FastAPI, Starlette, mycelos modules).

Smoke check after each domain: `PYTHONPATH=src python -c "from mycelos.gateway.routes import setup_routes; from fastapi import FastAPI; api = FastAPI(); setup_routes(api); print(len(api.routes))"`. Expected route count: 85 (plus FastAPI's auto-routes — record exact baseline before starting).

---

## Task 0: Capture baseline route count

**Files:** none — diagnostic only.

- [ ] Run `PYTHONPATH=src python -c "from fastapi import FastAPI; from mycelos.gateway.routes import setup_routes; api = FastAPI(); setup_routes(api); print(len([r for r in api.routes if hasattr(r, 'path')]))"`. Record the number — every later task must end with this same count.
- [ ] Run baseline test suite: `PYTHONPATH=src pytest tests/ --ignore=tests/e2e -q`. Record pass count.

---

## Task 1: Scaffolding — empty package + helpers extraction

**Files:**
- New: `src/mycelos/gateway/routers/__init__.py`
- New: `src/mycelos/gateway/routers/_helpers.py`
- Modify: `src/mycelos/gateway/routes.py`

- [ ] Create `routers/__init__.py` with a one-line module docstring.
- [ ] Create `routers/_helpers.py` containing the helper functions and Pydantic models (move, don't duplicate). Use public names: `sse_error`, `resolve_user_id`, `parse_frontmatter`, `list_docs`, `get_doc`, `render_session_markdown`, plus all the `*Request` classes.
- [ ] In `routes.py`, replace the helper definitions with re-exports:
  ```python
  from mycelos.gateway.routers._helpers import (
      sse_error as _sse_error,
      resolve_user_id as _resolve_user_id,
      parse_frontmatter as _parse_frontmatter,
      list_docs as _list_docs,
      get_doc as _get_doc,
      render_session_markdown as _render_session_markdown,
      ChatRequest, ConfirmRequest, ConnectorAddRequest,
      CredentialAddRequest, SessionUpdateRequest, RollbackRequest,
  )
  ```
  This keeps the test imports valid.
- [ ] Run `PYTHONPATH=src pytest tests/ --ignore=tests/e2e -q`. Must match baseline.
- [ ] Commit: `refactor(gateway): extract routes.py helpers into routers/_helpers`

---

## Task 2: First domain — chat router (pilot)

Pilot the migration with the smallest top-of-file block. Establishes the pattern.

**Files:**
- New: `src/mycelos/gateway/routers/chat.py`
- Modify: `src/mycelos/gateway/routes.py`

- [ ] Create `routers/chat.py` with `router = APIRouter()` and the two handlers from `routes.py:328-509` (`/api/chat`, `/api/health`). Apply the migration recipe.
- [ ] In `routes.py`, inside `setup_routes(api)`, add `from mycelos.gateway.routers.chat import router as chat_router; api.include_router(chat_router)` and **delete** the original two handlers from the closure.
- [ ] Verify route count unchanged.
- [ ] Run baseline test suite. Must match.
- [ ] Manually verify: start the gateway (`PYTHONPATH=src python -m mycelos.gateway.server` or whatever the project uses), curl `/api/health`, send a `/api/chat` message, confirm SSE stream works.
- [ ] Commit: `refactor(gateway): extract chat + health into chat router`

---

## Task 3-17: Migrate remaining domains

One commit per domain, in this order (smallest/safest first, biggest last):

- [ ] **Task 3 — config router** (`/api/config`, `/api/i18n`, `/api/config/rollback`, `/api/config/generations`). Smallest, no shared state surprises.
- [ ] **Task 4 — docs router** (`/api/docs/*`). Pure helpers, no state.
- [ ] **Task 5 — cost router** (`/api/cost`). Single route.
- [ ] **Task 6 — telegram_webhook router** (`/telegram/webhook`). Single route, distinct prefix.
- [ ] **Task 7 — schedules router** (`/api/schedules`). Single route. Decide: merge into `workflows.py` or keep tiny. Keep tiny — easier to read.
- [ ] **Task 8 — channels router** (`/api/channels`).
- [ ] **Task 9 — agents router** (`/api/agents/*`).
- [ ] **Task 10 — admin router** (`/api/admin/*`, `/api/notifications/*`, `/api/reminders/*`).
- [ ] **Task 11 — audit router** (`/api/audit/*`).
- [ ] **Task 12 — sessions router** (`/api/sessions/*`, `/api/sessions/{sid}/attachments/*`). Watch `_render_session_markdown` import.
- [ ] **Task 13 — workflows router** (`/api/workflows/*`, `/api/workflow-runs/*`).
- [ ] **Task 14 — media router** (`/api/transcribe`, `/api/audio`, `/api/upload`, `/api/reload`). Big file uploads — re-test with a real audio file.
- [ ] **Task 15 — knowledge router** (`/api/knowledge/*`, `/api/organizer/*`). 23 routes — biggest cohesive block. After this commit, watch for any test that imports knowledge-handler internals.
- [ ] **Task 16 — models router** (`/api/models/*`, `/api/tools`, `/api/system/*`). 9 routes, ~440 LOC.
- [ ] **Task 17 — setup router** (`/api/setup/*`, `/api/credentials/*`, `/api/telegram/*`, `/api/memory`).
- [ ] **Task 18 — connectors router** (`/api/connectors/*` including OAuth start/callback). Biggest single block, ~800 LOC. **Special care:** the OAuth callback uses `api.state.oauth_pending_states` (a closure-shared dict). After moving to `request.app.state.oauth_pending_states`, verify the OAuth flow with a real connector test (e.g. Google Drive recipe) — this is the highest-risk domain.

After each task: route count unchanged, full test suite green, commit with message `refactor(gateway): extract <domain> router`.

---

## Task 19: Final cleanup

**Files:** `src/mycelos/gateway/routes.py`

- [ ] Confirm `setup_routes()` body is now just imports + `api.include_router(...)` calls — no `@api.<method>` decorators left.
- [ ] Run `wc -l src/mycelos/gateway/routes.py` — should be ~250 LOC (down from 3588).
- [ ] Run full test suite one final time.
- [ ] Commit: `refactor(gateway): finish routes.py split — 3588 → ~250 LOC`

---

## Risk register

| Risk | Mitigation |
|---|---|
| Test imports break (`_list_docs`, `_get_doc`, `setup_routes`) | Re-export from `routes.py` (Task 1). |
| `api.state.X` access pattern misses a closure variable | Grep each domain block for `api\.` before moving — every match must become `request.app.state.X`. |
| Shared OAuth state behaves differently after move | Connectors domain (Task 18) ships last with a manual OAuth smoke test. |
| Route count drops silently (handler dropped during copy) | Capture baseline count in Task 0; check after every task. |
| Some handler uses an inner `def` helper from the closure | None found in current file (verified: all handlers are top-level `async def` inside `setup_routes`). If discovered during migration, lift the helper to module scope or `_helpers.py`. |
| Pydantic models imported by tests | Re-export from `routes.py` (Task 1). |
| Big diff hides a renamed route | Diff each domain commit by route path — every path string must appear once before and once after. |

---

## Definition of done

- `src/mycelos/gateway/routes.py` is ≤ 300 LOC.
- 16 router modules exist under `src/mycelos/gateway/routers/`.
- Full test suite passes (modulo the known-flaky hypothesis test).
- Route count matches baseline exactly.
- Manual smoke: `/api/chat`, `/api/health`, one OAuth-bearing connector (Google or Brave), one file upload (`/api/upload`).
- No new global state; existing globals (`oauth_pending_states`) preserved as `request.app.state.*` and noted in the follow-up issue for multi-worker fix.
