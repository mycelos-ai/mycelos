# Organizer P0 Residuals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining P0-2 sub-items from the June claims audit: the organizer's stale-suggestion auto-accept must respect a confidence floor and must only mark suggestions accepted when the action actually succeeded — in the background handler, the single-accept API, and the accept-all API. Merges additionally record a `merged_from` provenance link.

**Architecture:** The pure decision logic (confidence floor, merge exclusion) goes into `knowledge/organizer.py` (the LLM-free, storage-free testable core). The handler (`knowledge_organizer_handler.py`) and the gateway router (`gateway/routers/knowledge.py`) consume it. Failed auto-accepts flip the suggestion to a new `failed` status and reset the note to `organizer_state='pending'` so the next hourly run re-classifies it fresh. (Post-review correction: `MAX_CLASSIFY_ATTEMPTS` does NOT bound this loop — it only counts classification failures, and a successful re-classification resets it. A persistently un-applyable note re-cycles indefinitely at ~one classification per 25h. Fail-closed is preserved; bounding the loop by counting failed suggestions per (note_path, kind) is a planned fast-follow.)

**Tech Stack:** Python 3.12, SQLite (WAL), pytest, FastAPI TestClient.

## Global Constraints

- **Constitution Rule 1 (Audit Everything):** every new mutation outcome logs an audit event (`organizer.auto_accept_failed` added; existing `organizer.auto_accept`, `organizer.merge`, `organizer.accept_all` events preserved).
- **Constitution Rule 2:** `organizer_suggestions` and `knowledge_notes` are content/ephemeral tables, NOT declarative state — no config generation needed (per CLAUDE.md list).
- **Constitution Rule 3 (Fail-Closed):** on any error the action is treated as failed; a failure never results in `status='accepted'`.
- **Constitution Rule 9:** all code, comments, log messages in English.
- **Every change ships with tests. Every commit updates `CHANGELOG.md`** (calendar week: `## Week 32 (2026)`; the changelog step is folded into the final task).
- **Commit messages:** English, no Co-Authored-By/Generated-with footers.
- Existing behavior to preserve: merge suggestions are NEVER auto-executed by the background job (pinned by commit 4730eca); manual single-accept executes merges via `_execute_merge`.

## Verified current state (2026-08-09, branch claude/ux-review-round)

All four June P0s are fixed and tested (commit 4730eca, 84 tests green). Remaining gaps this plan closes:

1. `_auto_accept_stale` (`src/mycelos/agents/handlers/knowledge_organizer_handler.py:481-563`) ignores the stored `confidence` column entirely — suggestions are created precisely when confidence is LOW (< 0.8 silent-apply threshold), so after 24h the lowest-confidence classifications get applied unattended.
2. The same method wraps each action in `try/except: pass` and then unconditionally sets `status='accepted'` + `organizer_state='ok'` — failures are recorded as successes.
3. `POST /api/organizer/accept-all` (`src/mycelos/gateway/routers/knowledge.py:405-471`) swallows per-action failures, then calls `inbox.accept_all_pending()` as a "safety net" — which flips **merge** suggestions to accepted without ever executing them.
4. `POST /api/organizer/suggestions/{sid}/accept` (same file, `:475-531`): `_execute_merge` swallows its own exceptions and returns `None`, so a failed merge still reaches `inbox.accept(sid)`.
5. `_execute_merge` archives the secondary note (30-day tombstone via lifecycle) but writes no `merged_from` edge into `knowledge_links` (the `kind` column exists since commit df1a113), so the merge is invisible in the graph and hard to trace for restore.

---

### Task 1: Pure auto-accept predicate in `organizer.py`

**Files:**
- Modify: `src/mycelos/knowledge/organizer.py`
- Test: `tests/test_organizer_lifecycle.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `AUTO_ACCEPT_CONFIDENCE: float = 0.95` (module constant) and `should_auto_accept(kind: str, confidence: float) -> bool` in `mycelos.knowledge.organizer`. Task 2 imports both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_organizer_lifecycle.py`:

```python
from mycelos.knowledge.organizer import AUTO_ACCEPT_CONFIDENCE, should_auto_accept


def test_should_auto_accept_high_confidence_move() -> None:
    assert should_auto_accept("move", 0.95) is True
    assert should_auto_accept("new_topic", 1.0) is True
    assert should_auto_accept("link", 0.99) is True


def test_should_auto_accept_below_floor_is_rejected() -> None:
    assert should_auto_accept("move", 0.94) is False
    assert should_auto_accept("link", 0.0) is False


def test_should_auto_accept_merge_never() -> None:
    # Merges are destructive (archive + eventual hard-delete of the
    # secondary note) — never auto-accepted regardless of confidence.
    assert should_auto_accept("merge", 1.0) is False


def test_should_auto_accept_unknown_kind_fails_closed() -> None:
    assert should_auto_accept("refine_type", 1.0) is False
    assert should_auto_accept("frobnicate", 1.0) is False


def test_auto_accept_floor_is_stricter_than_silent_apply() -> None:
    # The silent-apply path (fresh classification) uses 0.8; unattended
    # acceptance of *stale* suggestions must be stricter, not looser.
    from mycelos.knowledge.organizer import SILENT_CONFIDENCE
    assert AUTO_ACCEPT_CONFIDENCE > SILENT_CONFIDENCE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_organizer_lifecycle.py -v -k auto_accept`
Expected: FAIL with `ImportError: cannot import name 'AUTO_ACCEPT_CONFIDENCE'`

- [ ] **Step 3: Implement the predicate**

In `src/mycelos/knowledge/organizer.py`, below the existing constants (`SILENT_CONFIDENCE = 0.8` block, around line 14):

```python
AUTO_ACCEPT_CONFIDENCE = 0.95

# Suggestion kinds that unattended acceptance may apply. Merge is
# deliberately absent: it archives (and eventually hard-deletes) the
# secondary note and always requires explicit user confirmation.
_AUTO_ACCEPTABLE_KINDS = frozenset({"move", "new_topic", "link"})


def should_auto_accept(kind: str, confidence: float) -> bool:
    """Whether a stale pending suggestion may be applied unattended.

    Fail-closed: unknown kinds and anything below the confidence floor
    stay pending for the user to decide.
    """
    if kind not in _AUTO_ACCEPTABLE_KINDS:
        return False
    return confidence >= AUTO_ACCEPT_CONFIDENCE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_organizer_lifecycle.py -v`
Expected: all PASS (new + pre-existing lifecycle tests)

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/organizer.py tests/test_organizer_lifecycle.py
git commit -m "feat(organizer): pure should_auto_accept predicate with 0.95 floor"
```

---

### Task 2: Handler auto-accept — confidence floor + accept only on success

**Files:**
- Modify: `src/mycelos/agents/handlers/knowledge_organizer_handler.py` (method `_auto_accept_stale`, lines 481–563; add helper `_apply_suggestion`)
- Test: `tests/test_knowledge_organizer_handler.py`

**Interfaces:**
- Consumes: `should_auto_accept(kind, confidence)` from Task 1 (add to the existing `from mycelos.knowledge.organizer import ...` block at the top of the handler, which already imports `DUPLICATE_THRESHOLD`).
- Produces: `_apply_suggestion(self, kb, storage, row, payload: dict) -> bool` (True only when the action fully succeeded). New suggestion status value `'failed'`. New audit event `organizer.auto_accept_failed` with details `{"id", "kind", "path"}`. Task 3 changes `_execute_merge` separately — this task does not touch it.

- [ ] **Step 1: Write the failing tests**

The existing file has `SQLiteStorage`, `_FakeKB`, `_FakeAudit`, and `InboxService` imported, plus a handler fixture pattern — follow the existing tests around `_auto_accept_stale` (search the file for `auto_accept` to find them; adapt if the local fixture names differ). Append:

```python
def _stale_suggestion(storage, note_path: str, kind: str, payload: dict,
                      confidence: float) -> int:
    """Insert a pending suggestion backdated past the 24h staleness window."""
    cursor = storage.execute(
        "INSERT INTO organizer_suggestions "
        "(note_path, kind, payload, confidence, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now', '-25 hours'))",
        (note_path, kind, json.dumps(payload), confidence),
    )
    return cursor.lastrowid


def test_auto_accept_skips_low_confidence(handler_env) -> None:
    handler, storage, kb, audit = handler_env
    sid = _stale_suggestion(storage, "notes/a", "move",
                            {"target": "topics/x"}, confidence=0.5)
    accepted = handler._auto_accept_stale(storage, kb, "u1")
    assert accepted == 0
    assert kb.moved == []
    row = storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "pending"  # left for the user, not applied


def test_auto_accept_applies_high_confidence_move(handler_env) -> None:
    handler, storage, kb, audit = handler_env
    sid = _stale_suggestion(storage, "notes/a", "move",
                            {"target": "topics/x"}, confidence=0.97)
    accepted = handler._auto_accept_stale(storage, kb, "u1")
    assert accepted == 1
    assert ("notes/a", "topics/x") in kb.moved
    row = storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "accepted"


def test_auto_accept_failure_is_not_marked_accepted(handler_env) -> None:
    handler, storage, kb, audit = handler_env
    kb.move_to_topic = lambda path, target: False  # simulate missing note
    sid = _stale_suggestion(storage, "notes/a", "move",
                            {"target": "topics/x"}, confidence=0.97)
    accepted = handler._auto_accept_stale(storage, kb, "u1")
    assert accepted == 0
    row = storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "failed"
    note = storage.fetchone(
        "SELECT organizer_state FROM knowledge_notes WHERE path=?", ("notes/a",))
    assert note["organizer_state"] == "pending"  # re-enters classification
    assert any(e[0] == "organizer.auto_accept_failed" for e in audit.events)


def test_auto_accept_merge_stays_pending(handler_env) -> None:
    handler, storage, kb, audit = handler_env
    sid = _stale_suggestion(storage, "notes/a", "merge",
                            {"duplicate_path": "notes/b"}, confidence=1.0)
    accepted = handler._auto_accept_stale(storage, kb, "u1")
    assert accepted == 0
    row = storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "pending"
```

`handler_env` must yield `(handler, storage, kb, audit)` with a real `SQLiteStorage` (temp dir), `_FakeKB(["topics/x"])`, `_FakeAudit`, and a seeded `knowledge_notes` row for `notes/a` (`organizer_state='suggested'`). If the file already has an equivalent fixture for the existing auto-accept tests, reuse it instead of creating a new one; existing tests that assert the old always-accept behavior must be updated to the new semantics, not deleted.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_knowledge_organizer_handler.py -v -k auto_accept`
Expected: new tests FAIL (low-confidence move still applied; failure still marked accepted)

- [ ] **Step 3: Rewrite `_auto_accept_stale` and add `_apply_suggestion`**

Replace the body of `_auto_accept_stale` (keep the docstring's intent, extend it):

```python
    def _auto_accept_stale(self, storage, kb, user_id: str) -> int:
        """Auto-accept suggestions that have been pending > 24 hours.

        Only non-destructive kinds at or above AUTO_ACCEPT_CONFIDENCE are
        applied (merge always needs explicit confirmation). A suggestion
        is marked 'accepted' only when the action actually succeeded;
        failures flip it to 'failed' and put the note back into the
        classification queue. Returns the number auto-accepted.
        """
        stale = storage.fetchall(
            "SELECT * FROM organizer_suggestions WHERE status='pending' "
            "AND created_at < datetime('now', '-24 hours')"
        )
        if not stale:
            return 0

        count = 0
        for row in stale:
            try:
                payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            except (TypeError, ValueError):
                payload = {}
            kind = row["kind"]
            try:
                confidence = float(row["confidence"])
            except (TypeError, ValueError):
                confidence = 0.0

            if not should_auto_accept(kind, confidence):
                # Stays pending: merges and low-confidence suggestions
                # wait for the user in the inbox.
                continue

            try:
                ok = self._apply_suggestion(kb, storage, row, payload)
            except Exception as exc:
                logger.warning("Auto-accept failed for suggestion %s: %s", row["id"], exc)
                ok = False

            if ok:
                storage.execute(
                    "UPDATE organizer_suggestions SET status='accepted' WHERE id=?",
                    (row["id"],),
                )
                storage.execute(
                    "UPDATE knowledge_notes SET organizer_state='ok' WHERE path=?",
                    (row["note_path"],),
                )
                count += 1
            else:
                # Fail closed: never record a failure as an acceptance.
                # Send the note back through classification so a fresh,
                # currently-valid suggestion replaces this one.
                storage.execute(
                    "UPDATE organizer_suggestions SET status='failed' WHERE id=?",
                    (row["id"],),
                )
                storage.execute(
                    "UPDATE knowledge_notes SET organizer_state='pending' WHERE path=?",
                    (row["note_path"],),
                )
                self._audit(user_id, "organizer.auto_accept_failed",
                            {"id": row["id"], "kind": kind, "path": row["note_path"]})

        if count > 0:
            self._audit(user_id, "organizer.auto_accept",
                        {"count": count, "reason": "stale>24h"})
        return count

    def _apply_suggestion(self, kb, storage, row, payload: dict) -> bool:
        """Execute one suggestion. True only when it fully succeeded."""
        kind = row["kind"]
        if kind == "move":
            target = payload.get("target")
            if not target:
                return False
            return bool(kb.move_to_topic(row["note_path"], target))
        if kind == "new_topic":
            name = payload.get("name")
            if not name:
                return False
            # Find-or-create via the ONE slugify — recomputing the slug
            # with a different algorithm produced parents that don't
            # exist (umlaut names).
            from mycelos.knowledge.note import slugify
            target = f"topics/{slugify(name)}"
            exists = storage.fetchone(
                "SELECT path FROM knowledge_notes WHERE path=? AND type='topic'",
                (target,),
            )
            if not exists:
                target = kb.create_topic(name)  # raises on failure
            for member in payload.get("members", []):
                if not kb.move_to_topic(member, target):
                    return False
            return True
        if kind == "link":
            dst = payload.get("to")
            src = payload.get("from") or row["note_path"]
            if not dst:
                return False
            kb.append_related_link(src, dst)  # raises on failure
            return True
        # merge and unknown kinds are never auto-applied (fail closed);
        # should_auto_accept filters them before we get here.
        return False
```

Add `should_auto_accept` to the existing `from mycelos.knowledge.organizer import (...)` import at the top of the file. The old inline `new_topic`/`move`/`link`/`merge` branches inside the loop are fully replaced by this.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_knowledge_organizer_handler.py -v`
Expected: all PASS (update any pre-existing auto-accept tests that seeded suggestions without confidence — they now need `confidence >= 0.95` to observe acceptance)

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/agents/handlers/knowledge_organizer_handler.py tests/test_knowledge_organizer_handler.py
git commit -m "fix(organizer): auto-accept gated by confidence floor, accepts only on success"
```

---

### Task 3: `_execute_merge` returns success + writes `merged_from` edge

**Files:**
- Modify: `src/mycelos/agents/handlers/knowledge_organizer_handler.py` (method `_execute_merge`, lines 565–604)
- Test: `tests/test_knowledge_organizer_handler.py`

**Interfaces:**
- Consumes: `knowledge_links(from_path, to_path, kind)` table (kind column exists since commit df1a113).
- Produces: `_execute_merge(...) -> bool` — True on success, False on failure. Task 4 relies on this return value. Edge semantics: `(from_path=primary, to_path=secondary, kind='merged_from')`, i.e. "primary was merged from secondary".

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_knowledge_organizer_handler.py` (reuse `_FakeKBWithFiles` and the real-`SQLiteStorage` pattern of the existing merge tests; the secondary note file must exist in the fake's `_files`):

```python
def test_execute_merge_returns_true_and_writes_merged_from_edge(merge_env) -> None:
    handler, storage, kb = merge_env  # kb: _FakeKBWithFiles with notes/b on "disk"
    ok = handler._execute_merge(kb, storage, "notes/a", "notes/b", 0.95, "u1")
    assert ok is True
    edge = storage.fetchone(
        "SELECT kind FROM knowledge_links WHERE from_path=? AND to_path=?",
        ("notes/a", "notes/b"),
    )
    assert edge is not None and edge["kind"] == "merged_from"
    assert "notes/b" in kb.archived


def test_execute_merge_returns_false_on_failure(merge_env) -> None:
    handler, storage, kb = merge_env
    def _boom(path, **kwargs):
        raise RuntimeError("disk full")
    kb.update = _boom
    ok = handler._execute_merge(kb, storage, "notes/a", "notes/b", 0.95, "u1")
    assert ok is False
    edge = storage.fetchone(
        "SELECT kind FROM knowledge_links WHERE from_path=? AND to_path=?",
        ("notes/a", "notes/b"),
    )
    assert edge is None  # no provenance edge for a merge that didn't happen
    assert kb.archived == []  # secondary must not be archived
```

`merge_env` mirrors the fixture the existing `_execute_merge` tests use (reuse it if present). Note `_execute_merge` reads the secondary file via `kb._knowledge_dir / (secondary_path + ".md")` — the fixture must create that file in a temp dir and point `kb._knowledge_dir` at it (the existing merge tests already solve this; copy their setup).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_knowledge_organizer_handler.py -v -k execute_merge`
Expected: FAIL — `ok is None` (method currently returns nothing), no `merged_from` edge

- [ ] **Step 3: Implement**

Change the signature and body of `_execute_merge`:

```python
    def _execute_merge(
        self, kb, storage, primary_path: str, secondary_path: str,
        similarity: float, user_id: str,
    ) -> bool:
        """Merge secondary note into primary: append content, archive secondary.

        Returns True only when the merge fully succeeded. Records a
        `merged_from` edge (primary -> secondary) so the merge is
        traceable in the graph and restorable while the secondary's
        30-day archive tombstone lasts.
        """
        try:
            from mycelos.knowledge.note import parse_frontmatter

            secondary_file = kb._knowledge_dir / (secondary_path + ".md")
            if not secondary_file.exists():
                return False

            secondary_md = secondary_file.read_text(encoding="utf-8")
            secondary = parse_frontmatter(secondary_md)

            separator = f"\n\n---\n*Merged from: {secondary.title}*\n\n"
            kb.update(primary_path, content=separator + secondary.content, append=True)

            primary_meta = storage.fetchone(
                "SELECT tags FROM knowledge_notes WHERE path=?", (primary_path,)
            )
            if primary_meta:
                primary_tags = json.loads(primary_meta["tags"] or "[]")
                merged_tags = list(set(primary_tags) | set(secondary.tags or []))
                if merged_tags != primary_tags:
                    kb.update(primary_path, tags=merged_tags)

            # Provenance edge BEFORE archiving: primary was merged from
            # secondary. Survives archival; removed only if the secondary
            # is hard-deleted (remove_note cleans its edges).
            storage.execute(
                "INSERT OR REPLACE INTO knowledge_links (from_path, to_path, kind) "
                "VALUES (?, ?, 'merged_from')",
                (primary_path, secondary_path),
            )

            kb.archive_note(secondary_path)

            self._audit(user_id, "organizer.merge", {
                "primary": primary_path,
                "archived": secondary_path,
                "similarity": similarity,
            })
            return True
        except Exception as exc:
            logger.warning("Merge failed %s + %s: %s", primary_path, secondary_path, exc)
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_knowledge_organizer_handler.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/agents/handlers/knowledge_organizer_handler.py tests/test_knowledge_organizer_handler.py
git commit -m "fix(organizer): _execute_merge reports success and records merged_from edge"
```

---

### Task 4: Single-accept endpoint fails closed on merge failure and bad payloads

**Files:**
- Modify: `src/mycelos/gateway/routers/knowledge.py` (route `organizer_accept`, lines 475–531)
- Test: `tests/test_organizer_api.py`

**Interfaces:**
- Consumes: `_execute_merge(...) -> bool` from Task 3.
- Produces: HTTP contract — 500 `{"error": "apply failed: merge failed"}` when a merge fails; 422 `{"error": "invalid suggestion payload"}` when the payload lacks its required field (`target` for move, `name` for new_topic, `to` for link, `duplicate_path` for merge). In both cases the suggestion stays `pending`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_organizer_api.py` (uses the existing `api_client` fixture and `_seed_note`):

```python
def test_accept_move_with_missing_target_is_422_and_stays_pending(api_client) -> None:
    client, app_obj = api_client
    path = _seed_note(app_obj)
    sid = InboxService(app_obj.storage).add(path, "move", {}, 0.7)  # no target
    resp = client.post(f"/api/organizer/suggestions/{sid}/accept")
    assert resp.status_code == 422
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "pending"


def test_accept_merge_failure_is_500_and_stays_pending(api_client) -> None:
    client, app_obj = api_client
    path = _seed_note(app_obj, "Primary")
    # duplicate_path points at a note that does not exist on disk ->
    # _execute_merge returns False
    sid = InboxService(app_obj.storage).add(
        path, "merge", {"duplicate_path": "notes/does-not-exist", "similarity": 0.95}, 0.95)
    resp = client.post(f"/api/organizer/suggestions/{sid}/accept")
    assert resp.status_code == 500
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "pending"
```

Check `InboxService.add`'s return value first (`src/mycelos/knowledge/inbox.py:35`) — if it does not return the new id, fetch it via `SELECT MAX(id) FROM organizer_suggestions`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_organizer_api.py -v -k "missing_target or merge_failure"`
Expected: FAIL — both currently return 200 and mark the suggestion accepted

- [ ] **Step 3: Implement**

In `organizer_accept`, replace the action block:

```python
    try:
        if kind == "move":
            target = payload.get("target")
            if not target:
                return JSONResponse({"error": "invalid suggestion payload"}, status_code=422)
            if not kb.move_to_topic(sug["note_path"], target):
                return JSONResponse({"error": "apply failed: note not found"}, status_code=500)
        elif kind == "new_topic":
            name = payload.get("name")
            if not name:
                return JSONResponse({"error": "invalid suggestion payload"}, status_code=422)
            new_path = kb.create_topic(name)
            for member in payload.get("members", []):
                if not kb.move_to_topic(member, new_path):
                    return JSONResponse(
                        {"error": f"apply failed: could not move {member}"}, status_code=500)
        elif kind == "link":
            dst = payload.get("to")
            if not dst:
                return JSONResponse({"error": "invalid suggestion payload"}, status_code=422)
            kb.append_related_link(payload.get("from") or sug["note_path"], dst)
        elif kind == "merge":
            duplicate_path = payload.get("duplicate_path")
            if not duplicate_path:
                return JSONResponse({"error": "invalid suggestion payload"}, status_code=422)
            handler = mycelos.knowledge_organizer
            ok = handler._execute_merge(
                kb, mycelos.storage, sug["note_path"], duplicate_path,
                payload.get("similarity", 0.0),
                resolve_user_id(request),
            )
            if not ok:
                return JSONResponse({"error": "apply failed: merge failed"}, status_code=500)
        elif kind == "refine_type":
            pass
    except Exception as exc:
        return JSONResponse(
            {"error": f"apply failed: {exc}"}, status_code=500
        )
```

The trailing `inbox.accept(sid)` + audit log stay unchanged — they are now only reached on success.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_organizer_api.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/gateway/routers/knowledge.py tests/test_organizer_api.py
git commit -m "fix(api): organizer accept fails closed on merge failure and bad payloads"
```

---

### Task 5: Accept-all endpoint — no blanket accept, merges stay pending

**Files:**
- Modify: `src/mycelos/gateway/routers/knowledge.py` (route `organizer_accept_all`, lines 405–471)
- Test: `tests/test_organizer_api.py`
- Modify: `CHANGELOG.md` (single Week-32 entry covering the whole plan)

**Interfaces:**
- Consumes: nothing new (uses `InboxService`, `kb` as today).
- Produces: HTTP contract — response becomes `{"accepted": int, "topics_created": int, "failed": int, "skipped_merges": int}`. Merge suggestions are never touched by accept-all (stay `pending`); failed actions leave their suggestion `pending` and are counted in `failed`. The `inbox.accept_all_pending()` safety net is removed (check the web frontend for consumers of the response shape — `src/mycelos/frontend/` — and update any display of the accept-all result).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_organizer_api.py`:

```python
def test_accept_all_leaves_merges_pending(api_client) -> None:
    client, app_obj = api_client
    path = _seed_note(app_obj, "Primary")
    inbox = InboxService(app_obj.storage)
    inbox.add(path, "merge", {"duplicate_path": "notes/x", "similarity": 0.95}, 0.95)

    resp = client.post("/api/organizer/accept-all")
    assert resp.status_code == 200
    assert resp.json()["skipped_merges"] == 1
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE kind='merge'")
    assert row["status"] == "pending"  # never blanket-accepted


def test_accept_all_counts_failures_and_leaves_them_pending(api_client) -> None:
    client, app_obj = api_client
    path = _seed_note(app_obj)
    inbox = InboxService(app_obj.storage)
    inbox.add("notes/ghost-note", "move", {"target": "topics/x"}, 0.7)

    resp = client.post("/api/organizer/accept-all")
    assert resp.status_code == 200
    assert resp.json()["failed"] == 1
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE note_path='notes/ghost-note'")
    assert row["status"] == "pending"
```

Note: `list_pending_by_topic` groups move/new_topic under topic groups and puts link/refine_type/merge under a "Links" group (see `inbox.py:124`) — the merge row reaches the loop via that group.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_organizer_api.py -v -k accept_all`
Expected: FAIL — merge gets blanket-accepted by `accept_all_pending()`; ghost move is marked accepted

- [ ] **Step 3: Implement**

Rewrite the loop body of `organizer_accept_all`:

```python
    groups = inbox.list_pending_by_topic()
    accepted = 0
    topics_created = 0
    failed = 0
    skipped_merges = 0

    for group in groups:
        if group.get("topic") is None:
            # Ungrouped suggestions: links are applied; merges are
            # destructive and NEVER part of accept-all — the user
            # confirms each one individually.
            for s in group["notes"]:
                if s.get("_synthetic"):
                    continue
                kind = s.get("kind")
                if kind == "merge":
                    skipped_merges += 1
                    continue
                if kind == "link":
                    try:
                        dst = s["payload"].get("to")
                        if not dst:
                            raise ValueError("link suggestion without target")
                        kb.append_related_link(
                            s["payload"].get("from") or s["note_path"], dst
                        )
                    except Exception:
                        failed += 1
                        continue
                    inbox.accept(s["id"])
                    accepted += 1
                else:
                    # refine_type and unknown kinds: accepting is a
                    # no-op action, mark handled.
                    inbox.accept(s["id"])
                    accepted += 1
            continue

        topic_path = group["topic"]
        is_new = group.get("is_new", False)

        if is_new and topic_path:
            try:
                kb.create_topic(group["topic_name"])
                topics_created += 1
            except Exception:
                pass  # may already exist; per-note moves below decide success

        for s in group["notes"]:
            if s.get("_synthetic"):
                continue
            try:
                ok = True
                if s["kind"] in ("move", "new_topic"):
                    ok = bool(topic_path) and bool(
                        kb.move_to_topic(s["note_path"], topic_path)
                    )
            except Exception:
                ok = False
            if ok:
                inbox.accept(s["id"])
                accepted += 1
            else:
                failed += 1  # stays pending — never record failure as success
```

Delete the `inbox.accept_all_pending()` "safety net" line. Extend the audit call and response:

```python
    try:
        mycelos.audit.log(
            "organizer.accept_all",
            user_id=user_id,
            details={"accepted": accepted, "topics_created": topics_created,
                     "failed": failed, "skipped_merges": skipped_merges},
        )
    except Exception:
        pass

    return {"accepted": accepted, "topics_created": topics_created,
            "failed": failed, "skipped_merges": skipped_merges}
```

Then grep the frontend for consumers: `grep -rn "accept-all\|topics_created" src/mycelos/frontend/ --include='*.js' --include='*.ts' --include='*.tsx' --include='*.vue' --include='*.html'`. The new fields are additive, so existing displays keep working; if the UI shows a completion toast, extend it to mention skipped merges when `skipped_merges > 0` (user-facing string in German UI files goes through the existing i18n mechanism of the frontend — match how neighboring strings are localized).

- [ ] **Step 4: Run tests to verify they pass, then the full affected suite**

Run: `python -m pytest tests/test_organizer_api.py tests/test_knowledge_organizer_handler.py tests/test_organizer_lifecycle.py tests/test_organizer_inbox.py tests/test_organizer_robustness.py tests/test_organizer_classify.py -v`
Expected: all PASS

- [ ] **Step 5: Update CHANGELOG.md**

Add under a `## Week 32 (2026)` heading (create it above Week 31/older entries if absent):

```markdown
## Week 32 (2026)

### Organizer auto-accept is fail-closed (P0 residuals)

Closed the remaining gaps from the June claims audit:

- **Confidence floor for unattended acceptance.** Stale (>24h) pending
  suggestions are only auto-applied at confidence >= 0.95; below that
  they stay in the inbox for the user. Merges remain excluded entirely.
- **Acceptance now means success.** The background auto-accept, the
  single-accept API, and accept-all no longer mark suggestions
  "accepted" when the underlying action failed — failures stay pending
  (API) or flip to a `failed` status and re-enter classification
  (background), with an `organizer.auto_accept_failed` audit event.
- **Accept-all never touches merges.** The blanket
  `accept_all_pending()` safety net silently marked merge suggestions
  accepted without executing them; accept-all now reports
  `skipped_merges` and leaves them pending for individual confirmation.
- **Merges are traceable.** A successful merge records a `merged_from`
  edge (primary -> secondary) in the link graph before archiving the
  secondary note, so it stays visible and restorable during the 30-day
  tombstone window.
```

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/gateway/routers/knowledge.py tests/test_organizer_api.py CHANGELOG.md
git commit -m "fix(api): accept-all skips merges, counts failures, drops blanket accept"
```

---

### Task 6: Full-suite verification

**Files:** none new.

- [ ] **Step 1: Run the security suite** (must never break)

Run: `python -m pytest tests/security/ -q`
Expected: all PASS

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest -q --tb=short`
Expected: all PASS. Fix anything red before declaring done (superpowers:verification-before-completion).
