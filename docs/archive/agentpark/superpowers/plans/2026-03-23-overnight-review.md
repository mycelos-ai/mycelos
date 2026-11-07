# Overnight Review — Security, UX, Architecture

**Date:** 2026-03-23
**Status:** REVIEW RESULTS (3 parallel audits)

---

## Security Audit: 23 Findings (78% Constitution Compliance)

### Critical (3)
| # | Finding | File | Fix |
|---|---------|------|-----|
| F1 | Gateway API has ZERO authentication — /api/chat, /api/config, /api/sessions open to anyone | routes.py | Add auth middleware or restrict to localhost |
| F2 | `.env_old` and `.master_key` not in .gitignore | .gitignore | Add `.env*`, `*.master_key` |
| F3 | Raw exception messages leak to API clients (`f"Internal error: {exc}"`) | routes.py:81 | Replace with generic message, log server-side |

### High (5)
| # | Finding | File | Fix |
|---|---------|------|-----|
| F4 | MCP ConnectorManager has zero audit logging (connect, tool_call, disconnect) | mcp_manager.py | Add audit.log calls |
| F5 | Telegram unauthorized access only logger.warning, not audit event | telegram.py:155 | Add audit.log |
| F6 | Webhook secret uses `==` not timing-safe comparison | telegram.py:124 | Use hmac.compare_digest |
| F7 | Capability check bypassed for agent_id="workflow-runner" + empty capabilities = allow-all | runner.py:184,144 | Remove bypass, deny empty caps |
| F8 | connector_call has no per-tool policy check — only checks generic "connector_call" | service.py:1275 | Add policy check on actual MCP tool name |

### Medium (9)
| # | Finding | Fix |
|---|---------|-----|
| F9 | http_get has no SSRF protection (localhost, 169.254.x, file://) | Add URL validation blocklist |
| F10 | 7 slash commands missing audit logs (memory delete/clear, mount add/revoke, connector add/remove, schedule pause/resume) | Add audit.log to each |
| F11 | Dead code after _handle_memory_set (unreachable lines) | Remove |
| F12 | /api/sessions leaks all session data without auth | Fix with F1 |
| F13 | load_credential_to_env puts keys in os.environ (contradicts "never loaded globally") | Remove or deprecate |
| F14 | verify_webhook_secret returns True when no secret configured (fail-open) | Return False |
| F15 | is_user_allowed returns True when no allowlist (fail-open, marked H-01) | Consider fail-closed |
| F16 | github_api endpoint parameter allows arbitrary API calls | Add endpoint allowlist |
| F17 | WorkflowRunner _sanitize_result only handles str, not dict/list | Add recursive sanitization |

### Low (6)
| # | Finding |
|---|---------|
| F18 | _check_policy returns None (allow) when app is None |
| F19 | Sanitizer base64 except pass |
| F20 | CORS allow_credentials + allow_methods=* for dev |
| F21 | LocalSandbox has no security boundary (acknowledged) |
| F22 | Schedule pause/resume missing config generation |
| F23 | No rate limiting on gateway endpoints |

---

## UX Analysis: Simpler Than OpenClaw? YES, with caveats

### What Works (our advantages)
1. **One-command init** — genuinely fewer steps than OpenClaw
2. **Conversational onboarding** — "do something immediately" is excellent UX
3. **Telegram without pairing codes** — simpler than OpenClaw's approach
4. **Token validation with expected format** — instant feedback
5. **Actionable error messages** — most errors say what to DO
6. **Smart defaults** — auto-assigns models to agents
7. **Master key auto-generation** — user never thinks about encryption
8. **Cost transparency** — exact dollar amounts
9. **mycelos db toolkit** — powerful debugging
10. **Well-structured /help** — clear, scannable

### What's Confusing (needs fixing)
1. **Hardcoded German strings** leak into English paths (Telegram, /memory, connector setup)
2. **10 internal init steps** with Rich table wall — feels complex
3. **No "what next?" after init** — only says "run mycelos chat"
4. **Telegram allowlist needs @userinfobot** — external dependency
5. **/help only shows slash commands** — doesn't mention natural language capabilities

### Priority Improvements
| Priority | What | Impact |
|----------|------|--------|
| HIGH | `mycelos init --quick` (one provider, auto-confirm) | Removes 80% of setup friction |
| HIGH | Fix all hardcoded German strings (i18n) | Broken experience for non-DE users |
| MEDIUM | `mycelos doctor` command (checks everything) | Debugging without guessing |
| MEDIUM | Post-init guidance (mention demo, connector setup, serve) | Reduces "what now?" |
| MEDIUM | /help header explaining natural language capabilities | Discovery |
| LOW | Auto-detect Telegram user ID from first message | Removes external dependency |

---

## Architecture Review: 10 Inconsistencies

### High Priority
1. **MCP manager accessed via private `_mcp_manager`** instead of `app.mcp_manager` property (5 locations) — bypasses lazy init
2. **Credential naming inconsistent**: `telegram` (bare) vs `connector:github` (prefixed) vs `credential:connector:github` (double prefix)
3. **Raw SQL in ChatService** (`SELECT FROM policies`) bypasses PolicyEngine abstraction

### Medium Priority
4. **Duplicate GitHub paths**: `github_api` (direct HTTP) + `connector_call("github", ...)` (MCP) — two ways to do the same thing
5. **Tool naming split**: `search_web` (underscore, chat) vs `search.web` (dot, registry) vs `connector_call` (meta) — three conventions
6. **Three different async/sync bridge patterns** in mcp_client, mcp_manager, telegram
7. **Audit event types have no schema** — inconsistent naming (singular/plural, dot hierarchy vs flat)

### Low Priority
8. **i18n violations** in telegram.py, slash_commands.py, connector_cmd.py, db_cmd.py, test_cmd.py
9. **Dead code**: `connect_mcp_sync` function potentially unused, TODO in service.py
10. **Missing security test**: MCP env var isolation test not in tests/security/

---

## Cross-Cutting Themes

### Theme 1: Audit Gaps
All three reviews found audit logging gaps. The Constitution says "Audit Everything" but:
- MCP manager: zero audit
- 7+ slash commands: no audit
- Telegram auth failures: no audit
- connector_call: no per-tool audit

### Theme 2: Fail-Open Patterns
Multiple places where the "Security Fail-Closed" principle is violated:
- Empty capabilities = allow all
- No webhook secret = accept all
- No allowlist = accept all
- App is None = skip all checks
- workflow-runner agent = skip capability check

### Theme 3: i18n Not Done
Hardcoded strings in German AND English throughout. This is the most widespread violation.
Not blocking for German users but breaks the experience for anyone else.

### Theme 4: Gateway Authentication
The single most impactful fix. Without it, all other security measures are bypassed
by anyone who can reach the gateway port.

---

## Recommended Fix Order

### Sprint 1 (next session, 2-3 hours)
1. `.gitignore` fix (F2) — 1 minute
2. Error message sanitization (F3) — 10 minutes
3. Gateway localhost-only or auth (F1) — 30 minutes
4. hmac.compare_digest for webhook (F6) — 5 minutes
5. Workflow-runner capability bypass (F7) — 15 minutes
6. connector_call per-tool policy check (F8) — 20 minutes

### Sprint 2 (following session)
7. MCP manager audit logging (F4)
8. SSRF protection for http_get (F9)
9. Missing audit logs for slash commands (F10)
10. Credential naming standardization
11. `_mcp_manager` → `mcp_manager` property access

### Sprint 3 (ongoing)
12. Complete i18n migration
13. `mycelos init --quick`
14. `mycelos doctor`
15. Rate limiting
