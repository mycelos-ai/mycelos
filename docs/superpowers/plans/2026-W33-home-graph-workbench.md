# Home graph workbench implementation plan

> **For Codex:** Use the execution, test-first, and verification workflows for every task.

**Goal:** Complete Package 4a and add the Package 4b graph workbench with server-stored positions.

**Architecture:** Keep the static Alpine frontend. Add a focused Home graph module. Extend the
existing graph response with position data. Put safe move rules in the knowledge service.

**Stack:** Python, FastAPI, SQLite, Alpine.js, SVG, HTML, CSS, pytest, Playwright.

---

## Task 1: Close the Package 4a gaps

**Files:**

- Modify: `tests/e2e/test_home_surface.py`
- Modify: `src/mycelos/frontend/shared/sidebar.html`
- Modify: `src/mycelos/frontend/shared/api.js`
- Modify: `src/mycelos/frontend/shared/home.js`
- Modify: `src/mycelos/frontend/shared/home.css`
- Modify: `src/mycelos/i18n/en.yaml`
- Modify: `src/mycelos/i18n/de.yaml`

1. Add failing tests for the network warning, capture location, singular text, the More action,
   and the independent Today count.
2. Run the focused Home tests and confirm the expected failures.
3. Restore the health request and the warning in the shell.
4. Use the Keep response to show the initial note location.
5. Add singular text and the root-note More action.
6. Run the focused Home tests until they pass.
7. Commit the Package 4a completion.

## Task 2: Add the graph position contract

**Files:**

- Create: `tests/test_knowledge_graph_api.py`
- Modify: `src/mycelos/storage/schema.sql`
- Modify: `src/mycelos/storage/database.py`
- Modify: `src/mycelos/knowledge/service.py`
- Modify: `src/mycelos/gateway/routers/knowledge.py`

1. Add failing API tests for position read, write, validation, and unknown nodes.
2. Run the new API tests and confirm the expected failures.
3. Add the idempotent position table for new and existing databases.
4. Add service methods to read and store a current-user position.
5. Add positions and each node's compatible `parent_path` to the graph response.
6. Add the position update route with finite-number and range checks.
7. Run the new API tests until they pass.

## Task 3: Make every parent change safe

**Files:**

- Modify: `tests/test_knowledge_graph_api.py`
- Modify: `src/mycelos/knowledge/service.py`
- Modify: `src/mycelos/gateway/routers/knowledge.py`

1. Add failing tests for missing targets, non-topic targets, self moves, and descendant cycles.
2. Add passing contract tests for valid note and topic moves.
3. Run the focused tests and confirm the unsafe cases fail.
4. Add target and cycle validation to the knowledge service.
5. Allow only the fixed `notes` and `tasks` system roots without active topic metadata.
6. Return an error from both routes when the service rejects a move.
7. Test a root-note move and Undo against the database and the Markdown file.
8. Run the focused API and knowledge tests until they pass.

## Task 4: Build the accessible graph surface

**Files:**

- Create: `src/mycelos/frontend/shared/home-graph.js`
- Create: `tests/e2e/test_home_graph.py`
- Modify: `src/mycelos/frontend/pages/dashboard.html`
- Modify: `src/mycelos/frontend/shared/home.js`
- Modify: `src/mycelos/frontend/shared/home.css`
- Modify: `src/mycelos/i18n/en.yaml`
- Modify: `src/mycelos/i18n/de.yaml`
- Modify: `tests/e2e/test_home_surface.py`

1. Add failing tests for the default mode, mobile fallback, topic-only start, and child batches.
2. Add failing tests for search paths, selection, relations, open, and Escape.
3. Add failing tests for position save, parent drop, rollback, and Undo.
4. Add failing tests for pan, zoom, fit, keyboard use, and large graph limits.
5. Add the graph module and connect it to the Home Alpine state.
6. Replace the placeholder with semantic node controls and SVG edges.
7. Add deterministic positions, topic expansion, search paths, and relation display.
8. Add pan, zoom, fit, selection, open, drag, drop, rollback, and Undo.
9. Use a node's `parent_path` to restore a non-visible system root during Undo.
10. Force the tree on mobile and honor reduced motion.
11. Add English and German text for all new controls and states.
12. Run the focused Home tests until they pass.

## Task 5: Verify the complete surface

**Files:**

- Modify only when a test proves a defect.

1. Run the Home, Inbox, shared page, and translation tests.
2. Run the graph API, knowledge, security, and database tests.
3. Run the complete existing test set that covers the changed routes.
4. Inspect Home at desktop and 375-pixel widths.
5. Run the interface detector once on all changed interface files.
6. Fix each confirmed defect with a failing test first.
7. Request an independent code review.
8. Run final verification after every review fix.
9. Commit the verified Package 4b implementation.
