# Home (Package 4a) — bar, today, tree, shell

Week 33 (2026). Spec for Package 4a of the Brain roadmap. Written for an external implementer:
Stefan builds this with a tool of his choice on a branch in this repo; Claude reviews the branch.

## What 4a is

The navigation shell with the five surfaces, and the Home surface in its **tree-first** form: the
omnibox (Search · Ask · Capture), the Today strip, and the tree view of the knowledge map. It
replaces `dashboard.html` as the entry point.

**What 4a is NOT:** the graph workbench (4b — deliberately split so the product works tree-first
while the graph matures), the Node view (5), the Converse re-frame (6), and any backend change.
**4a is frontend-only.** Every API it needs exists; a missing API is a finding for the review, not
something to build inline.

## The reference: MyCelos Design System

`MyCelos Design System.zip` (repo root) is the visual reference — the "Neural Mycelium" design.
Its language is binding unless a deviation below says otherwise:

- Dark ground `#0a0f14`, panels `#0e151c`/`#0d141b`, borders `#16212b`/`#1e2b35`/`#26343f`
- Accent cyan `#35c7e3` (hover `#6fd9ee`), on-accent text `#06222b`
- Text: `#e8eef2` primary, `#9fb1bc` secondary, `#7b8d98` muted, placeholder `#5c6d78`
- Font Lato; radii 8-16px; `fadeup`/`popin` animations; cyan glow shadows on focus surfaces
- `Brain v3.dc.html` is the authoritative Home mockup (v1/v2 are earlier iterations). `Inbox.dc.html`,
  `Converse.dc.html`, `Node.dc.html`, `Source.dc.html` show the sibling surfaces.

The mockups are `.dc.html` files with `{{placeholder}}` template slots — they demonstrate layout
and styling, not working code. The implementer translates them into the existing frontend stack
(`src/mycelos/frontend/`: static HTML pages + Alpine + shared `head/sidebar/api/i18n`), not into a
new framework. **No new build step, no npm frontend.** The `frontend/` Next.js directory at repo
root is dead and stays dead.

## Deviations from the mockup — decided, not optional

The mockups predate three decisions from the W33 concept reviews:

1. **Routines is the fifth surface.** The mockup sidebar shows Brain · Inbox · Converse · System.
   The shell must show **Brain · Inbox · Routines · Converse** with System below the spacer.
   Routines links to the existing `workflows.html` for now — the real Routines surface is a later
   package. The nav item exists so the shell is complete and the information architecture is
   stable from day one.
2. **Sources are a first-class concept** (attached at folders, one rule set per source). 4a only
   touches this in the tree: a topic that has source attachments shows a small source affordance
   (the `Source.dc.html` iconography). Clicking it goes to `connectors.html` for now.
3. **The Today strip is data, not decoration.** The mockup hardcodes "2 new from yt-summary ·
   3 awaiting review · 1 due: Müller filing". The real strip reads:
   - new arrivals: notes with `created_by='import'` created today (via existing knowledge API)
   - awaiting review: `GET /api/inbox/count` — **the count that Package 2 made trustworthy; the
     strip must show this number and no other**
   - due today: due reminders + overdue tasks from the inbox API entries
   Each segment links to its surface (inbox, review view). A segment with count 0 disappears; if
   all are 0, the strip says one calm sentence, not three zeros.

## The omnibox — the one hard interaction

One input, three intents, resolved by explicit action, not guessing:

- **Typing** filters/searches immediately (hybrid search API) — results appear as the match hint
  and in the tree (filtered).
- **⏎ = Ask**: the query goes to the answer flyout (mockup: `answerOpen`) with citation chips;
  each chip opens the note (for 4a: `knowledge.html`'s note view). "Continue in Converse" hands
  the thread to chat.
- **⇧⏎ = Keep**: the text is captured as a note via the existing quick-capture path, filed by the
  organizer like any capture. Feedback confirms where it went ("Kept — filing…" then the target).
- **⌘K / Ctrl+K** focuses the bar from anywhere in the shell. Esc closes the flyout, then clears,
  then unfocuses (three presses, in that order).

Ask uses the existing chat/ask API; if a streaming endpoint exists, stream — otherwise the
blinking-dots state from the mockup until the answer lands. **No new backend.**

## The tree

`treeMode` from the mockup: topic hierarchy with per-topic note counts, indentation by depth,
expand/collapse. Data: existing knowledge topics/graph APIs.

- Clicking a topic navigates to its contents (for 4a: `knowledge.html` filtered to that topic).
- The Graph/Tree toggle exists; **Graph shows a placeholder state in 4a** ("Graph view lands in
  the next package") — never a broken canvas. The toggle remembers its choice, but Graph never
  pretends to work.
- Uncertain placements (Package 2's `placement_confidence`) show their marker in the tree at the
  note level only if notes are shown; topics do not aggregate uncertainty in 4a.

## German and English

Every user-facing string goes through `t()` with keys in BOTH `en.yaml` and `de.yaml` in the same
commit — the project rule. The mockup's English labels (Search · Ask · Capture, Today, awaiting
review) need German equivalents decided at implementation time and reviewed for tone (Du-Form,
consistent with existing de.yaml).

## Success criteria — 4a is DONE when all of these hold

Functional, demonstrable on a live instance:

1. **Entry**: opening `/` lands on Home (the new surface), with the shell showing five nav items;
   every nav item goes somewhere real; the current surface is visually marked.
2. **Search**: typing in the bar filters the tree live against the real knowledge base;
   diacritics-insensitive (search "muller", find "Müller") — the hybrid search backend already
   guarantees this; the UI must not break it by pre-filtering client-side.
3. **Ask**: ⏎ produces an answer with at least one citation chip when relevant notes exist; the
   chip opens the note; the flyout closes with Esc and × .
4. **Keep**: ⇧⏎ creates a real note that appears in the knowledge base and is picked up by the
   organizer; the UI confirms the capture without blocking further typing.
5. **Today**: with a seeded day (an import, a pending inbox entry, a due reminder) the strip shows
   the three real numbers, each linking to its surface; with nothing pending it shows the calm
   empty state. The inbox number equals `GET /api/inbox/count` exactly.
6. **Tree**: renders the real topic hierarchy with counts; expand/collapse works; a topic with
   500+ notes renders without freezing the tab (virtualize or paginate — implementer's choice, but
   the criterion is: interaction stays responsive).
7. **Keyboard**: ⌘K focuses from anywhere in the shell; Esc behaves as specified; the bar,
   toggle, tree rows and flyout actions are reachable and operable by keyboard alone.
8. **Empty brain**: a fresh database shows a welcoming empty Home (bar works, tree explains
   itself, Today is calm) — not a wall of zeros or errors.
9. **Mobile**: at 375px width the shell collapses to the existing mobile-nav pattern; the bar,
   strip and tree remain usable (the mockup is desktop-first; mobile follows the existing
   `mobile-nav.html` conventions).
10. **i18n**: switching the user language flips every new string; no hardcoded English remains in
    the new surface (`grep` finds no literal UI strings outside `t()` calls in the new files).
11. **No regressions**: every existing page still loads; the security suite and the frontend
    API tests hold their baselines; no new backend endpoints, no schema changes.
12. **Design fidelity**: side-by-side with `Brain v3.dc.html`, the surface reads as the same
    design — palette, spacing rhythm, radii, the bar's focus glow — with the documented
    deviations applied. Pixel-identity is NOT required; recognizability is.

## Review contract

Stefan implements on a branch in this repo. The review (Claude) checks the branch against the
twelve criteria above — each gets a verdict with evidence, and criteria 2-8 are verified by
driving the running UI, not by reading code. Rule 1 applies to new code: no note content in
console logs, analytics, or error messages.
