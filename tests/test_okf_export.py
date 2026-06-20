"""Unit tests for the OKF (Open Knowledge Format) export serializer.

OKF is a boundary format (D1): the internal Note + SQLite index stay
authoritative. These tests pin the serializer contract — frontmatter
mapping, bundle layout, reserved index.md synthesis, and round-trip back
into a Note.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.knowledge.note import parse_frontmatter
from mycelos.knowledge.okf_export import (
    build_okf_bundle,
    note_to_okf_frontmatter,
)


@pytest.fixture
def app():
    from mycelos.app import App

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-okf"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def kb(app):
    from mycelos.knowledge.service import KnowledgeBase

    return KnowledgeBase(app)


# ── Frontmatter mapping ────────────────────────────────────────────────────


def test_frontmatter_always_has_required_type():
    fm = note_to_okf_frontmatter({"type": "note", "title": "X", "content": "b"})
    assert fm["type"] == "note"


def test_frontmatter_defaults_type_when_missing():
    # OKF requires `type`; a note dict lacking it must still get one.
    fm = note_to_okf_frontmatter({"title": "X", "content": "b"})
    assert fm["type"]  # non-empty


def test_frontmatter_maps_timestamp_from_updated_at():
    fm = note_to_okf_frontmatter(
        {"type": "note", "title": "X", "updated_at": "2026-06-20T10:00:00Z",
         "created_at": "2026-06-01T10:00:00Z"}
    )
    assert fm["timestamp"] == "2026-06-20T10:00:00Z"


def test_frontmatter_timestamp_falls_back_to_created_at():
    fm = note_to_okf_frontmatter(
        {"type": "note", "title": "X", "created_at": "2026-06-01T10:00:00Z"}
    )
    assert fm["timestamp"] == "2026-06-01T10:00:00Z"


def test_frontmatter_derives_description_from_first_paragraph():
    note = {
        "type": "note",
        "title": "Coffee",
        "content": "# Coffee\n\nDark roast beats light.\n\nSecond para.",
    }
    fm = note_to_okf_frontmatter(note)
    assert fm["description"] == "Dark roast beats light."


def test_frontmatter_description_empty_for_heading_only_body():
    fm = note_to_okf_frontmatter(
        {"type": "topic", "title": "Drinks", "content": "# Drinks\n"}
    )
    assert fm.get("description", "") == ""


def test_frontmatter_resource_from_source_url():
    fm = note_to_okf_frontmatter(
        {"type": "reference", "title": "X",
         "source": {"kind": "web", "url": "https://example.com/a"}}
    )
    assert fm["resource"] == "https://example.com/a"


def test_frontmatter_resource_from_source_filename():
    fm = note_to_okf_frontmatter(
        {"type": "document", "title": "X",
         "source": {"kind": "document", "filename": "report.pdf"}}
    )
    assert fm["resource"] == "report.pdf"


def test_frontmatter_preserves_existing_mycelos_keys():
    # D3: additive mapping — Mycelos-specific keys must round-trip.
    note = {
        "type": "task", "title": "Pay rent", "content": "body",
        "status": "open", "priority": 2, "parent_path": "tasks",
        "links": ["notes/a"], "created_by": "user", "tags": ["money"],
    }
    fm = note_to_okf_frontmatter(note)
    assert fm["status"] == "open"
    assert fm["priority"] == 2
    assert fm["parent_path"] == "tasks"
    assert fm["links"] == ["notes/a"]
    assert fm["created_by"] == "user"
    assert fm["tags"] == ["money"]


# ── Bundle layout ──────────────────────────────────────────────────────────
#
# Membership is carried by `parent_path` on the *list row*, not by the file
# path: KnowledgeBase.write() lays a note out at `notes/<slug>` regardless of
# its topic, and read() does NOT echo parent_path back. So the bundle layout
# must derive a note's OKF directory from its parent_path, and read_fn supplies
# only the body/frontmatter.


def _read_fn_from(notes_by_path: dict[str, dict]):
    return lambda path: notes_by_path.get(path)


def test_bundle_places_topicless_notes_at_root():
    notes = [
        {"path": "notes/coffee", "type": "note", "title": "Coffee",
         "content": "body", "parent_path": None},
        {"path": "tasks/pay-rent", "type": "task", "title": "Pay rent",
         "content": "body", "parent_path": None},
    ]
    bundle = build_okf_bundle(notes, _read_fn_from({n["path"]: n for n in notes}))
    assert "coffee.md" in bundle
    assert "pay-rent.md" in bundle


def test_bundle_nests_note_under_its_topic_dir():
    # A note whose parent_path is topics/drinks lands under topics/drinks/,
    # even though its stored path is notes/cold-brew.
    notes = [
        {"path": "topics/drinks", "type": "topic", "title": "Drinks",
         "content": "# Drinks\n", "parent_path": None},
        {"path": "notes/cold-brew", "type": "note", "title": "Cold Brew",
         "content": "Steep 18h.", "parent_path": "topics/drinks"},
    ]
    bundle = build_okf_bundle(notes, _read_fn_from({n["path"]: n for n in notes}))
    assert "topics/drinks/cold-brew.md" in bundle


def test_bundle_files_have_frontmatter_and_body():
    notes = [{"path": "notes/coffee", "type": "note", "title": "Coffee",
              "content": "Dark roast.", "parent_path": None}]
    bundle = build_okf_bundle(notes, _read_fn_from({n["path"]: n for n in notes}))
    text = bundle["coffee.md"]
    assert text.startswith("---")
    assert "type: note" in text
    assert "Dark roast." in text


def test_bundle_synthesizes_root_index():
    notes = [{"path": "notes/coffee", "type": "note", "title": "Coffee",
              "content": "b", "parent_path": None}]
    bundle = build_okf_bundle(notes, _read_fn_from({n["path"]: n for n in notes}))
    assert "index.md" in bundle


def test_bundle_synthesizes_per_topic_index_with_child_links():
    notes = [
        {"path": "topics/drinks", "type": "topic", "title": "Drinks",
         "content": "# Drinks\n", "parent_path": None},
        {"path": "notes/coffee", "type": "note", "title": "Coffee",
         "content": "b", "parent_path": "topics/drinks"},
    ]
    bundle = build_okf_bundle(notes, _read_fn_from({n["path"]: n for n in notes}))
    assert "topics/drinks/index.md" in bundle
    idx = bundle["topics/drinks/index.md"]
    # Child note is linked from its topic index.
    assert "coffee" in idx.lower()


def test_bundle_empty_has_only_root_index():
    bundle = build_okf_bundle([], _read_fn_from({}))
    assert list(bundle.keys()) == ["index.md"]


def test_root_index_lists_top_level_directories():
    # The root index is the bundle's entry point: it must surface top-level
    # topic directories, not just loose root notes (which usually don't exist).
    notes = [
        {"path": "topics/drinks", "type": "topic", "title": "Drinks",
         "content": "# Drinks\n", "parent_path": None},
        {"path": "notes/coffee", "type": "note", "title": "Coffee",
         "content": "b", "parent_path": "topics/drinks"},
    ]
    bundle = build_okf_bundle(notes, _read_fn_from({n["path"]: n for n in notes}))
    root = bundle["index.md"]
    assert "topics/drinks" in root
    assert "_No entries._" not in root


def test_bundle_skips_notes_that_read_returns_none():
    # A note listed but unreadable (deleted between list and read) is skipped,
    # not fatal.
    notes = [{"path": "notes/ghost", "type": "note", "title": "Ghost",
              "parent_path": None}]
    bundle = build_okf_bundle(notes, _read_fn_from({}))  # read returns None
    assert "ghost.md" not in bundle
    assert "index.md" in bundle


# ── Round-trip ─────────────────────────────────────────────────────────────


def test_bundle_file_roundtrips_into_equivalent_note():
    notes = [{
        "path": "notes/coffee", "type": "note", "title": "Coffee",
        "content": "Dark roast.", "tags": ["drinks"], "status": "active",
        "parent_path": None,
    }]
    bundle = build_okf_bundle(notes, _read_fn_from({n["path"]: n for n in notes}))
    parsed = parse_frontmatter(bundle["coffee.md"])
    assert parsed.title == "Coffee"
    assert parsed.type == "note"
    assert parsed.tags == ["drinks"]
    assert "Dark roast." in parsed.content


# ── Integration with a real KnowledgeBase ──────────────────────────────────


def test_export_excludes_archived(kb):
    kb.write(title="Keep me", content="body", type="note")
    archived = kb.write(title="Hide me", content="body", type="note")
    kb.archive_note(archived)

    notes = [n for n in kb.list_notes(limit=5000) if n.get("status") != "archived"]
    bundle = build_okf_bundle(notes, kb.read)

    titles = "\n".join(bundle.values())
    assert "Keep me" in titles
    assert "Hide me" not in titles
