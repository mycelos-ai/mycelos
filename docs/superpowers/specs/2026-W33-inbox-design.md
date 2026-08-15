# The Inbox — what needs you, and what does not

Week 33 (2026). Design for Package 2 of the Brain roadmap. Refines the INBOX surface sketched in `2026-W33-brain-ui-concept.md`.

## The problem, measured

The live yt-summary import created **500 notes in one run**. At today's thresholds (`SILENT_CONFIDENCE = 0.8` for a silent move, everything below becomes a suggestion), a realistic confidence distribution puts roughly a third of them — **150 to 200 entries** — into the inbox as `suggest_move`.

Nobody reviews a list of 180 items. The inbox becomes a landfill, the count becomes noise, and the one thing the count is *for* — signalling that something genuinely needs a human — stops working. That failure is worse than having no inbox: an ignored inbox hides the entries that mattered.

## The organizing question

For every candidate entry, ask: **what happens if I ignore this forever?**

| Answer | Class | Belongs in the inbox? |
|---|---|---|
| Nothing breaks; the brain is just slightly less tidy | **Optimization** | **No** |
| Something is lost, destroyed, or structurally changed | **Consequence** | **Yes** |
| A commitment of mine lapses | **Obligation** | **Yes** |

**The inbox is for decisions with consequences, not for optimization suggestions.** Everything else needs a home that does not demand attention.

## Class 1 — Optimization: out of the inbox

Today's largest contributor is `suggest_move`: "this note might fit better in X". Ignoring it changes nothing — the note stays where it is, findable by search, present in the graph. That is not a decision; it is a nice-to-have.

**Decision (Stefan, W33): file it and mark it uncertain.** A note classified below the silent-apply floor is placed in the proposed folder *immediately* and carries an `uncertain` marker. Consequences:

- The note is **usable at once** — searchable, linked, embedded. No knowledge sits in limbo waiting for a click.
- The marker makes it **findable as a set**: a "review placements" view lists every uncertain note, newest first, with its confidence and the alternative the organizer considered.
- Reviewing is **an opportunity, not a debt**. Nothing degrades if the list is never opened; it simply stays a to-do-when-bored.
- Correcting from that view offers the same "teach the rule?" follow-up as correcting on a node, so the review loop still improves the source rules.

This replaces `suggest_move` entirely. The suggestion kind disappears from the inbox; the confidence value survives on the note.

**Where uncertain placements surface, in descending order of visibility:**

1. **On the node** — "recently arrived here" already shows what landed, with a move gesture. An uncertain arrival is visually distinguishable there.
2. **In the review view** — the full list, filterable by source and folder.
3. **Nowhere else.** Not in the inbox, not in the count, not as a badge.

## Class 2 — Consequence: the real inbox

What stays, and why each earns its place:

| Entry | Why it must be confirmed |
|---|---|
| **Proposed merge** | Destructive: archives (and eventually hard-deletes) the secondary note. Never automatic, at any confidence. |
| **New main category** (`new_topic_confirm`) | Structural: opens a new top-level area under a source attachment. Cheap to accept, expensive to undo once notes accumulate. |
| **Failed source run** | Silence would mean a dead sync nobody notices — exactly the failure mode the truncation and high-water-mark work exists to prevent. |
| **Unclassifiable note** (`organizer_state='manual'`) | The organizer gave up after the retry cap. Without a human, this note stays invisible forever. |
| **Scope violation** | The classifier proposed a path outside a source's permitted subtrees. Rare, and it means either a wrong rule or a wrong attachment — both worth knowing. |

Estimated volume: **single digits per week**, versus 150+ today. That is a list one actually reads.

## Class 3 — Obligation: your agenda, in the same place

Due reminders and overdue tasks are not suggestions — they are commitments. They belong in the inbox because "what needs me right now?" should have one answer, not two. They are visually distinct from suggestions (an obligation has no accept/dismiss, it has done/snooze/reschedule) but they share the surface and the count.

## Bulk events — one entry, not two hundred

**Decision (Stefan, W33): a large import is one event.** When a single run produces many entries of the same kind, the inbox shows one summary entry:

> **499 notes imported from yt-summary** · 140 filed with low confidence · *Review placements →*

Rules for collapsing:

- Collapse **per run**, per source, per kind. Two different sources produce two entries.
- The summary links into the review view (Class 1) or into a filtered list (Class 2), never into a modal that must be worked through.
- **Consequence entries never collapse into a summary that hides them.** Ten failed runs are ten problems; ten merges are ten irreversible decisions. Collapsing is for optimization volume, not for consequence.
- A collapsed entry resolves by being opened — it does not linger after the user has looked.

## The count

One number, on the home surface. It counts **Class 2 and Class 3 only** — things with consequences and obligations. Uncertain placements never appear in it.

This is what makes the number trustworthy: if it says 3, there are exactly three things that genuinely want a human. A number that includes optimization suggestions is a number people learn to ignore, and then they ignore the real entries too.

## What each entry must state

Regardless of class:

1. **What** is proposed or due — in one line, without jargon.
2. **Why** — the organizer's reasoning, or the error, in the user's language. "The rule covers client mail and invoices; a newsletter matches neither, so Vorfina is the fallback."
3. **How sure** — where confidence applies, as a value plus its relation to the threshold ("61% — below your 75% floor"). Merges show no confidence bar: they are never automatic, so the number would imply a decision it does not make.
4. **The one or two actions** that resolve it. Never more than three.
5. **Where it came from** — source, run, timestamp — reachable but not shouting.

## Inbox zero is achievable and means something

With Class 1 removed, an empty inbox is a normal state rather than an aspiration. That matters: a surface that can never be empty stops being read. The empty state should say what it means — "nothing needs you; 12 placements are waiting to be reviewed whenever you like" — so the review view stays discoverable without being a demand.

## Implementation notes

**What exists:** `organizer_suggestions` with kinds `move`, `new_topic`, `new_topic_confirm`, `link`, `refine_type`, `merge`; the confidence column; `organizer_state` on notes including `manual`; failed source runs already produce inbox entries; reminders and tasks are note types with due dates.

**What is genuinely new:**

- An `uncertain` marker on notes (a column or an `organizer_state` value) plus the confidence at placement time, so the review view can rank by shakiness.
- The behavior change in `decide_action`: below the silent floor, **file anyway and mark**, instead of creating a `suggest_move`. This is the load-bearing change — and it must not weaken the *scoped* case: a note from a source with attachments still may only be filed inside the permitted subtrees, uncertain or not. The deterministic validation stays exactly as it is; only the below-threshold branch changes from "suggest" to "file + mark".
- Run-level grouping: suggestions need a run identifier to collapse by, which the ingest does not currently record.
- A unified inbox read model over suggestions + due reminders + due tasks + failed runs, with per-kind resolve actions.
- The review-placements view.

**Deliberately unchanged:** merges are never automatic; `new_topic_confirm` never auto-accepts; the scope boundary and its deterministic validation; the confidence floors themselves.

## Open questions

1. Whether `link` and `refine_type` suggestions (rarely produced today) are Class 1 or Class 2. Provisionally Class 1 — an unadded link breaks nothing.
2. Whether the uncertain marker should decay: after a note has sat correctly filed for months, the marker arguably tells you nothing.
3. Whether the review view should offer "accept all in this folder" — fast, but it re-creates the bulk-confirmation habit the redesign is trying to remove.
