# Package 8: Generative UI in the conversation

Week 45 (2026). Status: direction decided by Stefan on 2026-11-07; slices 1 to 5 built.
Supersedes the "graph is the home" part of `2026-W33-brain-ui-concept.md` (see "Direction change"
below). Builds on the conversation-first shell from the `codex/collector` work.

## Direction change

The W33 concept made the graph the home surface. After comparing four click prototypes (graph home,
conversation home, briefing home, conversation with visualizations) Stefan chose:

- **The conversation is the main way to operate Mycelos.** Talking is where products are heading.
- **Mycelos must not feel like yet another chatbot.** Answers are not just text. Each answer shows
  the knowledge in the form that fits it: a timeline, a mind map, a graph slice, a comparison, a
  flow, a chart, evidence side by side, a filing tree.
- **The daily briefing stays central.** It is the main benefit Stefan gets from Mycelos today. It
  becomes the first, visual message of the day in the conversation.
- **Mycelos as an MCP server for Claude is deferred**, because Mycelos runs only on the home
  network today. The view catalog is designed so that it can later be rendered inside Claude via
  MCP Apps without rework (see "Later: MCP Apps").

What survives from W33 unchanged: the inbox policy (Package 2), run history (Package 3), source
attachments and rules, provenance, and the principle that nothing changes silently. The graph from
Package 4b survives as one view in the catalog instead of as the home page.

## Principles

These follow the current practice for generative UI (stable skeleton, generated panels, fixed
component catalog, explain the change, approve before acting):

1. **Stable skeleton, generated content.** The shell (header, conversation picker, composer, bell,
   settings) never changes. Only the body of an answer is generated.
2. **Catalog, not code.** The model never writes HTML, CSS or script. It picks a view from a fixed
   catalog and supplies data. Mycelos renders the view itself.
3. **The view says why.** Every view shows its name and the reason it was chosen ("timeline:
   question about a course of events, every source has a date"). One click switches to another
   view that fits the same data.
4. **Same question, same view.** View choice is deterministic first (rules over the shape of the
   data), the model may only refine inside the allowed set. Predictability beats novelty.
5. **Every view has a text fallback and its sources.** Telegram, the CLI and any client without a
   renderer get readable text. Every view lists the notes it was built from.
6. **Changes are proposals.** A view that would change knowledge (file, move, attach a source,
   teach a rule) renders as an approval card. Nothing is written before the user approves.
7. **Content is data.** Note content inside view data is never interpreted as instructions,
   exactly as in the organizer prompt.

## The view catalog

The repository already has a channel-independent widget layer: `src/mycelos/widgets/types.py`
(TextBlock, Table, ChoiceBox, StatusCard, ProgressBar, CodeBlock, Confirm, ImageBlock, Compose)
with renderers for CLI, Telegram and JSON, and `ChatEvent(type="widget")` delivery to the web chat.
**Package 8 extends that layer** instead of starting a second one.

New knowledge views. As built in slice 1: one frozen `KnowledgeView` dataclass in
`widgets/types.py` (type, title, reason, data, refs, alternatives, fallback_text, version), with
one strict Pydantic schema per view type in `widgets/views.py` (extra fields forbidden, size
limits, approval actions from a fixed allow-list). `make_view` and `view_from_payload` are the only
ways to build one; the text fallback is always recomputed from validated data, never taken from a
payload. `tree_change` and `approval_card` are system views: they are never chosen for an answer
and never accepted from a model hint.

| View | Shown when | Data | Web rendering | Text fallback |
|---|---|---|---|---|
| `Timeline` | the question asks about a course of events or a status, and the sources carry dates | events `{date, text, kind: normal/warning/due, refs}` | own component | dated list |
| `MindMap` | an overview of one topic with a clear hierarchy | tree `{label, children}` up to depth 3 | Mermaid `mindmap` | indented list |
| `GraphSlice` | the question asks how two or more things relate | nodes, typed edges | SVG, reusing the Package 4b layout code | one line per edge |
| `Comparison` | two to four options judged by the same criteria | columns, rows, per-cell verdict | own component | table text |
| `Flow` | a process, a sequence or a decision | steps and branches | Mermaid `flowchart` | numbered steps |
| `Counts` | quantities across sources, folders or time | label/value pairs | horizontal bars | `label: value` lines |
| `Evidence` | sources support or contradict each other | claims with quote, source, date, relation | side-by-side cards | quotes with sources |
| `TreeChange` | the filing structure changes (capture, move, import preview) | before/after paths, confidence | indented tree with highlights | "moved X from A to B" |
| `ApprovalCard` | the system wants to change something | summary, effects, actions | card with buttons | numbered choices |

Every view also carries `title`, `reason` (the "why"), `refs` (note paths with titles) and
`alternatives` (other views valid for the same data).

`ApprovalCard` reuses `Confirm` and `ChoiceBox` semantics; forms for credentials keep using the
existing settings components. Keys are never entered as chat text (unchanged rule from
`frontend/PRODUCT.md`).

### Wire format

The JSON form follows the A2UI idea (declarative component plus data model, trusted catalog) in a
deliberately small shape:

```json
{
  "type": "timeline",
  "version": 1,
  "title": "Müller GmbH: what is open",
  "reason": "Question about a course of events; every source has a date",
  "alternatives": ["graph_slice", "list"],
  "data": {"events": [{"date": "2026-11-03", "text": "Mail: Q3 receipts missing", "kind": "warning", "refs": ["notes/..."]}]},
  "refs": [{"path": "notes/...", "title": "Mail 03.11."}],
  "fallback_text": "03.11. Mail: Q3 receipts missing\n..."
}
```

Full A2UI compatibility is not a goal of this package. The format is kept close enough that an
adapter can emit A2UI messages later.

### Web renderer contract (as built in slice 2)

`src/mycelos/frontend/shared/mycelos-view.js` defines `<mycelos-view>` (Shadow DOM, no build step,
no Tailwind, no CDN). Later slices rely on this contract:

- **Input.** `el.view = viewDict`, exactly the `view_to_dict` shape, or the `view-json` attribute
  with the same object as JSON. Unknown types or missing data show `fallback_text`.
- **Events** (all bubble and are composed):
  - `mycelos-view-ref` `{path, title}`: a source chip was clicked. Cancelable; without
    `preventDefault` the note opens in `/pages/knowledge.html?note=…`.
  - `mycelos-view-switch` `{from, to, view}`: the user picked another view. `text` is handled by
    the element. A host that calls `preventDefault` sets `el.view` itself. Otherwise the element
    shows `view.variants[to]` when the view carries prebuilt variants (slice 4: answers carry
    every valid alternative, so switching needs no round trip), and the text fallback when not.
    A variant is used only when its own `type` equals its key and is a catalog type.
  - `mycelos-view-action` `{action, view}`: an approval card button. Cancelable; the card locks
    only when the host handled it (`preventDefault`). The host reports the outcome with
    `el.actionResult({ok, message})`: `ok` keeps the card locked with the message, otherwise the
    card unlocks and shows the message as an error (slice 5).
- **Chat.** `chat.html` renders a message `{role: 'view', view}` with the element, and widget
  events whose `type` is a catalog type are turned into such messages.
- **Mermaid.** `mind_map` and `flow` are drawn with `/shared/mermaid.min.js` (loaded on demand,
  `securityLevel: strict`). Labels are entity-encoded, so note text cannot add Mermaid syntax,
  directives or HTML. A failed render shows the text fallback.
- **Labels** come from `web.views.*` via the page's `t()`, with English built in for hosts
  without the i18n script (for example a future MCP App).

## Choosing the view

`src/mycelos/knowledge/view_choice.py`, pure functions, no LLM:

```python
def candidate_views(intent: str, facts: dict) -> list[str]:
    """Views that fit the question intent and the shape of the retrieved knowledge."""

def choose_view(intent: str, facts: dict, model_hint: str | None) -> str:
    """Rule result first; the model hint is accepted only if it is a candidate."""
```

- **Intent** comes from a small, cheap classification step added in this package, with the
  sub-intents `status`, `overview`, `relation`, `compare`, `process`, `count` and `verify`.
  Slice 4 decided: deterministic keyword rules with the model's guess as fallback (see below).
- **Facts** are deterministic properties of the retrieved notes: share with dates, depth of the
  topic subtree, number of linked entities, presence of `contradicts`/`supports` relations.
- The answering model may propose a view (`model_hint`). A hint outside `candidate_views` is
  ignored, and the rule result wins. This mirrors the source-attachment rule: an LLM told "only
  these" will sometimes answer otherwise, and that must be rejected deterministically.
- Fallback when nothing fits: plain text with source chips. That is a valid outcome, not an error.

As built in slice 4 (`knowledge/ask_views.py`, tool `knowledge_view` in `tools/knowledge_view.py`):

- **Intent** is not a separate model call. Keyword rules over the user's question (English and
  German, ordered compare, count, process, verify, relation, status, overview) decide first; the
  intent the answering model passes is used only when no rule matches, and `overview` otherwise.
  A view the user names in the question ("as a timeline", "als Netzwerk") counts as a hint that
  beats the model's hint. Both are accepted only inside the candidates.
- **Retrieval**: hybrid search over the model's search terms, optionally widened by a topic
  subtree, at most 40 notes, archived notes and topic notes excluded. Only metadata is read
  (title, type, status, due, created, topic), plus `knowledge_links` and the current
  `knowledge_relations` among the retrieved notes. Note bodies never enter a view.
- **Facts**: a note's date is its due date, else its creation date; a timeline needs at least two
  distinct dates. `tree_depth` is 2 when the notes sit in two or more topics. Linked entities count
  links, typed relations and a shared topic node (only when it joins two or more notes).
  Quantities group by topic, else by note type, else by month, and need two groups.
- **Builders**: timeline (due items open in the future are `due`, overdue are `warning`), counts,
  graph slice (at most 40 nodes, by degree), mind map (topic tree, or the model's structure) and
  evidence (`supports`/`contradicts` relations with their reason) are deterministic. Comparison
  and flow exist only when the model passes a `structure`, which is validated against the view
  schema; an invalid structure is dropped.
- **Variants**: every candidate that builds is built. The answer view carries the others in
  `variants`, and each one's `alternatives` are the other built views plus `text`.
- **Delivery**: the tool returns a `__view__` payload; the chat service emits it as a widget
  event and the model only sees the text fallback, the sources and a reminder to write a short
  lead. Without notes the tool returns `no_notes`; when only text fits, it returns the note list.
  Telegram and the CLI render the view's text fallback and ignore `variants`.

The view data is built from retrieved notes by deterministic builders where possible (timeline
from dates, tree from topic paths, counts from SQL). The LLM writes the short lead text and, for
`Comparison`, `Flow` and `MindMap`, the structure, validated against the view schema. A response
that fails validation degrades to text.

## The daily briefing as the first message

`knowledge/briefing.py` already gathers deterministic facts (overdue tasks, tasks due today,
reminders, notes from the last 24 hours with provenance) and adds one short LLM synthesis. Package
8 changes only its presentation and adds three fact groups:

1. **Lead text**: the existing synthesis, kept to three sentences.
2. **`Counts`**: arrivals since the last briefing by source (yt-summary, Gmail, Telegram, local
   sources), from `knowledge_notes.source` and the run history.
3. **Cards that need the user** (Class 2 and 3 of the inbox policy): due tasks and reminders,
   proposals such as new top-level folders, failed runs. Taken from the unified inbox read model,
   with their existing resolve actions. The count matches `GET /api/inbox/count` exactly.
4. **New: contradictions** from `knowledge_relations` with `contradicts` created since the last
   briefing, as an `Evidence` teaser.
5. **New: rediscovered**: one older note strongly related to something that arrived today
   (hybrid search over today's arrivals, excluding notes seen in the last 30 days), at most one
   per day.

As built in slice 3 (`knowledge/briefing_views.py`): the briefing payload carries `lead`, `views`
(`counts`, `evidence`, `graph_slice` for the rediscovered note, each only when it has data) and
`needs_you` (inbox entries with their actions, read live on every request, never cached with the
briefing). "Since the last briefing" is the previous build's `generated_at`, at most seven days
back. `knowledge_relations` has no timestamp, so a contradiction counts as new when its from-note
was created or updated in that window (relations are rebuilt when the from-note is analysed).
Rediscovery probes the newest arrivals with the hybrid search (whole title, then its longest
word for FTS-only setups) and skips notes touched in the last 30 days or resurfaced in that
window.

Delivery:

- **Web**: when the user opens the conversation for the first time on a day, the briefing is the
  first message (from `get_or_build_briefing`, cached per day). It is not re-sent on reload:
  `GET /api/briefing/first-message` returns `show`, and the client calls
  `POST /api/briefing/first-message/shown` after rendering it.
- **Telegram**: unchanged schedule; the same views render through `telegram_renderer.py` as text,
  with `Counts` as a short line per source.

Reload and Telegram (follow-up to slices 4 and 5):

- Every view a tool hands to the chat (`__view__`) is appended to the session JSONL as a
  `{"type": "view"}` record. `load_messages` (model resume) skips them; `load_transcript` returns
  messages and views in order, and `GET /api/sessions/{id}/messages` returns views as
  `role: "view"`. An approval card also carries `state` (`pending`, `resolved` with action and
  result, `expired`, `unknown`), read from `view_proposals` for the requesting user, never the
  payload. The client restores answered cards locked (`el.actionResult({ok, action, message})`)
  and expired or unknown ones with `{ok: false, locked: true}`.
- Telegram sends approval cards as their own message with one inline button per action
  (`prop:<action>:<proposal id>`). The button resolves through `ProposalService` as
  `telegram:<user id>`: only allowlisted users, only their own cards, only the card's actions.
- The briefing remains opt-in for push delivery; the in-app first message is always on.

## Scenarios this package must carry

The same five scenarios as the click prototypes (artifact "Mycelos Richtungsvergleich", variant D):

1. **Open in the morning**: briefing with counts, cards that need the user, a contradiction teaser.
2. **Ask** ("What is open with client Müller?"): timeline with sources; switch to graph slice.
3. **Capture** ("Remember: client onboarding as a checklist"): `TreeChange` shows where it went and
   with what confidence, plus accept / elsewhere / make a task.
4. **Attach a source** (Obsidian vault, one rule): approval card with what, where, rule; import
   preview as `Counts` plus `TreeChange`. Requires that local sources use `source_attachments`
   and `source_rules` (see "Prerequisites").
5. **Correct a misfiling**: `TreeChange` before/after, with "teach the rule" showing which other
   notes the rule change would move.

As built in slice 5 (`knowledge/proposals.py`, tools in `tools/filing.py`, endpoint
`POST /api/proposals/{id}/resolve`):

- **Proposals.** Every card stores a proposal (`view_proposals`: kind, server-side payload,
  status). The `approval_card` data carries only its id (`proposal`, 32 hex characters) and the
  filing change as `changes` (same items as `TreeChange`, drawn inside the card). The client
  posts the id and one action; the body cannot name a note, folder or source. Actions per kind:
  capture `accept`/`elsewhere`/`make_task`, attach_source `approve`/`cancel`, correct
  `accept`/`teach_rule`/`cancel`. Refused without change: unknown id, another user's id, an
  action outside the kind, a resolved card (409), a card older than seven days (410). A proposal
  is claimed atomically; a failed action sets it back to pending.
- **Capture.** `note_write` in the chat (a chat service in the context and no connector
  `source`) returns the card as `__view__`. The note exists, because the user asked to remember
  it; `accept` clears the placement marker, `make_task` turns it into an open task, `elsewhere`
  changes nothing and the web chat puts "„title“ belongs in " into the composer.
- **Correct.** `filing_correct(path, topic)` builds before/after. "Teach the rule" lists up to
  five notes in the same folder with the same source key that share at least half of the title's
  significant words (per-word search, deterministic). Approving moves them only if they are
  still in that folder, and appends one English rule line to the source's rule
  (`source_rules`, config generation). Notes without a source key get no rule line.
- **Attach.** `source_attach(source_id, topic, rule)` builds the card and a `Counts` preview from
  `local_sources` preview (files and notes, no model call); sample titles appear as new entries
  in the card's tree. Approving attaches `local:<id>` to the folder, saves the rule, moves an
  existing article root into the folder and enqueues the import.
- **One source model.** `source_key()` maps provenance to the attachment/rule key: connector
  id, `local:<source_id>` for local sources, `chat` for chat captures. The organizer scopes by
  that key and applies a rule with or without attachments; `source_articles.source_root` creates
  a local source's root inside its first attachment.
- **Delivery.** A tool's `__view__` may be a list; each view is one widget event.

## Prerequisites

- **Merge the conversation-first work.** The `codex/collector` branch carries the chat home, the
  settings consolidation, local sources, source checks, evidence articles and the collector. It
  must be green and merged in reviewable parts first. Open items: the collector desktop app
  directory is not committed (26 browser tests depend on it), and the new pages hardcode German
  strings instead of using `t()` with keys in `en.yaml` and `de.yaml`.
- **One source model** (done). Local sources attach through `source_attachments` and use
  `source_rules` under the key `local:<id>`, so scenario 4 has the subtree guarantee:
  - `GET /api/local-sources/{id}/filing` returns the source's attachments and rule; the
    general `/api/sources/{key}` endpoints accept `local:<id>` (only for a source of the
    requesting user, fail closed otherwise) and `chat` next to connector ids.
  - Adding, editing, pausing and resuming a local source is audited (`local_source.*`, no root
    path in the payload) and creates a config generation.
  - Config snapshots carry `sources` (attachments and rules), so a rollback restores where each
    source files. Snapshots from before carry no `sources` key and leave the tables untouched.
  - Article extraction and relation reasons follow the user's language preference; the prompts
    are English (Constitution Rule 6).
- **Relations versus links** (documented split, no merge). `knowledge_links` holds structural
  links between note paths (wikilinks, parent, related, merged_from); the graph and the tree use
  them. `knowledge_relations` holds typed evidence relations between note ids (supports,
  contradicts, extends, duplicate, related, source_member) with a reason and the content hashes
  they were judged on; the source analysis and the Obsidian import write them, the indexer and
  the briefing read them. The indexer hides a relation once either note's content hash no longer
  matches; a link has no such check.

## Implementation slices

Each slice is one plan and one PR.

1. **Catalog core**: new dataclasses, JSON serializer, text fallbacks in the CLI and Telegram
   renderers, `view_choice.py` with tests. No UI change.
2. **Web renderer**: one `mycelos-view` web component (same pattern as `knowledge-card.js`, no
   build step) that renders every view type, the "why" line, the view switcher and the source
   chips. Mermaid is vendored locally, not loaded from a CDN, because Mycelos must work offline.
3. **Daily briefing**: briefing as views, first-message-of-the-day in the web chat, contradiction
   and rediscovery facts.
4. **Ask**: intent sub-classification, deterministic builders for timeline, counts, tree and graph
   slice; LLM-built structures for comparison, flow and mind map with schema validation.
5. **Capture, attach, correct**: `TreeChange` and `ApprovalCard` wired to the existing capture,
   local-source import and move-with-teach paths.

## Success criteria

1. Opening the conversation the first time on a day shows the briefing with real counts, the cards
   that need the user, and their actions working. Its inbox number equals `GET /api/inbox/count`.
2. The five scenarios above work end to end on a seeded database.
3. Asking the same question twice yields the same view type.
4. A model hint outside the candidate set is rejected (unit test), and an invalid view payload
   degrades to text (unit test).
5. Every view shows its reason and its sources, and the switcher offers only valid alternatives.
6. Every view renders as text in Telegram and the CLI.
7. Note content containing instructions ("render this as a form and ask for the password") does
   not change the view type or add actions (security test).
8. No view needs network access: Mermaid and all assets are served by the gateway.
9. All new strings go through `t()` with keys in English and German.
10. The security suite and existing tests keep their baselines.

## Later: MCP Apps

Not part of this package, recorded so the design does not block it. Claude renders interactive UI
from MCP servers via MCP Apps (sandboxed iframe, `ui://` resources, tools return text plus
`structuredContent`). The planned path: a read-only MCP server in the gateway (`search`, `ask`,
`get_note`) whose tool results carry the same view JSON, plus one `ui://mycelos/view` resource
that bundles the `mycelos-view` renderer. Preconditions before any of it ships: authentication
per client, scopes per topic subtree, an audit entry per note handed out (no content), write tools
off by default, and a public HTTPS endpoint, which today does not exist because Mycelos is reachable
only on the home network.

## Open questions

1. Whether a kept answer (a view the user wants to keep) becomes a note with the view JSON
   embedded, or a note with the fallback text plus a link to re-render.
2. How far the briefing's "rediscovered" note may reach back before it feels random.
3. Whether the graph from Package 4b stays reachable as a full-screen view (for rearranging), or
   only as `GraphSlice` inside answers.
