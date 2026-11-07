# mycelos doctor — Diagnostic & Self-Healing Command

> **Week:** 14 (2026)
> **Status:** Draft
> **Priority:** High — needed for beta testers

## 1. Vision

A single command that answers: **"Why isn't X working?"**

Not just health checks — intelligent diagnosis that combines audit logs, config history, scheduler state, and LLM reasoning to find root causes and suggest fixes.

## 2. Use Cases

### Basic Health
```
$ mycelos doctor
  [OK] Server running (localhost:9100, 2h uptime)
  [OK] LLM connection (Anthropic Sonnet 4.5, 0.3s response)
  [OK] Telegram bot connected (@MycelososBot)
  [WARN] 3 overdue task reminders not delivered
  [ERROR] Scheduled workflow 'daily-news' last ran 3 days ago
```

### "Why didn't my reminder fire?"
```
$ mycelos doctor --check reminders

  Checking reminder system...

  Found 3 tasks with reminder=1 and due <= today:
  - "Call Edea" (due: 2026-03-29) — remind_via: [chat, telegram]
  - "Buy milk" (due: 2026-03-28) — remind_via: [chat]

  Diagnosis:
  - No reminder.sent audit events in last 24h
  - Server was restarted at 15:30 — timer threads don't survive restart
  - Telegram chat_id is set (7671836114)

  Root cause: Timer threads are in-process — they die on server restart.

  Fix: Run reminders now?
  [Check reminders now]  [Schedule periodic check]
```

### "Why didn't my daily summary come?"
```
$ mycelos doctor --check schedules

  Checking scheduled workflows...

  Workflow: daily-news-summary
    Schedule: 0 8 * * * (daily at 8:00)
    Last run: 2026-03-27 08:00 (3 days ago)
    Next run: 2026-03-30 08:00 (today)
    Status: MISSED

  Diagnosis:
  - Server was not running between 03-28 08:00 and 03-29 17:00
  - Scheduler (Huey) only runs when 'mycelos serve' is active
  - Missed 2 scheduled runs

  Fix options:
  [Run daily-news now]  [Backfill missed runs]
```

### "Something broke after I changed config"
```
$ mycelos doctor --check config

  Config generations:
    Gen 45 (current) — "Added playwright connector" — 10min ago
    Gen 44 — "Granted filesystem access" — 1h ago
    Gen 43 — "Initial setup" — 2h ago

  Issues since Gen 44:
  - MCP connector 'playwright' failing (3 errors in audit log)
  - Error: "npx not found"

  Suggestion: Roll back to Gen 44?
  [Rollback to Gen 44]  [Install Node.js first]
```

## 3. Architecture

### Check Categories

| Category | What it checks | Data source |
|----------|---------------|-------------|
| `server` | Is mycelos serve running? Port, PID, uptime | HTTP health endpoint |
| `llm` | Can we reach the LLM? Model, latency | Test completion call |
| `credentials` | Are API keys valid? Which providers? | Credential proxy |
| `telegram` | Bot connected? Chat ID set? Polling active? | Telegram getMe API + memory |
| `reminders` | Overdue tasks? Timer threads alive? Last reminder.sent? | DB + audit log |
| `schedules` | Missed runs? Scheduler active? Next run time? | scheduled_tasks + audit |
| `config` | Recent changes? Errors since last gen? | config_generations + audit |
| `connectors` | MCP servers responding? Credentials present? | Connector registry + test |
| `knowledge` | FTS index healthy? Notes count? | DB + index check |
| `storage` | DB locked? WAL mode? Size? | SQLite pragmas |

### LLM-Assisted Diagnosis

For complex problems, doctor can use the LLM (Haiku, cheap):

```
Prompt: "Analyze these audit events and explain why the daily-news
workflow didn't run. Events: [last 50 audit events].
Schedule config: [workflow + schedule].
Server uptime: [health data]."
```

The LLM provides a natural-language explanation the user can understand.

### Data Sources

Doctor reads (all read-only, no mutations):
- `/api/health` — server status
- `audit_events` table — what happened and when
- `config_generations` — what changed
- `scheduled_tasks` — what should have run
- `knowledge_notes` — pending reminders
- `channels` table — Telegram config
- `memory_entries` — user preferences, chat_id
- `credentials` — which services are configured (not the keys!)

## 4. CLI Interface

```bash
mycelos doctor                    # Full health check (all categories)
mycelos doctor --check reminders  # Check specific category
mycelos doctor --check schedules  # Why didn't my schedule run?
mycelos doctor --check config     # What changed recently?
mycelos doctor --fix              # Auto-fix what's possible
mycelos doctor --why "daily summary didn't come"  # LLM diagnosis
```

### --why (LLM-powered)

The killer feature. User describes the problem in natural language:

```
$ mycelos doctor --why "my telegram reminder didn't come"

Analyzing...

I checked the audit log, reminder tasks, and Telegram config.

The issue: Your server was restarted at 15:30. The reminder timer
for "Call Edea" (set for 15:56) was running as an in-process thread
that doesn't survive restarts.

The Telegram notification for "Kaffee trinken" DID fire at 15:35
(before the restart) — check your Telegram history.

Recommendation: Set up a periodic scheduled check instead of one-shot
timers, so reminders survive restarts.

[Schedule periodic reminder check]
```

## 5. Auto-Fix Actions

| Problem | Auto-fix |
|---------|----------|
| Overdue reminders not sent | Run `check_and_notify()` now |
| Missed scheduled runs | Backfill: execute workflow now |
| Config broke something | Suggest rollback to last working gen |
| Telegram chat_id missing | "Send a message to your bot first" |
| MCP connector not responding | Restart gateway |
| LLM credentials expired | "Run: mycelos credential store <provider>" |
| DB locked | Show which process holds it |
| Server not running | "Run: mycelos serve" |

## 6. Doctor as an Agent

The doctor is not a hardcoded script — it's an **LLM agent** (WorkflowAgent) with:

### System Prompt = AGENT.md + Doctor Context

The doctor's plan includes:
- Full AGENT.md content (architecture, constitution, DB schema, components)
- Current system state (health endpoint, uptime, config generation)
- The user's question or --check category

This means the LLM **knows the codebase structure** and can reason about it.

### Tools the Doctor Can Use

```yaml
allowed_tools:
  - doctor_query_audit     # Query audit_events with filters
  - doctor_query_db        # Read-only SQL on any table
  - doctor_config_history  # List config generations + diffs
  - doctor_check_health    # Call /api/health
  - doctor_check_telegram  # Call Telegram getMe API
  - doctor_check_scheduler # Query scheduled_tasks + last runs
  - doctor_check_reminders # Query due tasks with reminder flag
  - note_search            # Search knowledge base
  - note_list              # List tasks/notes
```

### Model Selection

- `--why` and `--fix`: Uses **strongest model** (Opus) — needs deep reasoning
- Basic `--check`: Uses **cheapest model** (Haiku) — simple queries
- User can override: `mycelos doctor --model sonnet`

### Example Agent Loop

```
User: mycelos doctor --why "my daily summary didn't come"

Doctor Agent (Opus):
  1. Calls doctor_check_health → server running, uptime 2h
  2. Calls doctor_check_scheduler → daily-news last_run=2 days ago, missed
  3. Calls doctor_query_audit(type="scheduler.*", limit=20) → no scheduler events today
  4. Calls doctor_config_history(limit=5) → Gen 45 added playwright, Gen 44 was stable
  5. Reasons: server wasn't running during 8:00 AM scheduled time
  6. Response: "Your daily summary runs at 8:00 AM but the server was only
     started 2 hours ago. The scheduler missed the 8:00 run. Run it now?"
  7. Emits suggested_action: [Run daily-news now]
```

### AGENT.md as Living Documentation

AGENT.md serves double duty:
- **For contributors**: How to develop, test, extend Mycelos
- **For the Doctor Agent**: System knowledge for debugging

This means: keeping AGENT.md accurate directly improves the doctor's diagnostic quality.

## 7. Implementation Priority (updated)

1. **Basic health checks** (server, llm, telegram, storage) — framework
2. **Reminder diagnosis** (--check reminders) — most requested
3. **Schedule diagnosis** (--check schedules) — beta tester need
4. **Config rollback suggestion** (--check config) — NixOS strength
5. **--why LLM diagnosis** — the differentiator
6. **--fix auto-repair** — convenience

## 7. What OpenClaw Does That We Skip

- Shell completion checks (not relevant)
- Docker/sandbox checks (we don't have Docker sandboxing yet)
- Legacy migration (we don't have legacy formats yet)
- Platform-specific checks (launchctl, systemd) — future

## 8. What We Do That OpenClaw Doesn't

- **NixOS config rollback** — "go back to when it worked"
- **LLM-powered --why** — natural language diagnosis
- **Audit trail analysis** — correlate events to find root cause
- **Suggested Actions** — clickable fix buttons (like our chat UX)
- **Timer/scheduler diagnosis** — "why didn't X run?"
