# Knowledge Milestone C — Smart Import + Topic Map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Smart Import (drag & drop / file picker with preserve vs. suggest modes) and the auto-generated Mermaid Topic Map inside topic-index notes.

**Architecture:** A new `knowledge/import_pipeline.py` handles file extraction, `detect_import_mode`, and the two mode branches. A `POST /api/knowledge/import` multipart endpoint drives it. A new `knowledge/topic_map.py` emits Mermaid syntax from the current topic + wikilink graph, wired into `_build_topic_index` so every topic-index note carries its own graph at the top. The existing mermaid rendering in `knowledge.html` needs no changes.

**Tech Stack:** Python 3.12, `zipfile`/`tarfile`/multipart from Starlette, existing `KnowledgeService`, existing `LLMBroker` for the `suggest` mode classification batch, Alpine.js, Playwright.

**Spec reference:** `docs/superpowers/specs/2026-04-08-knowledge-organize-design.md` — Sections 3, 6, 7 (Import button + drag-drop), 8 (`POST /api/knowledge/import`), 9 (import unit + integration + Playwright).

**Prerequisites:** Milestones A and B merged. v3 schema, bucketing, parse_note, organizer handler + inbox, and all `/api/knowledge/notes` + `/api/organizer/*` endpoints exist.

**Out of scope:** Image/PDF import (future, vision-model spec), Obsidian round-trip sync, file watchers, conflict merge.

---

## File Structure

### Files created
- `src/mycelos/knowledge/import_pipeline.py` — `detect_import_mode`, `run_preserve_import`, `run_suggest_import`, frontmatter parsing helpers.
- `src/mycelos/knowledge/topic_map.py` — `build_topic_mermaid(topic_path, knowledge)` returning mermaid source.
- `tests/fixtures/obsidian-vault/` — small fixture vault: 3 folders (`journal/`, `projects/`, `recipes/`), 1–2 `.md` files per folder.
- `tests/fixtures/flat-notes/` — 10 `.md` files at the root, no subfolders.
- `tests/test_import_detect.py` — unit tests for `detect_import_mode`.
- `tests/test_import_preserve.py` — unit tests for preserve mode.
- `tests/test_topic_map.py` — unit tests for mermaid emission.
- `tests/test_knowledge_import_api.py` — API tests for `POST /api/knowledge/import`.
- `tests/integration/test_import_live.py` — suggest-mode integration test with cassette-recorded Haiku.
- `tests/e2e/test_smart_import.py` — Playwright drag-drop + modal flow.

### Files modified
- `src/mycelos/knowledge/service.py` — extend `_build_topic_index` to embed the mermaid block.
- `src/mycelos/gateway/routes.py` — add `POST /api/knowledge/import`.
- `src/mycelos/frontend/pages/knowledge.html` — Import button + drag-drop zone + modal with options.
- `src/mycelos/i18n/locales/en.yaml` — import strings.
- `src/mycelos/i18n/locales/de.yaml` — same keys, German.
- `CHANGELOG.md`

---

## Task 1: `detect_import_mode`

**Files:**
- Create: `src/mycelos/knowledge/import_pipeline.py`
- Test: `tests/test_import_detect.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_import_detect.py
from __future__ import annotations

from dataclasses import dataclass

from mycelos.knowledge.import_pipeline import FileEntry, detect_import_mode


def _mk(paths: list[str]) -> list[FileEntry]:
    return [FileEntry(relpath=p, content=b"# x\nbody\n") for p in paths]


def test_flat_is_suggest() -> None:
    files = _mk(["a.md", "b.md", "c.md"])
    assert detect_import_mode(files) == "suggest"


def test_one_folder_is_suggest() -> None:
    files = _mk(["notes/a.md", "notes/b.md"])
    assert detect_import_mode(files) == "suggest"


def test_two_folders_is_suggest() -> None:
    files = _mk(["a/one.md", "b/two.md"])
    assert detect_import_mode(files) == "suggest"


def test_three_folders_is_preserve() -> None:
    files = _mk(["a/x.md", "b/y.md", "c/z.md"])
    assert detect_import_mode(files) == "preserve"


def test_mixed_counts_folders_correctly() -> None:
    files = _mk(["root.md", "a/x.md", "b/y.md", "c/z.md"])
    assert detect_import_mode(files) == "preserve"


def test_empty_is_suggest() -> None:
    assert detect_import_mode([]) == "suggest"
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_import_detect.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement the module shell**

Create `src/mycelos/knowledge/import_pipeline.py`:

```python
"""Smart Import pipeline — preserve and suggest modes.

- ``detect_import_mode`` is a pure function driven by folder count.
- ``run_preserve_import`` mirrors source structure 1:1.
- ``run_suggest_import`` drops files into ``imports/<YYYY-MM-DD>/`` and
  calls the organizer for a batch classification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class FileEntry:
    relpath: str
    content: bytes


def detect_import_mode(files: list[FileEntry]) -> str:
    folders: set[str] = set()
    for f in files:
        if "/" in f.relpath:
            folders.add(f.relpath.rsplit("/", 1)[0])
    return "preserve" if len(folders) >= 3 else "suggest"


_FRONTMATTER_RE = re.compile(
    r"^---\n(.*?)\n---\n(.*)$", re.DOTALL
)
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _safe_filename(name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    return _SAFE_NAME_RE.sub("-", stem).strip("-").lower() or "note"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (front_dict, body). No YAML dep — simple key:value parser."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    front_raw, body = m.group(1), m.group(2)
    front: dict = {}
    for line in front_raw.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            front[key.strip()] = val.strip().strip('"').strip("'")
    return front, body


def run_preserve_import(
    files: list[FileEntry],
    knowledge: Any,
) -> dict:
    """Mirror source folders into topics. Pure file mapping, no LLM.

    Path scheme: ``topics/<lowercased-folder-chain>/notes/<safe-filename>``.
    """
    created: list[str] = []
    topics_touched: set[str] = set()
    for f in files:
        if not f.relpath.lower().endswith((".md", ".txt")):
            continue
        parts = f.relpath.split("/")
        folder_chain = "/".join(p.lower() for p in parts[:-1]) if len(parts) > 1 else ""
        topic_path = f"topics/{folder_chain}" if folder_chain else "topics"
        topics_touched.add(topic_path)

        text = f.content.decode("utf-8", errors="replace")
        if f.relpath.lower().endswith(".md"):
            front, body = _parse_frontmatter(text)
            title = front.get("title") or parts[-1].rsplit(".", 1)[0]
            tags = [t.strip() for t in front.get("tags", "").split(",") if t.strip()]
        else:
            body = text
            title = parts[-1].rsplit(".", 1)[0]
            tags = []

        path = knowledge.write(
            title=title,
            content=body,
            tags=tags,
            topic=topic_path,
        )
        # Mark as already organized — preserve mode trusts the source layout
        knowledge._storage.execute(
            "UPDATE knowledge_notes SET organizer_state='ok' WHERE path=?",
            (path,),
        )
        created.append(path)

    for topic in topics_touched:
        try:
            knowledge._build_topic_index(topic)
        except Exception:
            pass
    return {"mode": "preserve", "created": created, "topics": list(topics_touched)}


def run_suggest_import(
    files: list[FileEntry],
    knowledge: Any,
) -> dict:
    """Drop files into ``imports/<YYYY-MM-DD>/`` and let the organizer sort them."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    bucket = f"imports/{today}"

    created: list[str] = []
    for f in files:
        if not f.relpath.lower().endswith((".md", ".txt")):
            continue
        text = f.content.decode("utf-8", errors="replace")
        if f.relpath.lower().endswith(".md"):
            front, body = _parse_frontmatter(text)
            title = front.get("title") or f.relpath.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        else:
            body = text
            title = f.relpath.rsplit("/", 1)[-1].rsplit(".", 1)[0]

        path = knowledge.write(title=title, content=body, topic=bucket)
        # organizer_state already 'pending' by default (v3 migration)
        created.append(path)

    return {"mode": "suggest", "created": created, "bucket": bucket}
```

- [ ] **Step 4: Run the detect tests**

Run: `pytest tests/test_import_detect.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/import_pipeline.py tests/test_import_detect.py
git commit -m "feat(knowledge): import_pipeline scaffold + detect_import_mode"
```

---

## Task 2: Preserve mode unit test with fixture

**Files:**
- Create: `tests/fixtures/obsidian-vault/journal/2026-04-01.md`
- Create: `tests/fixtures/obsidian-vault/projects/mycelos.md`
- Create: `tests/fixtures/obsidian-vault/recipes/coffee.md`
- Test: `tests/test_import_preserve.py`

- [ ] **Step 1: Create fixture files**

`tests/fixtures/obsidian-vault/journal/2026-04-01.md`:

```markdown
---
title: Journal 2026-04-01
tags: journal, daily
---

Today I finally fixed the parser bug.
```

`tests/fixtures/obsidian-vault/projects/mycelos.md`:

```markdown
---
title: Mycelos
tags: project
---

The AI that grows with you.
```

`tests/fixtures/obsidian-vault/recipes/coffee.md`:

```markdown
---
title: Espresso
tags: coffee, recipe
---

18g in, 36g out, 28s.
```

- [ ] **Step 2: Write the test**

```python
# tests/test_import_preserve.py
from __future__ import annotations

from pathlib import Path

import pytest

from mycelos.knowledge.import_pipeline import FileEntry, run_preserve_import


@pytest.fixture
def vault_files() -> list[FileEntry]:
    root = Path(__file__).parent / "fixtures" / "obsidian-vault"
    entries: list[FileEntry] = []
    for p in root.rglob("*.md"):
        entries.append(FileEntry(relpath=str(p.relative_to(root)), content=p.read_bytes()))
    return entries


def test_preserve_creates_3_notes(vault_files, integration_knowledge) -> None:
    result = run_preserve_import(vault_files, integration_knowledge)
    assert result["mode"] == "preserve"
    assert len(result["created"]) == 3


def test_preserve_topics_reflect_folders(vault_files, integration_knowledge) -> None:
    result = run_preserve_import(vault_files, integration_knowledge)
    topics = set(result["topics"])
    assert {"topics/journal", "topics/projects", "topics/recipes"} <= topics


def test_preserve_strips_frontmatter(vault_files, integration_knowledge) -> None:
    run_preserve_import(vault_files, integration_knowledge)
    row = integration_knowledge._storage.fetchone(
        "SELECT content, tags FROM knowledge_notes WHERE title='Mycelos'"
    )
    assert row is not None
    assert "The AI that grows with you" in row["content"]
    assert "---" not in row["content"].split("\n", 1)[0]
```

`integration_knowledge` is a fresh `KnowledgeService` bound to a `tmp_path` DB. If the test conftest does not yet offer it, add it to `tests/conftest.py`:

```python
@pytest.fixture
def integration_knowledge(tmp_path) -> Any:
    from mycelos.storage.database import SQLiteStorage
    from mycelos.knowledge.service import KnowledgeService

    storage = SQLiteStorage(tmp_path / "k.db")
    storage.initialize()
    return KnowledgeService(
        storage=storage,
        knowledge_dir=tmp_path / "knowledge",
        user_id="default",
    )
```

- [ ] **Step 3: Run — expect pass**

Run: `pytest tests/test_import_preserve.py -v`
Expected: 3 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/obsidian-vault/ tests/test_import_preserve.py tests/conftest.py
git commit -m "test(knowledge): preserve-mode import with vault fixture"
```

---

## Task 3: Topic Map (auto-Mermaid)

**Files:**
- Create: `src/mycelos/knowledge/topic_map.py`
- Modify: `src/mycelos/knowledge/service.py` (extend `_build_topic_index`)
- Test: `tests/test_topic_map.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_topic_map.py
from __future__ import annotations

from pathlib import Path

import pytest

from mycelos.knowledge.topic_map import build_topic_mermaid


def test_empty_topic_returns_empty_string(integration_knowledge) -> None:
    integration_knowledge.create_topic("Coffee")
    out = build_topic_mermaid("topics/coffee", integration_knowledge)
    assert out == ""


def test_topic_with_one_note_renders_graph(integration_knowledge) -> None:
    integration_knowledge.create_topic("Coffee")
    integration_knowledge.write(title="Espresso", content="body", topic="topics/coffee")
    out = build_topic_mermaid("topics/coffee", integration_knowledge)
    assert out.startswith("```mermaid")
    assert "graph" in out
    assert "Espresso" in out
    assert out.rstrip().endswith("```")


def test_topic_with_wikilink_edge(integration_knowledge) -> None:
    integration_knowledge.create_topic("Coffee")
    integration_knowledge.write(title="Espresso", content="Try [[topics/coffee/grinder]]",
                                topic="topics/coffee")
    integration_knowledge.write(title="Grinder", content="body", topic="topics/coffee")
    out = build_topic_mermaid("topics/coffee", integration_knowledge)
    # Should contain an edge between the two sub-notes
    assert "-->" in out


def test_large_topic_wraps_in_details(integration_knowledge) -> None:
    integration_knowledge.create_topic("Big")
    for i in range(16):
        integration_knowledge.write(title=f"Note {i}", content="x", topic="topics/big")
    out = build_topic_mermaid("topics/big", integration_knowledge)
    assert "<details>" in out
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_topic_map.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement**

Create `src/mycelos/knowledge/topic_map.py`:

```python
"""Build a Mermaid graph of a topic and its sub-notes.

The emitted block is embedded by ``_build_topic_index`` at the top of
every topic-index markdown file. Manually-authored mermaid in other
notes is untouched.
"""
from __future__ import annotations

from typing import Any

_MAX_OPEN = 15


def _node_id(path: str) -> str:
    return "n_" + path.replace("/", "_").replace("-", "_")


def _label(note: dict) -> str:
    title = note.get("title") or note["path"].rsplit("/", 1)[-1]
    return title.replace('"', "'")[:40]


def build_topic_mermaid(topic_path: str, knowledge: Any) -> str:
    """Return a fenced mermaid code block, or '' if the topic has no sub-notes."""
    notes = knowledge._storage.fetchall(
        "SELECT path, title, content FROM knowledge_notes "
        "WHERE parent_path=? AND status!='archived' ORDER BY path",
        (topic_path,),
    )
    if not notes:
        return ""

    lines: list[str] = ["graph TD"]
    topic_node = _node_id(topic_path)
    topic_label = topic_path.rsplit("/", 1)[-1] or topic_path
    lines.append(f'  {topic_node}(("{topic_label}"))')

    note_ids: dict[str, str] = {}
    for note in notes:
        nid = _node_id(note["path"])
        note_ids[note["path"]] = nid
        lines.append(f'  {nid}["{_label(note)}"]')
        lines.append(f"  {topic_node} --> {nid}")

    # Wikilink edges between sub-notes
    for note in notes:
        for link in knowledge._extract_wikilinks(note.get("content") or ""):
            if link in note_ids and link != note["path"]:
                lines.append(f"  {note_ids[note['path']]} --> {note_ids[link]}")

    body = "\n".join(lines)
    fenced = f"```mermaid\n{body}\n```"
    if len(notes) >= 16:
        return f"<details><summary>Topic Map</summary>\n\n{fenced}\n\n</details>\n"
    return fenced + "\n"
```

- [ ] **Step 4: Run the mermaid tests**

Run: `pytest tests/test_topic_map.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Extend `_build_topic_index`**

Open `src/mycelos/knowledge/service.py` and find `_build_topic_index`. At the top of its rendered markdown (before the existing list of sub-notes), prepend the mermaid block:

```python
from mycelos.knowledge.topic_map import build_topic_mermaid
# ...
def _build_topic_index(self, topic_path: str) -> None:
    # ... existing logic that computes `md` markdown body ...
    mermaid_block = build_topic_mermaid(topic_path, self)
    if mermaid_block:
        md = mermaid_block + "\n" + md
    # ... rest unchanged ...
```

Adjust to the exact variable names that `_build_topic_index` already uses — the hook is "insert `mermaid_block` before the current body".

- [ ] **Step 6: Run the knowledge tests to check for regressions**

Run: `pytest tests/test_knowledge.py tests/test_topic_map.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/knowledge/topic_map.py src/mycelos/knowledge/service.py tests/test_topic_map.py
git commit -m "feat(knowledge): Topic Map auto-Mermaid in topic-index notes"
```

---

## Task 4: `POST /api/knowledge/import` endpoint

**Files:**
- Modify: `src/mycelos/gateway/routes.py`
- Test: `tests/test_knowledge_import_api.py`

- [ ] **Step 1: Write the failing API test**

```python
# tests/test_knowledge_import_api.py
from __future__ import annotations

import io
import zipfile

import pytest
from starlette.testclient import TestClient


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for relpath, body in entries.items():
            zf.writestr(relpath, body)
    return buf.getvalue()


def test_import_auto_preserve_three_folders(integration_app) -> None:
    client = TestClient(integration_app)
    zip_data = _zip_bytes({
        "journal/a.md": "# A\nbody",
        "projects/b.md": "# B\nbody",
        "recipes/c.md": "# C\nbody",
    })
    resp = client.post(
        "/api/knowledge/import",
        data={"mode": "auto"},
        files={"file": ("vault.zip", zip_data, "application/zip")},
        headers={"X-User-Id": "default"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "preserve"
    assert len(data["created"]) == 3


def test_import_auto_flat_is_suggest(integration_app) -> None:
    client = TestClient(integration_app)
    zip_data = _zip_bytes({f"note_{i}.md": f"# {i}\nbody" for i in range(5)})
    resp = client.post(
        "/api/knowledge/import",
        data={"mode": "auto"},
        files={"file": ("flat.zip", zip_data, "application/zip")},
        headers={"X-User-Id": "default"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "suggest"
    assert len(data["created"]) == 5


def test_import_forced_preserve_overrides_detection(integration_app) -> None:
    client = TestClient(integration_app)
    zip_data = _zip_bytes({"a.md": "# A\nbody", "b.md": "# B\nbody"})
    resp = client.post(
        "/api/knowledge/import",
        data={"mode": "preserve"},
        files={"file": ("flat.zip", zip_data, "application/zip")},
        headers={"X-User-Id": "default"},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "preserve"
```

- [ ] **Step 2: Run — expect 404**

Run: `pytest tests/test_knowledge_import_api.py -v`
Expected: FAIL with 404.

- [ ] **Step 3: Add the endpoint**

In `src/mycelos/gateway/routes.py`:

```python
import io
import zipfile

from mycelos.knowledge.import_pipeline import (
    FileEntry,
    detect_import_mode,
    run_preserve_import,
    run_suggest_import,
)


@router.post("/api/knowledge/import")
async def import_knowledge(request: Request) -> JSONResponse:
    user_id = request.headers.get("X-User-Id", "default")
    form = await request.form()
    mode_arg = form.get("mode", "auto")
    upload = form.get("file")
    if upload is None:
        return JSONResponse({"error": "file is required"}, status_code=422)

    blob = await upload.read()

    # Accept zips only in this milestone — directory uploads come later.
    entries: list[FileEntry] = []
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                entries.append(FileEntry(relpath=name, content=zf.read(name)))
    except zipfile.BadZipFile:
        return JSONResponse({"error": "file must be a zip archive"}, status_code=422)

    mode = mode_arg if mode_arg in ("preserve", "suggest") else detect_import_mode(entries)

    app = request.app.state.mycelos_app
    knowledge = app.knowledge_for(user_id)

    if mode == "preserve":
        result = run_preserve_import(entries, knowledge)
    else:
        result = run_suggest_import(entries, knowledge)
        # Kick the organizer once synchronously so the inbox fills up
        handler = app.agent_handlers.get("knowledge-organizer")
        if handler is not None:
            handler.run(user_id)

    app.audit.log(
        "knowledge.import",
        user_id=user_id,
        details={"mode": mode, "count": len(result.get("created", []))},
    )
    return JSONResponse(result)
```

- [ ] **Step 4: Run the API tests**

Run: `pytest tests/test_knowledge_import_api.py -v`
Expected: 3 tests PASS. (The suggest test will also invoke the organizer; make sure the integration fixture stubs the broker or uses cassettes.)

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/gateway/routes.py tests/test_knowledge_import_api.py
git commit -m "feat(knowledge): POST /api/knowledge/import (zip, preserve+suggest)"
```

---

## Task 5: Suggest-mode integration test

**Files:**
- Create: `tests/fixtures/flat-notes/` (10 `.md` files)
- Create: `tests/integration/test_import_live.py`

- [ ] **Step 1: Create fixture files**

Create 10 files `tests/fixtures/flat-notes/note_00.md` .. `note_09.md`. Each contains:

```markdown
# Idea {N}

A stray thought about {topic}.
```

Vary `{topic}` across: coffee, running, dev, books, music, recipes, travel, photography, finance, garden. One topic per file.

- [ ] **Step 2: Write the test**

```python
# tests/integration/test_import_live.py
from __future__ import annotations

from pathlib import Path

import pytest

from mycelos.knowledge.import_pipeline import FileEntry, run_suggest_import


@pytest.mark.integration
def test_suggest_import_creates_inbox_suggestions(integration_app) -> None:
    app_obj = integration_app.state.mycelos_app
    knowledge = app_obj.knowledge_for("default")

    root = Path(__file__).parent.parent / "fixtures" / "flat-notes"
    entries = [FileEntry(relpath=p.name, content=p.read_bytes()) for p in root.glob("*.md")]
    assert len(entries) == 10

    result = run_suggest_import(entries, knowledge)
    assert result["mode"] == "suggest"
    assert len(result["created"]) == 10

    handler = app_obj.agent_handlers["knowledge-organizer"]
    run_result = handler.run("default")

    assert run_result["processed"] >= 10
    # The organizer should have produced at least one suggestion of some kind
    assert run_result["suggested"] + run_result["moved"] >= 1
```

- [ ] **Step 3: Record the cassette, then run in replay**

Run: `MYCELOS_LLM_CASSETTE=auto ANTHROPIC_API_KEY=... pytest tests/integration/test_import_live.py -v`
Then: `pytest tests/integration/test_import_live.py -v`
Expected: PASS in replay mode without API key.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/flat-notes/ tests/integration/test_import_live.py tests/cassettes/
git commit -m "test(knowledge): suggest-mode import integration with cassette"
```

---

## Task 6: Smart Import modal + drag-drop UI

**Files:**
- Modify: `src/mycelos/frontend/pages/knowledge.html`
- Modify: `src/mycelos/i18n/locales/en.yaml`
- Modify: `src/mycelos/i18n/locales/de.yaml`

- [ ] **Step 1: Add i18n strings**

`en.yaml`:

```yaml
import:
  button: "Import"
  modal_title: "Import notes"
  file_label: "Choose a zip file or drop it here"
  options_toggle: "Other options…"
  option_auto: "Smart default"
  option_preserve: "Preserve structure 1:1"
  option_suggest: "Let Mycelos reorganize"
  submit: "Import"
  importing: "Importing…"
  done_preserve: "Imported {count} notes into {topics} topics"
  done_suggest: "Imported {count} notes — see the inbox"
```

`de.yaml`:

```yaml
import:
  button: "Importieren"
  modal_title: "Notizen importieren"
  file_label: "Zip-Datei wählen oder hier ablegen"
  options_toggle: "Andere Optionen…"
  option_auto: "Smart default"
  option_preserve: "Struktur 1:1"
  option_suggest: "Mycelos neu organisieren"
  submit: "Importieren"
  importing: "Importiere…"
  done_preserve: "{count} Notizen in {topics} Topics importiert"
  done_suggest: "{count} Notizen importiert — siehe Inbox"
```

- [ ] **Step 2: Add the button + modal to `knowledge.html`**

Top-right of the topic tree column, add:

```html
<button @click="importOpen = true"
        class="btn-secondary"
        x-text="$t('import.button')"></button>
```

At the bottom of the page body, add the modal + drag-drop component:

```html
<div x-data="smartImport()"
     @dragover.prevent="dragOver = true"
     @dragleave.prevent="dragOver = false"
     @drop.prevent="onDrop($event)"
     :class="{ 'drag-over': dragOver }">

  <div x-show="importOpen" x-cloak
       class="quick-capture-overlay"
       @click.self="importOpen = false">
    <div class="quick-capture-modal" style="width:min(640px,90vw)">
      <div class="quick-capture-header" x-text="$t('import.modal_title')"></div>

      <label style="display:block;margin-top:12px;color:#cdd5e0;font-size:13px"
             x-text="$t('import.file_label')"></label>
      <input type="file" accept=".zip" @change="file = $event.target.files[0]"
             style="margin-top:8px;color:#fff" />

      <div style="margin-top:14px">
        <a href="#" @click.prevent="showOptions = !showOptions"
           style="color:#6ad6ff;font-size:12px"
           x-text="$t('import.options_toggle')"></a>
        <div x-show="showOptions" style="margin-top:8px;display:flex;gap:12px;font-size:12px;color:#cdd5e0">
          <label><input type="radio" value="auto" x-model="mode" /> <span x-text="$t('import.option_auto')"></span></label>
          <label><input type="radio" value="preserve" x-model="mode" /> <span x-text="$t('import.option_preserve')"></span></label>
          <label><input type="radio" value="suggest" x-model="mode" /> <span x-text="$t('import.option_suggest')"></span></label>
        </div>
      </div>

      <div style="margin-top:16px;display:flex;justify-content:flex-end;gap:8px">
        <button @click="submit()" :disabled="!file || busy"
                class="btn-primary"
                x-text="busy ? $t('import.importing') : $t('import.submit')"></button>
      </div>
      <div x-show="result" style="margin-top:12px;color:#6ad6ff;font-size:12px"
           x-text="resultMessage()"></div>
    </div>
  </div>
</div>

<script>
  function smartImport() {
    return {
      importOpen: false,
      dragOver: false,
      showOptions: false,
      mode: 'auto',
      file: null,
      busy: false,
      result: null,
      onDrop(event) {
        this.dragOver = false;
        if (event.dataTransfer.files && event.dataTransfer.files.length) {
          this.file = event.dataTransfer.files[0];
          this.importOpen = true;
        }
      },
      async submit() {
        if (!this.file) return;
        this.busy = true;
        this.result = null;
        const form = new FormData();
        form.append('mode', this.mode);
        form.append('file', this.file);
        try {
          const resp = await fetch('/api/knowledge/import', {
            method: 'POST',
            headers: { 'X-User-Id': 'default' },
            body: form,
          });
          this.result = await resp.json();
        } finally {
          this.busy = false;
        }
      },
      resultMessage() {
        if (!this.result) return '';
        const count = (this.result.created || []).length;
        if (this.result.mode === 'preserve') {
          return (window.t ? window.t('import.done_preserve', { count, topics: (this.result.topics || []).length }) : `Imported ${count}`);
        }
        return (window.t ? window.t('import.done_suggest', { count }) : `Imported ${count}`);
      },
    };
  }
</script>

<style>
  .drag-over::after {
    content: '';
    position: fixed; inset: 0;
    border: 3px dashed rgba(106,214,255,0.7);
    pointer-events: none;
    z-index: 9998;
  }
</style>
```

- [ ] **Step 3: Manually verify**

Load `/knowledge`. Click Import, pick a small zip, submit. Expected: success message. Drag-drop a zip onto the page: the drag-over border appears, dropping opens the modal with the file pre-selected.

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/frontend/pages/knowledge.html src/mycelos/i18n/locales/en.yaml src/mycelos/i18n/locales/de.yaml
git commit -m "feat(frontend): Smart Import modal + drag-drop on Knowledge page"
```

---

## Task 7: Playwright E2E for Smart Import

**Files:**
- Create: `tests/e2e/test_smart_import.py`

- [ ] **Step 1: Write the test**

```python
# tests/e2e/test_smart_import.py
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


def _make_zip(tmp_path: Path) -> Path:
    p = tmp_path / "vault.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("journal/a.md", "# A\nbody")
        zf.writestr("projects/b.md", "# B\nbody")
        zf.writestr("recipes/c.md", "# C\nbody")
    return p


@pytest.mark.e2e
def test_import_button_opens_modal(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/knowledge")
    page.get_by_text("Import", exact=False).first.click()
    expect(page.locator(".quick-capture-modal")).to_be_visible()


@pytest.mark.e2e
def test_import_zip_flow(page: Page, live_server: str, tmp_path: Path) -> None:
    zip_path = _make_zip(tmp_path)
    page.goto(f"{live_server}/knowledge")
    page.get_by_text("Import", exact=False).first.click()
    page.locator('input[type="file"]').set_input_files(str(zip_path))
    page.get_by_text("Importieren", exact=False).or_(
        page.get_by_text("Import", exact=True)
    ).last.click()
    expect(page.locator(".quick-capture-modal")).to_contain_text("3", timeout=5000)
```

- [ ] **Step 2: Run**

Run: `pytest tests/e2e/test_smart_import.py -v -m e2e`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_smart_import.py
git commit -m "test(knowledge): Playwright E2E for Smart Import"
```

---

## Task 8: CHANGELOG + final verification

- [ ] **Step 1: Add changelog**

Under the current week in `CHANGELOG.md`:

```markdown
### Knowledge — Smart Import + Topic Map (Milestone C)
- Smart Import: zip upload with auto detection (≥3 folders → preserve, otherwise suggest)
- Preserve mode mirrors source folders into topics, parses frontmatter, skips LLM
- Suggest mode drops files into `imports/YYYY-MM-DD/` and invokes the organizer
- `POST /api/knowledge/import` multipart endpoint
- Knowledge page Import button + drag-drop zone + mode radios
- Topic Map: auto-generated Mermaid graph embedded in every topic-index note, with `<details>` wrapping for topics with ≥16 sub-notes
```

- [ ] **Step 2: Run the fast test suite**

Run: `pytest tests/test_import_detect.py tests/test_import_preserve.py tests/test_topic_map.py tests/test_knowledge_import_api.py -v`
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): Knowledge Milestone C — Smart Import + Topic Map"
```
