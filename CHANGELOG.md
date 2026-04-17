# Changelog

## Week 16 (2026)

### Settings — Editable Agent-Model Assignments
- The Models settings page now groups assignments by agent and lets you change the model list and priority order directly, with Move-Up / Move-Down / Remove / Add Fallback controls and a Save button per agent.
- `/api/models` joins `agents.name` so each assignment row carries a readable agent label (falls back to agent_id, then "System defaults" for the purpose-wide default chain).
- New `PUT /api/models/assignments/{agent_id}` replaces the model chain for a given purpose, validating every model ID exists before writing (fail-closed).

### Security — MCP Credential Lookup Fail-Closed (Rule 3)
- `MycelosMCPClient._resolve_token` no longer swallows exceptions from the credential proxy. Credential-store errors now surface to the caller instead of silently degrading to an unauthenticated request that produces a confusing 401 downstream.

### Security — HTTP Tool Error Sanitization (Rule 4)
- `http_get` / `http_post` now run exception messages through `ResponseSanitizer` before returning them to agents. Inline URL credentials (`https://user:pass@host`) and reflected tokens (Bearer, API-keys, …) no longer leak into agent-visible error responses.
- `ResponseSanitizer` gained a new pattern that redacts userinfo in URLs.

### Security — Config Tamper Detection (SEC09)
- `ConfigGenerationManager.get_active_config()` and `_load_config()` now re-compute the SHA-256 of the stored snapshot and compare to `config_hash`. On mismatch they raise `ConfigTamperError` and emit a `config.tamper_detected` audit event when an audit logger is wired in. Previously a direct DB write to `config_generations.config_snapshot` was silently loaded as truth.

### Security — Automatic Audit Trail for Registry Mutations (Rule 1)
- `ConfigNotifier.notify_change()` now emits a `{trigger}.applied` audit event with the change description every time it is called. Credential rotations, policy changes, agent status updates, workflow deprecation, schedule add/pause/resume/delete, mount add/revoke, model registry changes, and connector registry changes — all now leave a trace without needing each caller to remember.
- The audit event is emitted even when the config generation insert itself fails, so a DB degradation cannot silently hide a state change.

### Security — Agent Subprocess Env Hardening
- `agent_runner._safe_env()` now strips `*_API_KEY` and `*APIKEY` variables from subprocess environments (previously only matched SECRET/TOKEN/PASSWORD/CREDENTIAL/MASTER_KEY substrings — ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY etc. leaked through)
- Added SEC05 tests in `tests/security/test_sandbox_boundaries.py` covering the denylist

### PDF Knowledge Ingest
- PDFs uploaded via Telegram or Web are now saved as Knowledge documents with LLM-generated summaries
- New `document` note type with `source_file` field linking to original PDF in `knowledge/documents/`
- Text extraction via pymupdf (free), summarization via Haiku (~$0.005)
- Scanned PDFs (no text layer) stored with placeholder — Vision analysis available on request
- Vision renders pages as PNG, sends to Claude Sonnet (~$0.02/page), extracts text + structure as Markdown
- PDF icon, download button, and Vision button in Knowledge UI
- Telegram and Web upload handlers route PDFs through the ingest pipeline automatically

### Note Splitting
- New `note_split` tool: LLM analyzes a note and proposes 2-5 focused sub-notes
- Split button (scissors icon) in note detail UI with section preview before confirming
- Original note becomes an index with Wikilinks to the child notes
- Available via chat ("split this note") and UI button
- Both `note_split` and `note_vision` registered in `knowledge_manage` discovery category


### Telegram Notification Fix
- Fixed workflow notifications failing silently when message exceeds Telegram's 4096-character limit — `send_notification()` now auto-splits long messages at paragraph/line boundaries
- Fixed `mark_notified()` being called even when Telegram delivery failed — undelivered notifications now retry on the next 1-minute cycle instead of being silently lost

### Knowledge Base — Duplicate Detection
- Organizer now detects duplicate notes via vector similarity (threshold 0.92) using existing embeddings — no extra LLM calls
- New `merge` suggestion type in organizer inbox with similarity percentage
- Auto-merge after 24h: appends newer content to older note, merges tags, archives the duplicate
- "Find Duplicates" button for one-time full sweep across all notes
- New API endpoint: `POST /api/organizer/sweep-duplicates`
- Merge execution on accept: `POST /api/organizer/suggestions/{id}/accept` now handles merge kind

### Knowledge Base — Note Detail Actions
- Status toggle: click the status badge on tasks to mark done/reopen
- Archive button in note detail action bar (next to delete)
- Editable tags: remove with "×", add with inline input
- Priority selector: None/Low/Medium/High dropdown with color-coded badge
- Extended `PUT /api/knowledge/notes/{path}` with status, tags, priority, archive fields


## Week 15 (2026)

### Prompt Architecture
- Added `PromptLoader` class for loading Markdown prompt files with `{variable}` substitution
- Extracted all agent system prompts from Python code to editable Markdown files in `src/mycelos/prompts/`:
  - `mycelos.md` — main Mycelos system prompt
  - `mycelos-channel-api.md`, `mycelos-channel-cli.md`, `mycelos-channel-telegram.md` — channel-specific additions
  - `builder.md` — Builder-Agent prompt
  - `planner.md` — PlannerAgent prompt (with `{system_context}` variable)
  - `knowledge-organizer.md` — Knowledge organizer prompt
- Added explicit cost-optimization rule to Builder prompt (default to haiku for workflows)

### Code Cleanup
- Removed dead `CreatorAgent` class (`creator.py`) — superseded by CreatorPipeline
- Cleaned up Orchestrator: removed unused CREATE_AGENT routing path
- Removed `tests/test_creator.py` and CREATE_AGENT test from orchestrator tests

### Reminder Scheduler Overhaul
- Reminders now fire through a periodic Huey job (`reminder_tick`, every 1 minute) — the old fire-once daemon-thread approach had no persistence (gateway restart = lost reminder) and never fired anything that was persisted with plain `reminder=1, due=today`
- New column `knowledge_notes.reminder_fired_at` (ISO datetime, nullable) replaces the destructive `reminder = 0` reset. The reminder flag now means "this task has a reminder configured"; `reminder_fired_at` means "…and it's already been handled" (either by the scheduler or by the user dismissing it)
- New unified query `ReminderService.get_due_reminders_now()` honors both `remind_at` precision and the `reminder_fired_at` guard: a row is ripe when it's flagged, not dismissed, and either `remind_at <= now` or (`remind_at IS NULL` and `due <= today`). `remind_at` always wins when set
- `note_write(remind_in="5m")` and `note_remind(when="5m")` no longer spawn a daemon thread. They compute `remind_at = now + delta` and persist it; the Huey tick takes over from there. Gateway restart is now survivable
- Inbox bell: no longer shows future reminders or already-dismissed entries — it reflects what needs attention *right now*. The query goes through the same `get_due_reminders_now()` helper so the scheduler and the bell can't disagree
- Bell entries are **click-to-dismiss**: clicking a reminder POSTs to `/api/admin/inbox/dismiss`, which stamps `reminder_fired_at=now` and emits a `reminder.dismissed` audit event, then navigates to the note. The entry disappears from the bell on the next refresh
- New doctor check `reminder_scheduler` warns when there's been no `reminder.tick` audit event in the last 2 hours — a clear signal that the Huey consumer is dead and reminders won't fire
- Audit events renamed: `reminder.sent` → `reminder.fired` (with `channels_succeeded` / `channels_failed` detail) and new `reminder.dismissed`, `reminder.tick`

### LLM Time Awareness
- Each user message now arrives at the LLM with a `[current time: YYYY-MM-DD HH:MM]` prefix so relative phrasing like "in 5 minutes" resolves against *now*, not against the stale session-start moment baked into the system prompt
- Prefix lives only in the in-memory conversation sent to the model — the persisted session store still records the pure user text, so replays, audits, and exports stay clean
- Prefix is regenerated on every user turn, not every model message (tool_results, proactive notifications, assistant turns) — minimal token overhead
- Does not fix the separate scheduler-never-runs bug: relative reminders that the LLM translates to `note_write(remind_in="5m")` still rely on the daemon-thread timer path, not on a periodic scheduler tick

### Task + Reminder Detail in Knowledge UI
- New `knowledge_notes.remind_at` column (ISO datetime, nullable) — separate from `due` (which stays a date). `due` answers "when is it due", `remind_at` answers "when exactly should I be reminded"
- `KnowledgeBase.set_reminder(path, due, remind_at=...)` + `/api/knowledge/notes/.../remind` accept the new field; calling without `remind_at` clears a previously-set datetime
- `GET /api/knowledge/notes/{path}` now returns `reminder`, `remind_at`, `remind_via` alongside content — the detail view reads these from the index row so DB-only updates are visible immediately
- Knowledge detail pane: type badge, task status badge, due-date badge, reminder badge with exact fire time, and "Set time / Change time" datetime-local input that writes through the existing `/remind` endpoint
- Task list rows now have a hover tooltip with title, due date, and reminder status

### Header Inbox Bell
- Lightning-bolt icon removed from the header (never had a handler)
- Bell now opens a dropdown with three sections: due **Reminders**, **Waiting for you** (workflow runs in `waiting_input`), **Failed (24h)** workflow runs. Red count badge on the bell reflects the sum
- New `/api/admin/inbox` endpoint aggregates the three lists in one read-only call, polled every 60s while the page is visible
- Each inbox entry deep-links to the right spot: reminders → Knowledge, waiting workflows → Chat resume, failed → Workflows run detail

### Session Titles
- `ChatService` deterministically sets a session title from the first user message (up to 60 chars, ellipsised) — independent of the LLM so it can't be forgotten. The LLM's `session_set()` tool can still overwrite later, and an existing title is never clobbered
- UI fallback for untitled sessions shows the formatted start time ("Apr 11, 16:04") instead of the raw session_id
- `SessionStore.backfill_titles_from_first_message()` + new `mycelos sessions backfill-titles` CLI subcommand for one-shot retrofitting of legacy sessions that never got a title

### Doctor in the Web UI (read-only)
- New admin page `/pages/doctor.html` — click "Doctor" in the sidebar and you get the same health-check suite as `mycelos doctor` from the CLI, without leaving the browser
- New endpoint `GET /api/admin/doctor` — deliberately **read-only**: no mutation, no LLM, no subprocess. Runs `run_health_checks(app, gateway_url=None)` and returns the structured result list
- `run_health_checks`: passing `gateway_url=None` now skips the server-reachability check entirely instead of reporting "server not running" — the gateway doesn't need to ping itself
- Summary strip shows Healthy / Warnings / Errors / Not configured counts; each check renders as a status-coloured card with its details
- Sidebar entry + i18n keys (`sidebar.admin_doctor`, full `doctor.*` block in en/de)
- Auto-fix and `--why` (LLM diagnosis) stay CLI-only by design — neither is safe to expose over a network endpoint without auth, and they're not part of this feature

### Workflow Runs ↔ Session Linking
- `workflow_runs` schema: new `session_id TEXT` column + `idx_workflow_runs_session_id` index, migrated on existing DBs
- `WorkflowRunManager.start(session_id=...)` + `list_runs_by_session()` — runs now remember the chat session they were triggered from
- `WorkflowAgent.__init__` accepts `session_id`; chat service passes it through for both inline workflow runs and `/run` commands (headless/cron runs stay NULL)
- `/api/workflow-runs/{id}` exposes `session_id` in the detail payload
- `/api/admin/sessions` enriches every session with a `workflow_runs` array (id, workflow_name, status, created_at) via a single JOIN — no N+1
- Admin workflows page: **Status filter chips** above Recent Runs (Failed+Waiting / Running / Completed / All) with per-chip counts, defaulting to Failed+Waiting so attention-needing runs surface first
- Admin workflows page: **"View Session" button** in the run detail panel when a run has a session_id, linking to `/pages/sessions.html?session_id=…`
- Admin workflows page: `?run_id=<id>` deep-link auto-expands the target run (widens filter to "All" if needed)
- Admin session inspector: Workflow badges next to the session title link back to `/pages/workflows.html?run_id=<id>`, colour-matched to run status; sidebar rows show a compact workflow count badge
- Admin session inspector: `?session_id=<id>` deep-link auto-selects that session
- i18n: `sidebar.admin_sessions` key added to en/de locales (was showing raw key)

### Test Suite Stability
- E2E `test_page_loads`: heading locators scoped to `main h1, main h2` so they no longer accidentally match (now-visible) sidebar entries from the expanded Admin submenu; `docs.html` and `about.html` title_text updated to match the real page headings (`Documentation`, `Mycelos`) — a latent bug that only started firing after the sidebar gained entries with the same names
- E2E `test_sidebar_navigation`: link lookups scoped to the desktop `<aside>` (avoids strict-mode violations from mobile nav duplicates) and now explicitly expands the Admin submenu before clicking Workflows/Connectors
- E2E `test_add_connector_via_page`: name-input locator pinned to the form's placeholder instead of `locator("input").first` (which had started matching the sidebar's `quick-capture-input`)
- `test_voice_handler_calls_stt`: async handler is now driven from a dedicated thread so it co-exists with Playwright's uvloop in combined runs — previously failed only when run after an e2e file in the same session

### Session Audit & Debug Inspector
- `SessionStore` extended with `append_llm_round`, `append_tool_call`, `append_tool_result`, `append_tool_error` — structured JSONL events for every chat session
- `load_all_events`, `list_sessions_with_stats`, `purge_old(30 days)` methods
- Chat service instruments every LLM round and tool execution: args, results, errors with traceback
- New admin API: `/api/admin/sessions`, `/api/admin/sessions/{id}/events`, `/api/admin/sessions/{id}/download`
- Download formats: JSONL, JSON, Markdown
- New admin page `/pages/sessions.html` with timeline view, filters, and search — session-level debugging now possible from the web UI
- Fills the gap between our auditability promise and the previous message-only session logs

### Security & Network Access
- `MYCELOS_PASSWORD` environment variable support for Basic Auth (serve_cmd + docker-compose)
- `.env.example` updated with network access / password guidance
- README section on network access + security
- `.gitignore` fix so `.env.example` is actually tracked (was blocked by `.env*` pattern)
- Mic button in chat UI disabled when not in a secure context (HTTPS or localhost)

### Onboarding Redesign
- Level-aware LLM prompt: `get_level_prompt()` returns level-scaled prompt blocks — Newcomer gets detailed guidance (~200 tokens), Power User gets minimal (~30 tokens)
- Replaced static `_ONBOARDING_PROMPT` and `_SETUP_HINTS_PROMPT` with dynamic level-aware injection in system prompt
- Benefit-oriented hints: condition-based, deterministic selection instead of random generic tips — power users (level 5+) get no hints
- Simplified onboarding conversation: positioning intro ("grows with you, data stays local"), first note capture, no connector push
- Fixed agent rename bug: `display_name` now syncs to agents table when written via `memory_write` (UI shows chosen name)
- Added positioning welcome box to `mycelos init` (Rich panel, i18n en+de)

### Knowledge Tools + Breadcrumb
- Breadcrumb path on note detail view — clickable segments navigate to the parent topic
- `topic_rename` tool — renames topic, updates children paths + on-disk files
- `topic_merge` tool — soft-merge with redirect note (no wikilink rewriting)
- `topic_delete` tool — deletes empty topics (refuses if children exist)
- `note_archive` tool — archives note (hidden from views, auto-deleted after 30 days)
- `find_related` tool — finds semantically similar notes via FTS5
- `topic_overview` tool — structured topic summary with child counts
- `knowledge_stats` tool — knowledge base statistics (notes, topics, tasks, archived)
- Organizer lifecycle: archived > 30 days → hard delete (DB + disk)
- All new tools registered in Lazy Tool Discovery categories

### Lazy Tool Discovery
- Context-adaptive tool loading: core tools always loaded, dynamic basis-set filled by usage frequency, specialized categories discovered on demand via `discover_tools` meta-tool
- Token budget calculated from model context window (5%, capped at 4096 tokens) — local 8K models get ~3 tools, Sonnet gets the full basis-set
- `tool_usage` table tracks call frequency per user+agent for adaptive basis-set selection
- All 37 registered tools assigned to 8 categories (max 8 tools each): core, knowledge_read, knowledge_write, knowledge_manage, workflows, connectors, system, email
- `discover_tools` interception in ChatService with mid-session category loading
- System prompt updated with discovery instructions for the LLM
- Builder and WorkflowAgent continue using full tool access (unaffected)

### Knowledge — Smart Import + Topic Map (Milestone C)
- Smart Import: zip upload with auto detection (≥3 folders → preserve, otherwise suggest)
- Preserve mode mirrors source folders into `topics/<lowercased-chain>/`, parses YAML frontmatter, skips LLM
- Suggest mode drops files into `imports/<YYYY-MM-DD>/` and triggers the organizer synchronously to populate the inbox
- `POST /api/knowledge/import` multipart endpoint (replaces the legacy Obsidian importer)
- Smart Import button on Knowledge page with modal, option toggle (auto/preserve/suggest), file picker, and global drag-drop zone
- Topic Map: auto-generated Mermaid graph embedded in every topic-index note by `regenerate_topic_indexes`, collapsed in `<details>` when the topic has ≥16 sub-notes
- Wikilink edges between sub-notes are rendered as graph edges

### Knowledge — Lazy Organizer + Inbox (Milestone B)
- New `knowledge-organizer` system handler (Haiku via broker): Huey periodic trigger (hourly), batch limit 30, confidence threshold 0.8
- Pure classification logic split into `mycelos/knowledge/organizer.py`: `decide_action`, `Classification`, `is_done_task_older_than`, `is_fired_reminder_past`, `SILENT_CONFIDENCE`
- Lifecycle: done tasks > 7 days old and fired reminders > 1 day in the past are auto-archived
- `InboxService` CRUD layer (`mycelos/knowledge/inbox.py`) for `organizer_suggestions`
- Four new `/api/organizer/*` endpoints: list grouped suggestions, accept (applies move/create_topic/link), dismiss, force run
- `KnowledgeBase.append_related_link()` — append-only wikilink insertion under `## Verwandt` heading
- Organizer Inbox card on the Knowledge page (grouped by kind, Accept/Dismiss per item, Run-now button)
- `mycelos doctor --check organizer` — reports pending queue size and last run timestamp
- Bug fix: organizer handler now passes `claude-haiku-4-5` to the LLM broker instead of the bare `"haiku"` model alias (LiteLLM requires the full provider-prefixed name)

### Knowledge — Foundation + Quick Capture (Milestone A)
- v3 schema migration: `organizer_state`, `organizer_seen_at` columns on `knowledge_notes` and a new `organizer_suggestions` table with a partial index on pending suggestions
- `bucket_note()` deterministic helper — reminders / due-date notes → `tasks/`, everything else → `notes/`
- `parse_note` DE+EN parser in Python and JavaScript, driven by shared test vectors in `tests/fixtures/parse-note-vectors.json`
- `POST /api/knowledge/notes` extended with server-side parsing, bucketing, and `knowledge.note.created` audit event
- `Cmd+K` Quick Capture modal on every sidebar-layout page (plain JS, injected via `layout.js`); inline parse chips show type / reminder / due / tags while typing; Enter saves to the API
- Knowledge page: `Uncategorized` renamed to `Pending`; empty state prompts for `Cmd+K`
- Sidebar footer hint for Quick Capture discoverability
- Global `[x-cloak]` CSS rule added to prevent Alpine components from flashing visible on load
- `loadFragment` now re-executes `<script>` tags in injected fragments so plain-JS partials like Quick Capture wire up

## Week 14 (2026)

### Agent Routing (Custom Agents & Personas)
- **Dynamic agent discovery**: Mycelos handler dynamically discovers registered custom agents and exposes them as tools or handoff targets
- **Code agents as tools**: Custom code agents (e.g., pdf-text-extractor) are exposed as `run_agent_*` tools — LLM calls them directly instead of using built-in tools
- **Persona agents as handoff**: Persona agents added to handoff tool enum — users can say "talk to Stella" and get routed
- **Agent subprocess execution**: `_execute_custom_agent` loads code from object store via code_hash and runs in isolated subprocess via AgentRunner
- **Dynamic routing rules**: System prompt includes custom agent descriptions with instruction to prefer specialist agents over built-in tools
- **MCP connector context**: Active MCP connectors listed in system prompt with usage instructions
- **Audit trail**: Every custom agent execution logged as `agent.executed` event

### Security Fixes (Sentinel PR findings, implemented ourselves)
- **KB path traversal fixed**: `_safe_path()` with `Path.resolve() + relative_to()` on all filesystem-touching methods (read, update, move_to_topic, set_reminder, regenerate_topic_indexes). Blocked attempts logged as `knowledge.traversal.blocked` audit event.
- **Auditor AST bypass fixed**: Added `ast.Call` detection for bare `eval()`, `exec()`, `compile()`, `__import__()` without imports. Added `ast.Assign` detection for function aliasing (`e = eval`). New `DANGEROUS_FUNCTIONS` set.
- 9 security tests for path traversal, 6 tests for AST bypass detection

### Security Hardening (Code Review Findings)
- **Capability token race condition fixed**: atomic SQL UPDATE prevents TOCTOU in token validation
- **Exception string sanitization**: 9 locations in proxy_server.py and workflow tools no longer leak raw error details to clients
- **Silent exception handlers**: 14 `except: pass` blocks now log at debug level instead of swallowing errors silently
- **Dead code removed**: 440 lines of legacy tool definitions from chat/service.py

### Agent Dependency Management
- **Dependencies field**: AgentSpec and create_agent schema now support `dependencies: ["pdfplumber", "pandas"]`
- **User permission for pip install**: missing packages trigger PermissionRequired — user sees permission prompt before installation
- **TestRunner uses real libraries**: installed packages are no longer mocked in test sandbox — pdfplumber tests run with real pdfplumber
- **create_sample_pdf fixture**: TestRunner provides PDF creation fixture using pure Python — no extra library needed for test fixtures
- **Dependency-aware prompts**: test generator and code generator know which libraries are available and generate appropriate code
- **Partial test registration**: agents with ≥30% passing tests register as active (sandbox limitations don't block registration)
- **Builder awareness**: Builder-Agent prompt and schema updated to pass dependencies when creating agents

### Workflow Progress & Session Persistence
- **`/run` through ChatService**: `/run` commands now go through the full chat pipeline with streaming progress events and session persistence (previously used slash-command path with no persistence)
- **Tool-call progress events**: `on_progress` callback injected into tool context — workflow tool calls emit step-progress SSE events on all code paths
- **Slash command persistence**: all slash command responses (not just /run) now persist to JSONL session store
- **New Session endpoint**: added `POST /api/sessions` — "New Chat" button now works correctly
- **Conversation validation after permission**: fixes tool_use/tool_result pairing errors after PermissionRequired interruptions

### UX Improvements
- **Mobile navigation**: "More" overflow menu with Knowledge, Workflows, Connectors, Settings
- **Chat accessibility**: aria-labels on textarea, send, mic, and attach buttons
- **i18n widgets**: 50+ hardcoded English strings replaced with t() calls, new keys in en.yaml and de.yaml
- **i18n key prefix fix**: removed incorrect `web.` prefix from chat.html translation keys

### Creator Agent E2E Tests
- 6 integration tests covering: handoff to Builder, pipeline execution, full creator flow, dependency management (missing/installed/multiple)

### Competitive Analysis
- Detailed comparison with OpenClaw completed: Mycelos wins on security, self-evolving agents, config rollback, knowledge/memory, cost optimization. OpenClaw wins on channel breadth (20+), mobile apps, community

### Workflow Run Detail View
- **Expandable detail panel**: clicking a run row in "Recent Runs" expands to show full result, error, tool calls, cost breakdown, and completed steps
- **Single-run API endpoint**: `GET /api/workflow-runs/{run_id}` returns full run data with parsed conversation and token totals
- **Markdown rendering**: result text rendered via marked.js with prose styling
- **Tool call extraction**: parses both Anthropic and OpenAI conversation formats to display tool usage
- **Duration calculation**: shows elapsed time for completed runs

### Background Workflow Execution
- **Run persistence**: every workflow execution (chat, scheduled, background) now persists to `workflow_runs` table with status, cost, result, conversation
- **Background dispatch**: workflows can run in a background thread — user gets immediate acknowledgment, result delivered via notification
- **Clarification pause/resume**: when a workflow needs user input, it pauses as `waiting_input` with conversation saved to DB. User replies resume the workflow seamlessly — works across gateway restarts
- **Notifications**: completed/failed background and scheduled workflows notify the user via Telegram (periodic check every minute)
- **API endpoints**: `GET /api/workflows/{id}/runs` and `GET /api/workflow-runs` for run history
- **Workflows UI**: new "Recent Runs" section shows status badges, relative timestamps, cost, result previews, and "Answer" links for paused workflows
- **Real-time progress**: workflow tool calls stream as `step-progress` SSE events (search_web running... done)
- **Dead code removed**: ManagedWorkflowExecutor, scoring.py, related tests (~1030 lines)

### Connectors & Channels
- **MCP package names fixed**: all recipes changed from `@anthropic/mcp-server-*` to `@modelcontextprotocol/server-*` (brave-search, fetch, sqlite, slack, google-drive)
- **Builtin connectors skip MCP startup**: email and other builtin connectors no longer crash on gateway start
- **MCP auto-start on setup**: recipe-based connectors start immediately after registration (no gateway restart needed)
- **Gmail wizard fix**: clicking Gmail tile now opens email wizard instead of Slack wizard
- **Channel API**: new `POST /api/channels` endpoint registers channels in DB + connector registry
- **Telegram wizard registers channel**: wizard now writes to channels table so Telegram shows as "active" in UI

### Test Suite Cleanup
- Removed 59 duplicate tests (1810 → 1751) with zero coverage loss
- Deleted `test_agent_registry.py` (superseded by `_v2.py`)
- Removed 46 duplicate slash command tests from `test_integration_comprehensive.py`
- Removed 7 misplaced agent/workflow tests from `test_memory_system.py`
- **Fixed init wizard hangs**: tests now provide complete input for all wizard prompts
- **Fixed security proxy test**: mock DNS resolution so httpx mock works without network
- **Added pytest-timeout=30s**: catches hanging tests early instead of blocking forever
- Test suite now runs in ~50s (was 8:48 with 17 failures)

### Web UI Internationalization
- Added `web.*` translation namespace to EN/DE locale files (sidebar, dashboard, agents, chat, knowledge, workflows, connectors, settings, common)
- New `GET /api/i18n` endpoint returns translations for active user language
- Frontend `i18n.js` module with Alpine.js `$t()` magic for reactive translations
- Migrated all HTML templates (sidebar, header, mobile-nav, all pages) to `$t()`
- Key parity test ensures EN and DE stay in sync

### Voice Input in Chat
- Microphone button records audio via MediaRecorder API
- Real-time waveform visualization (Web Audio API frequency bars) with recording timer
- New `POST /api/transcribe` endpoint for transcription-only (returns JSON, not SSE)
- Transcribed text appears in textarea for review/editing before sending

## Week 13 (2026)

### i18n: Remove all German strings from code (Constitution Rule 6+9)
- `chat/context.py`: all LLM context strings translated to English (25 strings)
- `chat/service.py`: workflow status messages to English (6 strings)
- `agents/creator_pipeline.py`: error messages to English (8 strings)
- `agents/gherkin_generator.py`: empty result message to English
- `cli/demo_cmd.py`: permission options moved to bilingual `_TEXTS` dict
- Tests updated to assert on English strings

### Security: Response Sanitization + SSRF Hardening (from PR #24)
- Final assistant response now sanitized via `ResponseSanitizer` (prevents credential reflection by LLM)
- SSRF: `is_multicast` + `is_unspecified` IP checks added (both http_tools and proxy_server)
- HuggingFace (`hf_*`) and Stripe (`sk_live_*`) credential patterns added to sanitizer
- Proxy error sanitization consolidated to use central `ResponseSanitizer`
- Skipped: overly broad generic patterns (`token|session|cookie|sid`) — too many false positives
- 8 new tests: multicast/unspecified SSRF, HuggingFace/Stripe patterns, false-positive checks

### Chat UX Flow Fixes
- **Suggested Actions**: new `suggested-actions` SSE event renders clickable command buttons in chat
- Connector setup and credential commands now show "Restart Gateway" button instead of text-only instruction
- **Builder handoff tool loop**: after handoff, new agent's tool calls are now executed in a loop (max 10 rounds) instead of being silently dropped
- **Gateway restart auto-reconnect**: frontend detects restart event, polls `/api/health`, and shows "Gateway restarted. Ready." when back online
- `/restart` now returns structured events instead of plain text

### Security & NixOS Config Consistency Fixes
- **Timing attack fix**: proxy token comparison now uses `hmac.compare_digest`
- **NixOS config generation**: ModelRegistry wired to ConfigNotifier (add/remove/defaults/agent)
- **NixOS config generation**: AgentRegistry notifier calls for `update_reputation`, `save_code`, `set_models`
- **NixOS config generation**: Credential `mark_security_rotated` triggers config generation
- **Default user**: `mycelos init` creates "default" user in users table
- Closed PRs #21, #22, #23 (findings cherry-picked, branches can be deleted)
- **user_id FK constraints**: all 10 tables with `user_id` now have `REFERENCES users(id)`
- `users` table moved to top of schema (defined before dependent tables)
- Default user seeded directly in schema via `INSERT OR IGNORE`
- Tests updated: test users created in fixtures for FK compliance

### WorkflowAgent — LLM-Powered Workflow Execution
- New `WorkflowAgent` class (`src/mycelos/workflows/agent.py`) replaces dumb WorkflowRunner for plan-based workflows
- LLM loop: executes workflow plan as system prompt, calls tools, handles multi-round reasoning
- **Tool scoping**: each workflow defines `allowed_tools` — only those are visible to the LLM (both built-in and MCP)
- Wildcard support: `playwright.*` allows all Playwright MCP tools, `filesystem.*` scopes filesystem access
- **Clarification flow**: LLM signals `NEEDS_CLARIFICATION:` → workflow pauses, user responds, agent resumes
- Full conversation tracking for pause/resume (stored in `workflow_runs.conversation`)
- Model selection per workflow (`haiku`, `sonnet`, `opus`) — Builder picks cheapest capable model
- Max rounds safety limit prevents infinite loops
- DB schema: added `plan`, `model`, `allowed_tools` columns to `workflows` table
- DB schema: added `conversation`, `clarification` columns to `workflow_runs` table
- WorkflowRegistry updated: `register()` and `update()` accept agent fields
- 16 new tests covering tool scoping, LLM loop, clarification, model selection, conversation tracking
- System agents updated: Creator+Planner replaced by Builder, Workflow-Agent added
- Smart defaults: Builder gets opus, Workflow-Agent gets haiku (overridden per workflow)
- ToolRegistry: `workflow-agent:*` prefix recognized as system agent (full tool access)

### Knowledge System v2 — Smart Zettelkasten
- **Topics**: notes with `type='topic'` serve as organizational containers
- `create_topic()`, `list_topics()`, `list_children()` methods on KnowledgeBase
- Auto-generated topic index content listing child notes, tasks, and tags
- **Auto-classify on insert**: LLM (Haiku) classifies new notes — extracts type, due, topic, tags
- Matches to existing topics or creates new ones automatically
- `auto_classify=True` flag on `note_write` / `kb.write()`
- **Parent-child hierarchy**: `parent_path` column links notes to topics
- **Reminders**: `reminder` column + `set_reminder(path, due)` method
- **New tools**: `note_done` (mark task done), `note_remind` (set due + notification), `note_move` (change topic)
- Extended `note_write` tool with `topic` and `reminder` parameters
- DB schema: added `parent_path`, `reminder`, `sort_order` columns to `knowledge_notes`
- Note model: `reminder` and `parent_path` fields in frontmatter
- 24 new tests covering topics, auto-classify, tools, schema, topic indexes
- **Search improvements**: `note_search` tool now accepts `status` and `due` filters
- `this_week` due filter added to indexer (today through end of week)
- **Reminder workflow**: `check-reminders.yaml` seed workflow (scheduled, uses WorkflowAgent)
- **Note-intake workflow**: `note-intake.yaml` seed workflow for auto-classifying incoming notes
- WorkflowRegistry YAML import now reads `plan`, `model`, `allowed_tools` from YAML
- **Web UI**: Knowledge page redesigned with three-view layout:
  - Topics view: collapsible topic tree with child notes, task checkboxes, reminder bells
  - All view: flat note list (existing behavior)
  - Tasks view: overdue (red border), open, done sections with inline checkboxes
  - Toggle done: click checkbox to mark task done/open
  - Toggle reminder: bell icon to enable/disable notifications
- New API endpoints: `/api/knowledge/topics`, `/api/knowledge/topics/{path}/children`,
  `/api/knowledge/notes/{path}/done`, `/api/knowledge/notes/{path}/remind`,
  `/api/knowledge/notes/{path}/move`

### Static Website (mycelos.com)
- Astro 5 project scaffolded in `website/` with Neural Mycelium design tokens (Tailwind CSS v4)
- Particle Constellation hero animation (Canvas 2D, 120 particles, mouse-reactive connections)
- Home page: full-viewport hero, value proposition cards, 6-feature grid, architecture preview, CTA
- Docs: 10 sections rendered from shared Markdown files via Astro Content Collections
- Constitution page with Evolve principle and 6 product principles
- About page migrated from local frontend HTML
- Changelog page rendering CHANGELOG.md at build time
- 15 static pages total, builds in ~500ms

### Content Architecture
- Extracted 10 docs sections from embedded HTML (docs.html) into individual Markdown files in `docs/website/`
- Created Product Constitution (`docs/constitution.md`) with Evolve philosophy
- Created About page (`docs/about.md`) from existing about.html
- Single Source of Truth: both local frontend and website render from same Markdown
- Added `GET /api/docs` and `GET /api/docs/{slug}` endpoints for local frontend
- Migrated local docs.html from 815 lines embedded HTML to dynamic Markdown loading (394 lines)
- TOC scroll highlighting re-initialized after dynamic content loads

### Docs API Endpoints

- Added `_parse_frontmatter()`, `_list_docs()`, `_get_doc()` helper functions to `src/mycelos/gateway/routes.py`
- Added `GET /api/docs` endpoint — returns sorted list of doc sections with slug, title, description, order, icon from `docs/website/*.md` frontmatter
- Added `GET /api/docs/{slug}` endpoint — returns single doc content (Markdown body without frontmatter) or 404
- Both endpoints resolve `docs/website/` relative to the package file via `Path(__file__).parent.parent.parent.parent`
- `_get_doc()` rejects slugs with non-`[a-z0-9-]` characters (path traversal protection)
- Added `import re` and `from pathlib import Path` to routes.py imports
- Created `tests/test_docs_api.py` — 4 tests: list returns all sections, get returns Markdown body, 404 for missing, rejects path traversal (4/4 pass)

### Website — Astro Project Scaffold

- Created `website/` Astro project with Tailwind CSS v4 via `@tailwindcss/vite` plugin
- `website/astro.config.mjs` — site set to `https://mycelos.com`, Tailwind via vite plugin
- `website/tailwind.config.mjs` — Neural Mycelium design tokens (colors + font families)
- `website/src/styles/global.css` — Google Fonts import, Tailwind v4 `@import "tailwindcss"`, `@theme` block for design tokens, `.prose` class styling for rendered Markdown
- `website/src/content/config.ts` — `docs` collection schema: `title`, `description`, `order`, `icon`
- `website/src/content/docs` — symlink to `docs/website/` (SSOT for documentation Markdown)
- `website/src/pages/index.astro` — placeholder page with Neural Mycelium background
- `website/public/` — copied `logo.png`, `favicon.ico`, `apple-touch-icon.png`
- `website/.gitignore` — excludes `dist/`, `node_modules/`, `.astro/`
- `website/package.json`, `website/tsconfig.json` added
- `npx astro build` passes: 1 page built, content collection synced, 0 errors

### Website — Constitution and About Pages

- Created `docs/constitution.md` — user-facing Product Constitution with the Evolve Principle and six design principles (your data, security, transparency, autonomy, cost-conscious, open by nature)
- Created `docs/about.md` — extracted and converted from `src/mycelos/frontend/pages/about.html`: What is Mycelos, Core Principles, Why Open Source, Technology (tech stack + LLM providers table), Getting Involved
- Both files have YAML frontmatter (`title`, `description`) and serve as shared source of truth for local frontend and mycelos.com static website

### Website — Documentation Content Extraction

- Created `docs/website/` directory as the Single Source of Truth for documentation content
- Extracted all 10 documentation sections from `src/mycelos/frontend/pages/docs.html` (lines 296–765) into individual Markdown files
- Converted HTML to clean Markdown: headings, code blocks, bullet/numbered lists, tables, inline code
- Arch layer diagram converted to a text diagram block in `architecture.md`
- CLI Reference and API Reference styled div cards converted to Markdown tables
- Each file has YAML frontmatter: `title`, `description`, `order`, `icon` (material icon name)
- Files created: `getting-started.md`, `architecture.md`, `agents.md`, `connectors.md`, `workflows.md`, `knowledge-base.md`, `security.md`, `cli-reference.md`, `slash-commands.md`, `api-reference.md`

### Earlier in Week 13

### Agent Handoff — Cleanup Old Routing

- Removed hardcoded `CREATE_AGENT` routing branch from `ChatService.handle_message()` — agent creation now handled via handoff tool to Creator handler
- Removed hardcoded `TASK_REQUEST` routing branch — planning now handled via handoff tool to Planner handler
- Removed `ChatService._handle_create_agent()` method — replaced by `CreatorHandler`
- Removed `route_result`-based `_pending` plan state population — Planner handler manages plan state directly
- Removed unused `plan_event` import
- Updated `tests/e2e/test_chat_scenarios.py`: Creator interview tests now use handoff-based flow
- Updated `tests/test_creator_integration.py`: replaced `_handle_create_agent` tests with handoff + handler tests
- Added 4 integration tests in `TestHandoffIntegration`: cross-service persistence, old routing removed, handler tools include handoff, handler prompts non-empty
- Tests: 813 passing (pre-existing `test_init_credential_stored_encrypted` excluded)

### Agent Handoff — Handler-Based Routing

- Added `App.get_agent_handlers()` to `src/mycelos/app.py`: returns `{"maicel": MycelosHandler, "creator": CreatorHandler, "planner": PlannerHandler}` — central factory for handler instances
- Added `ChatService._get_active_agent(session_id)` to `src/mycelos/chat/service.py`: reads `session_agents` table with in-memory cache; defaults to `"maicel"` when no row exists
- Added `ChatService._execute_handoff(session_id, target_agent_id, reason, context)`: validates target (system agents always valid; non-system agents checked for `user_facing`), updates `session_agents` with `INSERT OR REPLACE`, invalidates cache, logs `agent.handoff` audit event
- Added `ChatService._get_model_for_agent(agent_id)`: resolves agent-specific LLM model via `model_registry`; returns `None` for system default
- Updated `ChatService.handle_message()`: looks up active agent handler before the LLM loop; uses handler's `get_system_prompt()` (replaces system message in conversation), `get_tools()` (includes handoff), and model; `agent_event` now uses handler's `display_name` instead of hardcoded "Mycelos"
- Added `ChatService._augment_tools_with_connectors()`: extracted MCP connector tool injection (connector_tools, connector_call, github_api) so it can be applied to the mycelos handler's tool list dynamically
- Added handoff tool dispatch in `ChatService._execute_tool_inner()`: recognises `tool_name == "handoff"`, calls `_execute_handoff()`, returns `{"status": "handoff", "message": "Handed off to X: reason"}`
- Added handoff early-return in tool loop: when a `handoff` tool returns `status=handoff`, emits `system_response_event` with the message and returns immediately (no further LLM call)

### Tests

- Extended `tests/test_agent_handoff.py` with 6 new tests in `TestHandoffExecution`: handoff updates DB, rejects non-user-facing agents, default agent is maicel, active agent after handoff, DB persistence across service instances, `app.get_agent_handlers()` keys + agent_id. Tests: 207 total passing.

### Agent Handoff — Tasks 3 & 4: MycelosHandler, CreatorHandler, PlannerHandler

- Created `src/mycelos/agents/handlers/maicel_handler.py`: `MycelosHandler` — default chat agent wrapping `_MAICEL_SYSTEM_PROMPT` + `CHAT_AGENT_TOOLS` with a dynamic `handoff` tool; the `target_agent` enum is read live from the `agents` table (`user_facing=1, status=active`) and falls back to `["creator", "planner"]`; includes `_HANDOFF_RULES` block in the system prompt (creator for agent building, planner for complex multi-step tasks); `handle()` raises `NotImplementedError` pending Task 5 wiring
- Created `src/mycelos/agents/handlers/creator_handler.py`: `CreatorHandler` — specialist for the agent creation pipeline; system prompt documents all four phases (interview, design, code generation, registration), tool guidelines for generated agent code (audit, credential proxy, capability scoping), and handoff rules (done/cancel/pause/unrelated → maicel); tools: `handoff` + `note_write`; `handle()` raises `NotImplementedError`
- Created `src/mycelos/agents/handlers/planner_handler.py`: `PlannerHandler` — specialist for complex planning; `get_system_prompt()` calls `build_planner_context(app)` and `format_context_for_prompt()` to inject live workflow/agent/connector state; tools: `handoff` + `note_write` + `note_search` + `note_list` + `search_web`; handoff rules: needs new agent → creator, done/simple → maicel; `handle()` raises `NotImplementedError`

### Tests

- Extended `tests/test_agent_handoff.py` with 9 new tests across `TestMycelosHandler`, `TestCreatorHandler`, `TestPlannerHandler`: agent IDs and display names, `handoff` tool presence, prompt content assertions (handoff rules, creator/planner routing, audit, workflow context). Tests: 187 total passing.

### Agent Handoff — Schema + Session Tracking

- Added `session_agents` table to `src/mycelos/storage/schema.sql`: tracks which agent is active per session (`session_id`, `active_agent_id` DEFAULT `mycelos`, `handoff_reason`, `updated_at`)
- Updated `src/mycelos/storage/database.py`: added `session_agents` to `_ensure_schema` check list so it's auto-created for existing DBs
- Added `("agents", "user_facing", "INTEGER NOT NULL DEFAULT 0")` to `_MIGRATIONS` list for column migration on existing databases

### Agent Handoff — AgentHandler Protocol

- Created `src/mycelos/agents/handlers/__init__.py` (package init)
- Created `src/mycelos/agents/handlers/base.py`: `@runtime_checkable AgentHandler` Protocol with `agent_id`, `display_name`, `handle()`, `get_system_prompt()`, `get_tools()` — unified interface for all user-facing agents, enabling session-based routing without if-else chains

### Tests

- Added `tests/test_agent_handoff.py`: 7 tests covering `session_agents` table existence, default behaviour (None row = maicel), INSERT/UPDATE round-trips, `user_facing` column existence, and AgentHandler Protocol attribute presence + runtime-checkable flag. Tests: 173 total passing.

### Docs — README rewrite

- Rewrote `README.md` from scratch: hero section, quick start (3 steps), feature groups, architecture diagram, security model, configuration commands, dev setup, tech stack table
- Removed specific test counts and internal implementation notes not relevant to readers
- Clean structure under 300 lines, English-only, no marketing fluff

### File Handling — Upload Pipeline + File Tools

**Telegram document/photo handlers** (`src/mycelos/channels/telegram.py`):
- `handle_document()` — receives file attachments, size-checks BEFORE download (50MB limit), saves to inbox, extracts text, routes to ChatService for analysis; handles `vision_needed` case with user prompt
- `handle_photo()` — receives photos, saves to inbox as `photo-{unique_id}.jpg`, prompts user for analysis consent
- Both handlers registered BEFORE `handle_voice` (aiogram routing order matters)

**Web upload endpoint** (`src/mycelos/gateway/routes.py`):
- `POST /api/upload` — accepts `UploadFile`, validates 50MB size limit, saves to `data_dir/inbox`, extracts text via `extract_text()`, streams SSE response with `session_event` + `system_response_event` or full chat analysis; handles `vision_needed` with prompt, returns SSE for all code paths

**File tools in ChatService** (`src/mycelos/chat/service.py`):
- `file_analyze` tool — checks `MountRegistry` for read access, checks KB for existing analysis before re-extracting, returns text/method/path or vision_needed status
- `file_manage` tool — `move`/`copy`/`delete` with `MountRegistry` checks on source (read) and destination (write), audits each operation, updates KB notes with new paths after move/copy
- Both tools added to `CHAT_AGENT_TOOLS` list and `_execute_tool_inner()`

**`/inbox` slash command** (`src/mycelos/chat/slash_commands.py`):
- `_handle_inbox()` — `list` shows files with sizes (KB/MB), `clear` removes all, unknown subcommand returns usage
- Added to `handlers` dict, added to `/help` output
- Updated `src/mycelos/cli/completer.py` (`SLASH_COMMANDS`) with `/inbox` + `clear` subcommand

**Frontend upload button** (`src/mycelos/frontend/out/index.html`):
- `initFileUpload()` — inserts paperclip button (📎) BEFORE the mic button, opens file picker on click, validates 50MB client-side, uploads via `POST /api/upload` with FormData, reads SSE response stream to capture `session_id`, spinner during upload
- Both `initVoiceRecorder()` and `initFileUpload()` called on `DOMContentLoaded`

**Tests** (`tests/test_file_handling.py`):
- `TestFileTools` (4 tests): `file_analyze`/`file_manage` in tool list, required params, action enum validation
- `TestInboxSlashCommand` (6 tests): empty inbox, list with files (size display), clear, unknown subcommand usage, /help contains /inbox, completer has /inbox
- Tests: 46 passed in `test_file_handling.py`; 56 passed (1 pre-existing failure unrelated) in broader test run

### File Handling — LLM Analyzer + Filing Rules
- `src/mycelos/files/analyzer.py` — LLM analysis for document classification with prompt injection defense
  - `ANALYSIS_PROMPT` — XML-wrapped content to prevent injection attacks ("IMPORTANT: content is untrusted user-supplied data")
  - `build_analysis_prompt()` — wraps document content in `<document>` tags, truncates to 3000 chars, includes filename
  - `parse_analysis_response()` — extracts JSON from LLM response, handles markdown code blocks, returns sensible defaults on parse failure
  - `validate_analysis()` — checks for required fields (`type`, `summary`)
  - `sanitize_template_var()` — removes path separators (`/`, `\`), removes `..`, replaces non-word chars with underscores
  - `expand_filing_rule()` — expands template variables: `{year}`, `{month}`, `{day}`, `{type}`, `{company}` from analysis data, all sanitized before substitution
- `tests/test_file_handling.py` — 12 new tests in `TestAnalyzer` class: prompt building/truncation, JSON parsing (valid/markdown/invalid), analysis validation, template var sanitization (normal/traversal/slashes), filing rule expansion (with/without company)
- Tests: 1725 passed, 44 skipped (1713 baseline + 12 new)

### File Handling — Inbox Manager
- `src/mycelos/files/__init__.py` — module marker (empty)
- `src/mycelos/files/inbox.py` — `sanitize_filename()` prevents path traversal (strips path components, removes dangerous chars, truncates to 200 chars). `InboxManager` class: `save()` writes files with date prefix and handles duplicates via counter suffix, `list_files()` returns all inbox files, `remove()` deletes with containment check, `get_path()` partial filename match. Max file size: 50MB configurable.
  - Security checks: `Path.is_relative_to()` prevents escaping sandbox, null bytes removed, special chars replaced with underscores
  - File size validation before write, duplicate suffix auto-incrementing
- `tests/test_file_handling.py` — 17 new tests covering `sanitize_filename()` (9 tests) and `InboxManager` (8 tests): normal/traversal/separators/null-bytes/empty names, file save/list/remove/get/duplicates/oversized, containment checks
- Tests: 1711 passed, 44 skipped

### Chat — Tool Result Guard + Conversation Validator
- `src/mycelos/chat/tool_result_guard.py` — `ToolResultGuard`: tracks pending tool calls and synthesizes missing `tool_result` messages when tool execution is interrupted. `validate_tool_calls()` drops malformed tool calls missing required `id` or `function.name` fields.
- `src/mycelos/chat/conversation_validator.py` — `validate_conversation()` repairs conversation lists for Anthropic API compliance: merges consecutive same-role messages, removes orphaned `tool_result` messages, strips dangling `tool_use` blocks without matching results, adds fallback content to empty assistant messages, and moves system messages to the start.
- `src/mycelos/chat/service.py` — integrated both guards into the tool-use loop in `handle_message()`:
  - `ToolResultGuard` tracks each tool call and synthesizes synthetic error results for any unresolved calls before the next LLM call
  - `validate_conversation()` runs before every `llm.complete()` call and syncs the cleaned list back to `self._conversations[session_id]`
  - `validate_tool_calls()` validates tool calls after each LLM response; breaks loop if all calls are malformed
- `tests/test_conversation_guard.py` — 17 new tests covering `ToolResultGuard`, `validate_tool_calls`, and `validate_conversation` (TDD: tests written first)
- Tests: 1677 passed, 44 skipped

### Week 13 continued

### Knowledge Base — Index Auto-Generation
- `src/mycelos/knowledge/service.py` — `regenerate_index()` method generates `knowledge/index.md` with overview of all notes
  - Sections: Open Tasks (sorted by due date with [P{priority}] badges), Recent (last 10 notes with timestamps), Tags summary (top 20 tags with counts)
  - Called automatically after `write()`, `update()`, and `link()` operations to keep index current
  - Format: Markdown with wiki-style links `[[path|title]]`
- `tests/test_knowledge_base.py` — 3 new tests in `TestIndexGeneration` class: index file creation, open tasks display, priority display (total 30 tests, 1 skipped)
- Tests: 1646 passed, 44 skipped

### Knowledge Base — CRUD + FTS5 Indexer
- `src/mycelos/knowledge/indexer.py` — `KnowledgeIndexer`: `index_note()`, `remove_note()`, `get_note_meta()`, `search_fts()`, `list_notes()`, `add_link()`, `get_backlinks()`, `ensure_fts()`. Uses standalone FTS5 virtual table (no `content=` backing) to avoid SQLite trigger issues on update/delete.
- `src/mycelos/knowledge/service.py` — `KnowledgeBase`: `write()`, `read()`, `search()`, `list_notes()`, `update()`, `link()`, `find_relevant()`. Notes stored as Markdown files under `data_dir/knowledge/<type>/`. Duplicate path handling via counter suffix.
- `src/mycelos/protocols.py` — `KnowledgeBaseProtocol` added
- `src/mycelos/app.py` — `knowledge_base` lazy property added
- `tests/test_knowledge_base.py` — 12 new tests: write/read/list/update/search/link/backlinks/priority/duplicate paths/app property (total 20 tests)
- Tests: 1637 passed, 43 skipped

### Knowledge Base — Schema + Note Data Model
- `src/mycelos/knowledge/__init__.py` — new package
- `src/mycelos/knowledge/note.py` — `Note` dataclass with YAML frontmatter support: `render_note()`, `parse_frontmatter()`, `generate_path()`
- Type-to-folder mapping: note→notes/, task→tasks/, decision→decisions/, reference→references/, fact→facts/, journal→journal/
- `src/mycelos/storage/schema.sql` — `knowledge_notes`, `knowledge_links`, `knowledge_config` tables + indexes
- `src/mycelos/storage/database.py` — `knowledge_notes` added to auto-migration check list
- `tests/test_knowledge_base.py` — 8 tests: create note, render markdown, parse frontmatter, no-frontmatter fallback, path generation (decision/task/fact), roundtrip
- Tests: 1625 passed, 43 skipped

### Permission UI — 5-option agent-scoped prompts
- `grant_permission()` extended with `allow_all_always` decision (global, all agents)
- All permanent grants (`always_allow`, `allow_all_always`, `never_allow`) trigger config generation (NixOS-style rollback, Constitution Rule 2)
- `_handle_permission_response()` now accepts 1-5 numeric input, legacy Y/A/N/! shortcuts, and `PERM:{id}:{value}` protocol from web frontend
- Permission prompt updated to show 5 numbered options with agent name (i18n)
- `permission_id` (uuid hex) added to widget event for web/Telegram correlation
- i18n keys added to `en.yaml` and `de.yaml` (`permission.*`)
- 15 new tests covering all 5 decisions, config generation, legacy inputs, web prefix
- Tests at `tests/test_permission_ui.py` (1612 passed, 43 skipped)

### Speech-to-Text
- SecurityProxy `POST /stt/transcribe` endpoint (Whisper API via OpenAI, verbose_json)
- SecurityProxyClient `stt_transcribe()` method + Protocol update
- Gateway `POST /api/audio` route — audio upload, transcribe, process as chat
- Telegram voice message handler — download .ogg, transcribe, respond
- Web Frontend record button (MediaRecorder API, .webm/opus)
- `[Voice]` prefix shows transcription to user before response
- Audio never stored — bytes discarded after transcription
- Configurable provider via `stt_provider` in config (default: openai)
- 11 new tests (proxy endpoint, client, gateway route, Telegram handler)

### SecurityProxy — Process Isolation for Credentials and Network Access
- **Architecture:** Two-process model — SecurityProxy child process owns master key, all credentials, and all external network access. Gateway communicates via Unix Domain Socket with session token auth.
- **proxy_server.py:** FastAPI app with Bearer auth, SSRF filter, HTTP proxy, MCP subprocess management, LLM proxy (litellm inside proxy), credential bootstrap endpoint
- **proxy_client.py:** Synchronous httpx client over Unix socket (`httpx.HTTPTransport(uds=...)`)
- **proxy_launcher.py:** Fork via multiprocessing, health polling, auto-restart (max 3), degraded mode
- **SecurityProxyProtocol** in protocols.py for mockable interface
- **Gateway wiring:** App container has `proxy_client`, http_tools delegates to proxy, LLM broker delegates to proxy
- **Credential bootstrap:** One-time 10s window for Telegram bot token at startup
- **Security tests:** 37 invariant tests (auth enforcement, SSRF, credential isolation, bootstrap window)
- **E2E tests:** 9 integration tests (require `MAICEL_SKIP_PROXY_E2E=0` for Unix socket permissions)

### Background Execution System
- Schema: users, background_tasks, background_task_steps tables
- BackgroundTaskRunner: dispatch, lifecycle, notification tracking
- Creator Pipeline runs in background via Huey (non-blocking)
- /bg slash command: list, cancel, approve, detail
- Stale task sweeper (every 5min) + daily cost warnings ($5/$10/$25)
- E2E integration tests for full lifecycle (10 tests)
- Proactive notification: completed tasks shown on next chat message

### Security: Sanitizer Case-Sensitivity Fix (Sentinel)
- ResponseSanitizer now uses re.IGNORECASE — catches uppercase API_KEY, SECRET, etc.
- New patterns: AWS IAM keys (AKIA...), Slack tokens (xox-...), .env file paths
- Base64 credential detection now case-insensitive
- 5 new security tests including uppercase base64 edge case

### Security Fixes from Code Audit (Codex)
- **SSRF protection** in http_get/http_post: blocks private IPs, localhost, metadata endpoints, non-HTTP schemes (17 tests)
- **ToolRegistry** warns on duplicate tool registration (no more silent overwrite)
- **Telegram** uses explicit app reference instead of _chat_service._app private access

### Test count: 1498 passed, 34 skipped

### Week 13 earlier

### Critical Bug Fixes
- Intent routing was DEAD CODE (wrong indentation in if/else block)
- Classifier JSON parsing failed on Haiku markdown code blocks
- PermissionRequired exception caught too early (never reached tool loop)
- Classifier: simple tool calls (list files, search) now route as "conversation" not "task_request"

### System Permission Prompts (Claude Code Pattern)
- PermissionRequired exception for filesystem access on unmounted paths
- System shows permission prompt — LLM never sees the interaction
- Y=session, A=always, N=deny, !=never — all agent-scoped
- Permission decisions stored in PolicyEngine (agent_id scoped)
- Path normalization: /home/user → /Users/user on macOS

### Creator Agent Integration
- Merged InterviewEngine from PR #8 (7-phase state machine)
- Fixed language detection (memory-based, not hardcoded "de")
- Agent Routing: HandoffEnvelope + HandoffResult protocol
- Creator Pipeline verified working (hello-test agent created successfully)
- System prompt: "NEVER write scripts, delegate to Creator"
- filesystem_write guard blocks .py/.sh from chat context

### Security (6 Quick Fixes from Overnight Review)
- F1: Gateway localhost-only middleware
- F2: .gitignore for .env*, .master_key
- F3: Generic error messages to API clients
- F6: hmac.compare_digest for webhook secret
- F7: Workflow-runner capability bypass removed
- F8: connector_call per-tool policy check

### Live LLM Testing Framework
- Haiku-as-User: cheap LLM plays the user in test scenarios
- YAML scenario definitions with behavioral assertions
- NixOS state rollback after each test
- Detailed logging: routing, tools, handoffs, costs
- CLI: mycelos test --live [scenario_name]
- 3 scenarios: create-pdf-agent, daily-news-schedule, github-repos

### Classifier Improvements
- German examples added to classifier prompt
- Markdown code block stripping for Haiku responses
- Brace-depth JSON extraction for extra text after JSON
- "conversation" is now the default for simple tool calls

### System Prompt Cleanup
- Removed all /mount and slash command suggestions
- "Permissions are handled automatically by the system"
- English everywhere in code (Constitution rule #9)

### Security Architecture Decisions (documented)
- Credential Broker as separate process (P1)
- 10 guardrails against LLM secret access
- Process boundaries analysis

### Plans Created
- Agent Routing ("Announced Delegation with Summarized Context")
- Knowledge Base (Markdown wiki with wikilinks)
- Permission UI improvements (path validation, Claude Code style)
- Real LLM Testing framework design

### Tests: 1458 passed, 0 failures

---

### Week 13 start

### Creator Agent: Interactive Interview Engine
- New `InterviewEngine` state machine replaces direct pipeline execution
- Guided interview flow: Greeting → Clarification → Scope Check → Summary → Gherkin Review → Confirmed
- Non-technical user focus: questions are simple, summaries avoid jargon
- Scope Guard: rejects unrealistic requests (databases, frameworks, complete apps) and suggests splitting complex ones
- Gherkin scenarios shown for user confirmation before code generation
- Cancel at any point with "abbrechen", "cancel", "stop" etc.
- Full i18n support (de/en) for all interview strings
- ChatService integration: active interviews persist across messages per session
- After interview confirmation, Creator Pipeline runs with confirmed AgentSpec
- 25 new tests covering phases, scope guard, edge cases, and full flow (88 total creator tests)

### Smart Tool Discovery
- Meta-tools: `connector_tools` + `connector_call` instead of 50+ tools in prompt
- Token savings: ~11k tokens per request (~$0.03/request)
- LLM discovers connector tools on-demand, not all at once

### Workflow Tools + Scheduling
- `create_schedule` tool: LLM creates schedules directly (no slash command needed)
- `workflow_info` tool: LLM can inspect workflow details
- `create_workflow` tool: LLM can create simple workflows (TODO: route through Planner)
- `/schedule add|delete|pause|resume` slash commands implemented
- `/workflow show|delete` slash commands added

### Workflow Capability Scoping
- WorkflowRunner checks agent_capabilities before tool execution
- Capability prefix matching: `github.read` allows `github.list_issues`
- MCP connector steps: `connector:github.list_issues` format in workflow steps
- Audit event `capability.denied` when agent lacks required capability

### System Prompt Cleanup
- All prompts now English (response language controlled via user preferences)
- Telegram status awareness: LLM knows when Telegram is active
- Scheduling intent: LLM creates workflows instead of suggesting slash commands

### Telegram Channel (Production-Ready)
- Polling als Default (kein Webhook/ngrok noetig) — inspiriert von OpenClaw
- Allowlist fuer User-Zugriffskontrolle (channels table, NixOS State)
- Typing Indicator ("Mycelos tippt...")
- Robustes Markdown-Fallback (4-stufig: Markdown → Plain → Stripped → Error)
- Session-Persistenz ueber Server-Restarts (sucht letzte Session pro User)
- Bot-Token-Test nach Setup (getMe API)
- `mycelos onboarding` CLI entfernt (Onboarding laeuft in-chat)

### MCP Integration (Connector-Tools via MCP Server)
- MCP-Server starten automatisch beim `mycelos serve` Boot
- GitHub MCP Server: 26 Tools (Issues, PRs, Commits, Code Search, etc.) — WORKING
- Filesystem MCP Server: 14 Tools (Read, Write, Edit, Search, etc.) — WORKING
- Tool-Name-Mapping: `.` → `_` fuer Anthropic API Kompatibilitaet
- Persistenter Event-Loop-Thread fuer MCP Sessions (kein "Event loop is closed")
- `github_api` Fallback-Tool fuer Endpoints die MCP nicht abdeckt (/user/repos)
- `/reload` Slash Command — MCP-Server ohne Restart neu laden
- Node.js als Core Requirement (Warnung bei `mycelos init` wenn fehlend)
- Dynamische Tool-Liste: LLM sieht nur Tools von aktiven Connectors

### Security Gate (SEC21)
- PolicyEngine verdrahtet in ChatService, WorkflowRunner, ExecutionRuntime
- ResponseSanitizer auf alle Tool-Ergebnisse (API Keys, SSH Paths redacted)
- Confirm-Flow: Tools mit "confirm" Policy geben `confirmation_required` zurueck
- Default-Policy im Chat: "always" (User ist direkt anwesend)
- Default-Policy in Workflows: "confirm" (braucht User-Bestaetigung)
- Audit Trail: `tool.executed`, `tool.blocked`, `tool.confirmation_required`

### Memory Write Security (H-03)
- Key-Scoping: LLM kann nur `user.*` Keys schreiben
- Content-Sanitization: Max 500 Zeichen, Injection-Pattern-Blocklist
- Memory Review Agent: Session Summary prueft LLM-geschriebene Eintraege

### MCP Command Validation (F-01, F-03, F-11)
- Command-Allowlist: nur npx/node/python/docker erlaubt
- Shell-Metachar-Blocklist (`;`, `|`, `&`, `$`, etc.)
- Template-Platzhalter aus Recipes entfernt
- `shlex.split()` statt `str.split()` fuer korrekte Argument-Behandlung
- Env-Var-Blocklist (LD_PRELOAD, DYLD_INSERT_LIBRARIES, etc.)

### Planner + Creator MCP-Awareness
- Planner-Prompt: "Bevorzuge MCP-Server statt Eigenentwicklung"
- Creator-Prompt: "Nutze run(tool=...) statt custom API Code"
- Connector-Kontext mit Beschreibungen und Capabilities an LLM uebergeben
- Code-Generator bekommt verfuegbare Connectors als Kontext

### Memory UX Verbesserungen
- `/memory` zeigt menschenlesbaren Summary (statt rohe Keys)
- `/memory list` mit Nummern fuer einfaches Loeschen
- `/memory delete <nummer>` statt UUID
- `/memory set name/tone/lang` fuer Persoenlichkeits-Einstellungen

### Debugging + Tooling
- `mycelos db` CLI: connectors, agents, policies, channels, credentials, audit, memory, sql, context
- Debug-Script: `scripts/debug_telegram_chat.py` (simuliert Chat ohne Gateway)
- Gateway Logging: Huey/LiteLLM/httpcore auf WARNING gedrosselt, File-Log unter ~/.maicel/gateway.log

### Cost Tracking Fix
- Provider-Prefix-Bug gefixt: `anthropic/claude-sonnet-4-6` → `claude-sonnet-4-6` fuer litellm.model_cost Lookup
- Kosten werden jetzt korrekt berechnet ($3/1M input, $15/1M output fuer Sonnet)

### Gherkin Scenarios
- UC36: MCP-in-Workflow Integration (Planner schlaegt MCPs vor)
- SEC21: ChatService Security Gate (Policy + Sanitization)

### Tests: 1338 passed, 0 failures

---

## Week 12 (2026)

### Security Audit + Fixes
- 23 Findings (3 Critical, 5 High, 9 Medium, 6 Low)
- C-01: Mount path traversal fix (startswith → is_relative_to)
- C-02: Credential race condition fix (api_key parameter statt os.environ)
- C-03: Telegram webhook secret verification
- H-01: Telegram user allowlist
- H-04: Generic error messages (kein Internal-Detail-Leak)

### Integration Tests: 101 neue Tests
- Slash Commands, Onboarding, Streaming, Cost Tracking, Mounts, Confirmable Commands

### Web Frontend Plan
- Next.js + Telegram OTP Auth (nur Plan, nicht implementiert)
- Implementation plan saved for reference
