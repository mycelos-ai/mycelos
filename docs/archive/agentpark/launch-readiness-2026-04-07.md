# Launch Readiness Analysis — 2026-04-07

Honest gap analysis before public/friends launch. Findings grouped by priority. Status reflects what was fixed in the same session vs. what's parked for later.

## Status snapshot

- **Tests:** 1990 passed, 0 failures (excluding e2e/playwright)
- **CHANGELOG:** well-maintained, current
- **Security tests:** 15 files in `tests/security/` covering SSRF, path traversal, capability scoping, prompt injection, dotfile protection, fail-closed, response sanitization, AST bypass
- **Recently fixed (session 2026-04-05/07):** KB path traversal, Auditor AST bypass, SSRF gaps + consolidation, SecurityProxy stale socket dirs, Email/Chat test mocks

## ✅ Fixed in this session

### License inconsistency
- **Was:** README badge said `License TBD`, README.md "License" section said `[TBD]`, but `pyproject.toml` and `LICENSE` already said MIT.
- **Fixed:** README badge → MIT (green), License section now points to LICENSE file.

### Hardcoded English in `init_cmd.py` (Constitution Rule 7 violation)
- **Was:** `_pick_provider` used hardcoded `"Provider"`, `_setup_ollama` used `"Ollama URL"`, `_setup_filesystem_permissions` had ~15 hardcoded English strings (Working Directory, Additional Access, Grant read access to..., etc.).
- **Fixed:** Added 21 new keys to en.yaml + de.yaml under `init.*`, replaced all hardcoded strings with `t()` calls. Logger added, silent excepts replaced with `logger.debug(..., exc_info=True)`.

## 🚨 Still BLOCKER (must fix before public launch)

### 1. PyPI publication
- **Status:** Parked (decided to do last).
- **Where:** `pyproject.toml` is fine, just unpublished. README quick-start says `pip install mycelos` which won't work.
- **Action:** Either publish, or change README to `pip install -e .` until published.

### 2. Gateway has no real auth
- **Where:** `src/mycelos/gateway/routes.py:98-103` — `_resolve_user_id` trusts `X-User-Id` header blindly. Default falls back to first active user in DB.
- **Problem:** Two friends sharing one instance = each can read everyone's notes, credentials list, audit log. The optional Basic Auth at `serve_cmd.py:71` is a single shared password, doesn't isolate users.
- **Decision (session):** This is the next priority. Either: (a) document loud and clear "single-tenant only — self-host per user", or (b) build proper auth before friends launch.
- **Impact:** Critical for any multi-user scenario, including casual sharing.

### 3. SecurityProxy startup is fragile
- **Where:** `src/mycelos/gateway/server.py:375, 420` — proxy can die during startup, gateway falls through to "running without proxy" mode and silently disables LLM credential injection.
- **Symptoms:** During test runs we see `Failed to start SecurityProxy: SecurityProxy process died during startup` followed by `LLM health check: SKIPPED — credentials may not be available`. On macOS/sandboxed environments this can reliably break first-run.
- **Fix options:**
  - (a) Make proxy startup failure fatal — better to refuse to start than to run in a broken state
  - (b) In-process fallback for single-user mode (skip the unix socket, call LiteLLM directly with credentials in-memory)
  - (c) Better diagnostics: log *why* the proxy died, surface to user
- **Decision (session):** Future session, this is the blocker that matters most.

### 4. README ↔ Reality drift
- **Slack channel:** Listed in README architecture diagram and CLAUDE.md, but `src/mycelos/channels/` only has Terminal + Telegram. No `slack.py`.
- **OptimizerAgent:** Listed in CLAUDE.md system agents list, but `init_cmd.py:30-61` doesn't register it. Only `mycelos`, `builder`, `workflow-agent`, `evaluator-agent`, `auditor-agent` exist.
- **EvaluatorAgent:** Registered but no handler — currently a stub.
- **Action:** Either build them or remove from marketing. Recommendation: remove from README/CLAUDE.md until built.

## ⚠️ ROUGH EDGES (should fix before launch)

### 5. First-run "wow path" has too many decisions
- **Where:** `init_cmd.py` filesystem setup
- **Problem:** After API key, user is asked: working dir → home read access? → ~/Documents? → ~/Downloads?. That's 4 yes/no prompts in a row.
- **Recommendation:** Default to `~/.mycelos/workspace` only. Move other mounts to a `/mount add` slash command users can run later when they actually need it.

### 6. Doctor command not in README
- **Where:** `README.md` command list
- **Problem:** `mycelos doctor` is the single best onboarding/debug tool. CLAUDE.md documents it heavily. README doesn't mention it.
- **Action:** Add a "Troubleshooting" section to README mentioning `mycelos doctor` and `mycelos doctor --why`.

### 7. Master key backup warning shown only once
- **Where:** `init_cmd.py:601-607`
- **Problem:** If user loses `.master_key`, all credentials are unrecoverable. Warning is shown only on first generation, easy to miss.
- **Action:** Add `mycelos credential backup` command + force re-display warning on first `mycelos chat`.

### 8. No user-visible error when LLM fails mid-chat
- **Where:** Chat runtime path
- **Problem:** Connectivity check at init has retry loop, but gateway runtime logs `LLM health check: FAILED` only to logger — user sees no message in chat UI.
- **Action:** Surface LLM errors as chat messages, not just log entries.

## 🟢 NICE-TO-HAVES (can wait)

### 9. Repo root cleanup
- **What's there:** `heise_de_screenshot.png`, `obsidian-sample-vault.zip`, `stitch.zip`, `n` (empty dir), `=9.0` (typo file)
- **Decision (session):** Not critical — when we cut a clean public repo, we'll start fresh. Park.

### 10. `maicel-credentials.json` in repo root
- **Status:** Stefan's local file for not having to re-init. Not a leak. Already gitignored.

### 11. Slack channel
- See #4 — either build or remove from marketing.

## What's GOOD (no action needed)

- Test suite is solid (1990 passed)
- No TODO/FIXME debt in `src/`
- Frontend distribution via hatch artifacts works
- Security tests are comprehensive (15 files, all major attack vectors covered)
- CHANGELOG well-maintained (628+ lines)
- Code clean, no major dead-code paths

## Recommended order for the next launch sprint

1. **SecurityProxy startup robustness** (#3) — without LLM working reliably, nothing works
2. **Auth strategy decision** (#2) — either document single-tenant or build basic per-user auth
3. **README ↔ Reality fix** (#4) — remove Slack/OptimizerAgent from marketing
4. **Onboarding simplification** (#5) — fewer prompts on first run
5. **Doctor in README** (#6) — quick win, surfaces existing tooling
6. **Master key backup UX** (#7) — prevent data loss
7. **PyPI publish** (#1) — last step before going wide

## Success criteria for "ready to ship to friends"

A new user (technical, but not a Mycelos contributor) should be able to:

1. Find install instructions that work (PyPI or git)
2. Run `mycelos init` and end with a working setup
3. Run `mycelos serve` and `mycelos chat` and have a conversation
4. Recover from an API key error without restarting
5. Understand from the README that this is single-tenant (or have working multi-user)
6. Know to run `mycelos doctor` if something is broken

Currently: **3 of 6 work reliably.** Items 1, 5, 6 are documentation gaps. Item 4 is the LLM-error UX issue.
