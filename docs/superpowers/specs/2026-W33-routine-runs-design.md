# Run history — one table, four writers

Week 33 (2026). Design for Package 3 of the Brain roadmap. Narrowed from the original
"Routines" package after a code survey refuted the concept's own estimate.

## What this package is, and is not

**It is:** a durable, honest record of every routine run — workflows, scheduled tasks, the
briefing, and source syncs — plus the `failed_run` inbox entry that Package 2 reserved and
could not build.

**It is not:** the Routines interface, the control plane (run-now / pause over HTTP), per-source
schedules, cost accounting, or the rename to "Routines". Those follow. This package makes them
possible by giving all four kinds one place to write to.

**Decision (Stefan, W33):** source scheduling and per-source pause are deliberately out of scope.
Today there is one global `auto_ingest_enabled` boolean and a hardcoded hourly cadence; changing
that is a separate, larger piece of work.

## Why this comes before the interface

The concept claimed surfacing routines was "largely a read-and-render job, not a build". A
survey of the code refuted it. The relevant finding:

> Three of the four routine kinds write no run rows at all. Scheduled tasks record `run_count`
> but no outcome. The briefing records one date string. Source syncs record nothing durable.

A Routines list built on that today would show one kind honestly and three kinds as blanks. And
the inbox's `failed_run` entry — reserved in `INBOX_KINDS` since Package 2, documented as
awaiting a producer — would stay unbuildable.

**A dead sync nobody notices is the failure mode the whole ingest hardening exists to prevent.**
Package 1 built truncation flags and high-water-mark clamping so a sync cannot silently lose
data; none of it helps if the sync stops running and no surface says so.

## The core decision: extend, do not duplicate

`workflow_runs` already carries `status`, `error`, `cost`, `budget_limit`, `retry_count`,
`user_id` and timestamps. It is populated and read. Two things stop the other three kinds from
using it:

| Blocker | Where | Fix |
|---|---|---|
| `workflow_id TEXT NOT NULL REFERENCES workflows(id)` | `schema.sql:158` | Allow NULL; a source sync has no workflow |
| No discriminator | — | Add `kind` so one list can tell the four apart |

**Decision (Stefan, W33): extend `workflow_runs` rather than create a second table.** One table,
four writers. A second table would mean two schemas to keep in step, two readers, and a union in
every query — the "two mirrored records" the UI concept explicitly forbids.

`kind` is `'workflow' | 'scheduled_task' | 'briefing' | 'source_sync'`. `workflow_id` stays
non-null in practice for the first two.

**`workflow_events` is deleted.** Zero writers, zero readers in `src/` — the only reference is a
test asserting a column exists. It is not history; it is a table that was designed and never
wired. Carrying it forward would keep implying a durable step log that does not exist.

## The three failure holes

The one kind that *does* have history under-reports it. All three must close, or the new list is
unreliable from its first day.

1. **`fail()` has exactly one call site** — the max-rounds branch. A raised exception propagates
   out of `execute()` and the row stays `running` forever.
2. **A restart mislabels those rows.** The orphan sweep stamps "Orphaned: gateway restarted while
   workflow was running" — which reads as an infrastructure hiccup when the real cause was a
   crash. Wrong cause is worse than no cause: it sends the reader looking in the wrong place.
3. **A scheduled task's failure vanishes into a log line.** `run_count` increments identically
   for success and failure, so the only durable trace is `logger.warning`.

**A run that ends for any reason must leave a row that says so.** That is the invariant.

## What a run row must state

The same discipline as the inbox entry, for the same reason:

1. **Which routine** — kind plus identity (workflow id, source name, or `briefing`).
2. **When** — start and end.
3. **What happened** — succeeded, failed, or still running, and on failure a cause in the user's
   language, not a traceback.
4. **What it did** — counts. Items imported, notes filed, messages sent.

**Constitution Rule 1 applies to the `error` column.** A traceback is not an error message: it
carries file paths, and for an ingest failure it can carry the content that failed to parse. The
stored cause names what failed and why, never what the data contained.

## The inbox entry

`failed_run` becomes real. It is Class 2 (consequence) under the Package 2 policy, and the
reasoning is already recorded there: silence would mean a dead sync nobody notices.

Rules inherited from Package 2, not re-litigated here:

- **Consequence entries never collapse.** Ten failed runs are ten problems.
- **It must be resolvable.** Package 2's final review found `unclassifiable` entries that no
  endpoint could clear, which broke inbox zero. `failed_run` must not repeat that: a failed run
  is resolved by retrying it or by dismissing it, and both must exist before the entry ships.
- **Fail closed.** A failed retry does not mark the entry resolved.

**Open question:** whether a routine that fails on every tick should produce one entry per
failure or one entry that updates. One per failure is honest but becomes its own landfill for an
hourly sync. Provisionally: one entry per routine, carrying a failure count and the latest cause.

## Deliberately unchanged

- The scheduler's cadences and its hardcoded job registration.
- `auto_ingest_enabled` as a single global switch.
- The workflow executor's design — an LLM tool-calling loop over `plan`/`allowed_tools`/
  `success_criteria`. The inert `steps`/`conditions`/`policies` columns stay inert; this package
  does not make them real, and no UI should render them.
- Cost accounting. `budget_per_run` remains a dead variable and `llm_usage` remains unjoinable to
  a run. Recording it honestly is its own piece of work.
