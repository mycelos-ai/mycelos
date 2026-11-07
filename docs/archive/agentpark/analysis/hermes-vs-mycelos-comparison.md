# Hermes Agent vs. Mycelos — Comparative Analysis

**Date:** Week 14 (2026)
**Author:** Claude Code Analysis

---

## Executive Summary

Hermes Agent (Nous Research) and Mycelos are both agent operating systems that go beyond simple chatbot wrappers. They share surprising architectural parallels — multi-scope memory, channel integrations, self-improvement aspirations — but diverge fundamentally in philosophy: **Hermes optimizes for autonomous self-improvement**, while **Mycelos optimizes for security-first controlled evolution**.

---

## 1. Architecture Comparison

| Dimension | Hermes Agent | Mycelos |
|-----------|-------------|--------|
| **Core Philosophy** | Self-improving loop (GEPA) | Security-first, auditable evolution |
| **Agent Model** | Single evolving agent | Multi-agent system (Creator, Planner, Evaluator, Auditor) |
| **Deployment** | VPS with full OS access | Local-first, SecurityProxy in separate process |
| **LLM Backend** | OpenRouter (multi-model) | LiteLLM (Anthropic, OpenAI, Gemini, OpenRouter, Ollama) |
| **Config Management** | Flat config files | NixOS-style immutable generations with atomic rollback |
| **Database** | SQLite (session archive) | SQLite WAL (25+ tables, structured data) |
| **Codebase Size** | ~5k lines (estimated) | 32,463 lines Python, 2,147 tests |
| **License** | Open Source (Nous Research) | Proprietary |

---

## 2. Memory Systems — The Closest Parallel

Both projects independently arrived at a **multi-tier memory architecture**, which is arguably the most critical component for long-running agents.

### Hermes (4 layers)
1. **Markdown files** (`memory.md`) — persistent notes
2. **SQLite database** — session archive
3. **Procedural memory** — learned skills (code snippets)
4. **User modeling** — optional user preferences

### Mycelos (4 scopes)
1. **System** — global state (all agents read)
2. **Agent** — per-agent isolated memory
3. **Shared** — cross-agent accessible data
4. **Session** — ephemeral conversation state

### Analysis

| Aspect | Hermes | Mycelos |
|--------|--------|--------|
| **Isolation** | No scope isolation — agent sees everything | Strict scope isolation (agent memory is private) |
| **Expiration** | Manual cleanup | TTL-based auto-expiry (`expires_at`) |
| **Context Recovery** | "Intelligent forgetting" — extracts key info before compression | Session summaries auto-generated after stale sessions |
| **User Modeling** | Built-in user profile | Memory entries with `key='telegram_chat_id'`, language prefs |
| **Searchability** | Markdown search | SQL-backed `search()` with scope filtering |

**Verdict:** Hermes has a more creative approach with procedural memory (learned skills as executable code). Mycelos has stronger isolation and auditability. The "intelligent forgetting" feature of Hermes is elegant — Mycelos's session summaries achieve a similar goal but less dynamically.

**Mycelos advantage:** Agent memory isolation prevents one agent from reading another's private state. This is a security feature Hermes entirely lacks.

**Hermes advantage:** Procedural memory (skills) that can be invoked later is a powerful paradigm Mycelos doesn't have in this form.

---

## 3. Self-Improvement — The Key Differentiator

### Hermes: GEPA (Generic Evolution of Prompt Architectures)
- Agent **mutates its own prompts and code** through evolutionary approaches
- Evaluates conversations for errors, generates new "skills" automatically
- No human review required — fully autonomous improvement loop
- Evolutionary: multiple prompt variants compete, best survive

### Mycelos: Controlled Evolution via TDD Pipeline
- New agents created through **7-step Creator Pipeline** (Feasibility → Gherkin → Tests → Code → Run → Audit → Register)
- **Independent Auditor Agent** reviews all generated code for security
- Human confirmation required for agent registration (Constitution Rule)
- Plan success rates tracked (`success_rate`, `use_count`) for learning
- Agent reputation scoring exists but isn't auto-applied yet

### Analysis

| Aspect | Hermes | Mycelos |
|--------|--------|--------|
| **Speed of Evolution** | Fast — autonomous loop | Slow — requires audit + human approval |
| **Safety** | Low — no security review | High — auditor checks for eval, exec, forbidden imports |
| **Predictability** | Low — emergent behavior | High — immutable config + rollback |
| **Transparency** | Opaque — evolutionary mutations | Full audit trail for every change |
| **Rollback** | None described | Atomic rollback to any config generation |

**This is the fundamental tradeoff.** Hermes can improve faster, but with no safety net. Mycelos improves slower, but every change is audited, tested, and reversible.

For a **personal dev tool** on a VPS: Hermes' approach is fine.
For a **production system** handling credentials and external services: Mycelos's approach is necessary.

---

## 4. Security — Mycelos's Clear Advantage

| Security Feature | Hermes | Mycelos |
|-----------------|--------|--------|
| **Credential Encryption** | Not described | AES-256-GCM at rest, per-call injection |
| **Credential Isolation** | LLM sees API keys in env | SecurityProxy in separate process, never in prompts |
| **Capability Scoping** | None — full OS access recommended | Prefix-based (`github.read` ≠ `github.write`) |
| **Policy Engine** | None | Per-agent, per-resource (always/confirm/prepare/never) |
| **SSRF Protection** | None | Private IPs, metadata endpoints blocked |
| **Response Sanitization** | None | Strips API keys, tokens from LLM output |
| **Audit Trail** | Session logs | Every state mutation logged |
| **Fail-Closed** | No | Yes — unknown = denied, error = denied |
| **Code Review** | None | Auditor checks for forbidden imports, eval, exec |
| **Sandbox** | Full OS (by design) | Filesystem mounts with access control |

**Hermes explicitly recommends giving the agent full OS access on a VPS.** This is its strength (powerful) and its weakness (dangerous). One prompt injection could compromise the entire server.

**Mycelos's security model** is enterprise-grade: separate proxy process, encrypted credentials, capability tokens with TTL, policy engine, response sanitizer, and 12 security test suites.

---

## 5. Channel Integrations

| Channel | Hermes | Mycelos |
|---------|--------|--------|
| **Terminal/CLI** | Yes | Yes (Rich, tab completion) |
| **Telegram** | Yes | Yes (aiogram, voice, permission buttons) |
| **Discord** | Yes | Planned |
| **Slack** | Yes | Planned |
| **WhatsApp** | Yes | No |
| **Signal** | Yes | No |
| **Web UI** | Not described | Yes (FastAPI + SSE + Alpine.js) |

**Hermes advantage:** More channel integrations out of the box (6 vs 3).

**Mycelos advantage:** Deeper integration per channel — Telegram has inline permission buttons, voice transcription, allowlist-based access control. Web UI with real-time streaming.

---

## 6. LLM Strategy

### Hermes
- OpenRouter as primary provider
- Model selection is user-configured
- No described cost optimization

### Mycelos
- LiteLLM broker with 5+ providers
- **Failover chains** — if model fails, automatically tries next
- **Smart defaults** — cheapest model for classification, mid-tier for execution, expensive for creation
- **Per-call cost tracking** with budget limits
- **Creator Pipeline pauses** if budget exceeded
- Credential-isolated: API keys injected per-call, cleared immediately after

**Mycelos advantage:** Significant cost optimization through tiered model selection. A classification task uses Haiku, not Opus. Budget tracking prevents runaway costs.

---

## 7. Configuration & Reproducibility

### Hermes
- Standard config files
- `hermes claw migrate` for migration from OpenClaw
- No described versioning or rollback

### Mycelos
- **NixOS-style immutable generations** with SHA-256 content addressing
- **Atomic rollback** to any previous generation (O(1) pointer swap)
- **Full diff** between any two generations
- **Trigger tracking** (who/what caused each config change)
- **Parent chain** for full lineage

**This is one of Mycelos's strongest differentiators.** If a config change breaks something, `mycelos config rollback N` instantly restores the previous state. Hermes has no equivalent.

---

## 8. Agent Architecture

### Hermes: Single Evolving Agent
- One agent that gets better over time
- Skills accumulate as procedural memory
- GEPA mutates prompts/code for optimization
- Simple, but less composable

### Mycelos: Multi-Agent System with Specialization
- **5 built-in agents** with distinct roles (Creator, Planner, Evaluator, Auditor, Workflow)
- **Agent handoff** mechanism for delegation
- **Agent registry** with versioning
- **Reputation scoring** per agent
- **User-created agents** via TDD pipeline
- Agents are not just LLM wrappers — deterministic programs are first-class

**Hermes advantage:** Simplicity. One agent, one mental model.

**Mycelos advantage:** Composability. Complex tasks decomposed across specialized agents. The Auditor can't be bypassed by the Creator. Separation of concerns enforced architecturally.

---

## 9. Maturity & Stability

| Aspect | Hermes | Mycelos |
|--------|--------|--------|
| **Age** | New (2026) | ~6 months of development |
| **Stability** | "Rough around the edges" (per video) | 2,147 tests, 12 security test suites |
| **Documentation** | README + video | 5,000+ line spec, 60 Gherkin scenarios |
| **Test Coverage** | Not described | Extensive (unit, integration, E2E, security, property-based) |
| **Error Recovery** | Not described | Config rollback, workflow pause/resume, conversation repair |

---

## 10. Where Each System Should Learn from the Other

### What Mycelos should adopt from Hermes

1. **Procedural Memory (Skills):** The concept of agents learning reusable skills from past executions is powerful. Mycelos tracks `success_rate` but doesn't yet auto-generate reusable skill templates from successful workflows.

2. **Intelligent Forgetting:** Hermes' pre-compression information extraction is elegant. Mycelos's session summaries are similar but could be more dynamic — extracting key facts before context window compression rather than only at session end.

3. **More Channel Integrations:** Discord, WhatsApp, Signal would broaden Mycelos's reach. The architecture supports it (Channel Layer is abstracted), but implementation is pending.

4. **Autonomous Improvement Loop (controlled):** Mycelos could implement a *supervised* version of GEPA — evolutionary prompt optimization that proposes changes but requires audit before activation. Best of both worlds.

### What Hermes should adopt from Mycelos

1. **Security Layer:** Hermes running with full OS access and no credential isolation is a security incident waiting to happen. At minimum: credential encryption, response sanitization, capability scoping.

2. **Immutable Config Generations:** One bad self-improvement mutation could corrupt the system with no way back. NixOS-style generations provide a safety net.

3. **Audit Trail:** Every self-improvement step should be logged and reviewable. Currently Hermes' evolutionary process is opaque.

4. **Code Auditing:** Self-generated code should be reviewed for security before execution. Hermes' GEPA could generate code with `eval()`, `subprocess`, or network calls without any checks.

5. **Multi-Agent Separation:** A single agent that improves itself is both judge and jury. Mycelos's separation (Creator creates, Auditor reviews, Human approves) is more trustworthy.

---

## 11. Strategic Assessment

### Hermes' Bet
"Speed of autonomous improvement outweighs the risks."

This works when:
- Running on isolated infrastructure (VPS)
- Tasks are dev-focused (code generation, scripting)
- User is technical and can recover from failures
- No sensitive credentials at stake

### Mycelos's Bet
"Controlled, auditable evolution is worth the slower pace."

This works when:
- Handling real credentials (API keys, tokens)
- Operating across external services (Telegram, Slack, APIs)
- Multiple agents need isolation from each other
- Reproducibility and rollback matter
- Long-running production deployment

### Who wins?

Neither — they serve different niches.

**Hermes** is a **power user's toy** — exciting, fast-moving, great for experimentation. Think Linux in 1995: powerful, but you need to know what you're doing.

**Mycelos** is a **production agent OS** — slower to evolve, but you can trust it with your API keys and it won't silently break at 3 AM with no rollback. Think NixOS: boring in the best way.

The market will likely want both: Hermes-style rapid experimentation for R&D, Mycelos-style controlled operations for production.

---

## 12. Summary Scorecard

| Category | Hermes | Mycelos | Notes |
|----------|--------|--------|-------|
| **Self-Improvement** | ★★★★★ | ★★☆☆☆ | Hermes' core strength |
| **Security** | ★☆☆☆☆ | ★★★★★ | Mycelos's core strength |
| **Memory System** | ★★★★☆ | ★★★★☆ | Tie — different strengths |
| **Channel Coverage** | ★★★★☆ | ★★★☆☆ | Hermes has more channels |
| **Config Management** | ★★☆☆☆ | ★★★★★ | Mycelos's NixOS approach is unique |
| **LLM Cost Optimization** | ★★☆☆☆ | ★★★★☆ | Tiered model selection matters |
| **Agent Composability** | ★★☆☆☆ | ★★★★★ | Multi-agent > single agent |
| **Ease of Setup** | ★★★★★ | ★★★☆☆ | One-line install is hard to beat |
| **Maturity/Testing** | ★★☆☆☆ | ★★★★★ | 2,147 tests vs "rough edges" |
| **Transparency** | ★★☆☆☆ | ★★★★★ | Full audit trail vs opaque evolution |
| **Innovation** | ★★★★★ | ★★★☆☆ | GEPA is genuinely novel |

**Overall: Hermes ★★★☆☆ — Mycelos ★★★★☆**

Hermes wins on innovation and ease of entry. Mycelos wins on everything required for production use.

