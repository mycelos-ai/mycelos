# Home graph workbench (Package 4b)

Week 33 (2026). This specification completes Package 4a and defines Package 4b.

## Goal

Home becomes the daily knowledge workbench. The graph shows the knowledge structure without
replacing the usable tree. A user can find, select, arrange, open, and safely move knowledge.

The implementation also closes six confirmed gaps in Package 4a:

- Restore the network exposure warning in the new shell.
- Confirm the initial capture location after Keep.
- Use the correct singular result text.
- Replace the hidden 200-note tree limit with an explicit More action.
- Show today's imported-note count from the database and link it to Knowledge.
- Show a source action on each topic with an attached source and link it to Connectors.

## Scope

Package 4b includes:

- a graph-first desktop Home after the first visit;
- the tree as a full alternative and the only mobile mode;
- stable node positions stored on the server;
- direct topic expansion with batches of 50 children;
- search highlights inside the graph;
- node selection, relation chips, and an Open action;
- pan, zoom, fit, keyboard access, and reduced-motion support;
- drag to move a position;
- drop on a topic to change the parent;
- server validation for every parent change;
- an Undo action after a parent change.

Package 4b does not include:

- the large Node view from Package 5;
- relation creation or deletion;
- source editing;
- organizer teaching actions;
- confirmed answer citations;
- a change to the current 5,000-node graph limit.

## Main interaction

Desktop Home opens the graph on the first visit. Home remembers a later Graph or Tree choice.
Mobile Home always opens the tree.

The graph starts with topics only. Opening a topic adds its direct children. Home shows 50
children first. A More action adds the next batch. Closing a topic removes its descendants from
the visible graph unless a search or another open path needs them.

Search keeps the graph in place. Home adds each available result and its topic path. It highlights
the results and reduces the contrast of unrelated nodes. A result outside the graph response gets
an Open result action instead of a false location.

A first click selects a node. Home then shows its relation chips and the selected relation edges.
A second click or Enter opens the existing knowledge page. Escape clears the selection.

Dragging a node to free space stores its position. Dropping a node on a topic also changes its
parent. Home highlights a valid target. Home rejects a node itself and its descendants as targets.
The server repeats all checks. After a successful parent change, a toast offers Undo.

## Graph display

Home uses semantic HTML buttons for nodes and SVG lines for edges. It does not use the old canvas
simulation. This keeps the controls accessible and avoids a pairwise layout cost.

The graph uses these visual rules:

- A solid line shows a parent relation.
- A dashed cyan line shows a selected knowledge relation.
- A relation chip includes text, so color is never the only signal.
- A selected node has a clear cyan outline.
- A search result has a distinct highlight.
- A possible drop target has a separate target state.

Home calculates a deterministic first position near the parent. Home uses a stored position when
one exists. Search, selection, open, pan, and zoom never change stored node positions.

## Stored positions

The database adds `knowledge_graph_positions` with these fields:

- `user_id`
- `note_path`
- `x`
- `y`
- `updated_at`

The pair of `user_id` and `note_path` is unique. The note path follows a rename and disappears
with a deleted note. The current user owns each position.

`GET /api/knowledge/graph` adds a top-level `positions` object. Each node also includes its
`parent_path`. Existing clients can ignore both additions.

`PUT /api/knowledge/graph/positions/{path}` accepts `x` and `y`. The server accepts only finite
numbers in the supported coordinate range. It returns 404 for an unknown node.

The browser stores only the view mode, open topics, pan, and zoom. It does not store node positions.

## Safe parent changes

The service accepts a parent change only when all conditions are true:

- The source exists.
- The target exists and is an active topic, or it is the fixed `notes` or `tasks` system root.
- The source and target differ.
- A topic does not move below one of its descendants.

The two system roots are the only targets that do not need topic metadata or a Markdown file.
Only a topic can use `null` to return to the topic root. A note cannot use `null` to leave its
system root. The service rejects every other missing target. The service checks a topic target path
to the root with a visited set. It rejects a cycle and leaves the old parent unchanged. Both note
update routes return an error for an invalid move.

## 4a completion details

The shell reads `/api/health`. It shows a clear warning when the service has network exposure
without password protection. The warning links to the security instructions.

Keep uses the create response. It reports the initial location and states that the organizer still
checks the note. It never claims a later location that the response cannot prove.

The result count uses separate singular and plural text. A root list above 200 notes shows a More
action. Each activation adds 200 notes without hiding the remaining count.

The Today review number always comes from `/api/inbox/count`. The number of loaded inbox rows does
not replace it.

Home reads `/api/knowledge/home-summary` for two Package 4a facts. The response counts notes with
`created_by='import'` that the database created today. It also groups the current user's source
attachments by `topic_path`. A zero import count stays hidden. A nonzero count links to the
Knowledge page.

Each tree topic with one or more source attachments shows a Source action. The action links to the
existing Connectors page. English and German provide the same labels and recovery text.

## Errors and recovery

Home keeps the last usable graph when a refresh fails. It shows a short retry message. A failed
position save returns the node to its last stored position. A failed parent change restores the
old parent and position. Undo repeats a normal validated parent change. The node `parent_path`
lets Undo restore a non-visible `notes` or `tasks` system parent.

Home does not put note content in a log, an error message, or analytics.

## Mobile and access

At 600 pixels or less, Home uses the tree. The mobile navigation stays unchanged. The graph toggle
does not offer an unusable graph mode.

Every node and control supports the keyboard. Enter selects or opens as defined above. Escape
clears selection. Buttons include an accessible name and state. The graph stops optional motion
when the user requests reduced motion.

## Performance limits

Home creates DOM nodes only for visible topics, open child batches, search paths, and the selected
relations. It never creates 5,000 node controls at the initial load. Edge work scales with the
visible edges. The layout does not compare every node with every other node.

The graph API still returns at most 5,000 recent notes. Home states this limit when a search result
cannot join the graph. The Open result action remains available.

## Success criteria

Package 4b is complete when all checks pass:

1. A first desktop visit shows the graph. A mobile visit shows the tree.
2. The graph first shows topics only.
3. Topic expansion shows direct children in batches of 50.
4. Search highlights results and their paths without replacing the graph.
5. Selection shows relation chips and only the selected relation edges.
6. Enter and a second click open the selected node.
7. A free drag stores a position and a reload restores it.
8. A topic drop changes the parent and offers Undo.
9. The server rejects unknown targets, non-topic targets, self moves, and topic cycles.
10. A failed save restores the prior state and shows an error.
11. Pan, zoom, fit, and keyboard controls work.
12. The tree remains usable and mobile always uses it.
13. A 5,000-node response does not create 5,000 visible controls at start.
14. The six Package 4a gaps in this specification have regression tests.
15. English and German contain the same new translation keys.
16. The security, API, and existing page tests keep their baselines.
