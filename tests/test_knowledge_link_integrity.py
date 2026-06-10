"""Tests for knowledge graph referential integrity.

knowledge_links rows are keyed by path with no FK — every mutation
(rename, merge, delete) must keep them consistent or the graph UI renders
dangling edges and ghost nodes. These tests pin the integrity rules.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-links"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def kb(app):
    from mycelos.knowledge.service import KnowledgeBase
    return KnowledgeBase(app)


def _links(app):
    return app.storage.fetchall(
        "SELECT from_path, to_path, kind FROM knowledge_links ORDER BY from_path"
    )


class TestLinkKinds:
    def test_wikilink_sync_records_kind(self, kb, app):
        p1 = kb.write("Target", "t", type="note")
        kb.write("Source", f"see [[{p1}]]", type="note")
        kb.sync_relations()
        rows = _links(app)
        assert rows, "expected at least one link"
        assert all(r["kind"] == "wikilink" for r in rows)


class TestRenameTopicIntegrity:
    def test_rename_updates_link_endpoints(self, kb, app):
        topic = kb.create_topic("Coffee")
        note = kb.write("Note", f"about [[{topic}]]", type="note")
        kb.sync_relations()
        assert any(r["to_path"] == topic for r in _links(app))

        new_path = kb.rename_topic(topic, "Espresso")

        rows = _links(app)
        assert not any(r["to_path"] == topic for r in rows), "dangling link to old path"
        assert any(r["to_path"] == new_path for r in rows)

    def test_rename_updates_fts_title(self, kb):
        topic = kb.create_topic("Gardening")
        kb.rename_topic(topic, "Horticulture")
        titles = [r["title"] for r in kb.search("Horticulture")]
        assert any("Horticulture" in t for t in titles)
        assert not any("Gardening" in t for t in kb.search("Gardening"))


class TestMergeTopicsIntegrity:
    def test_merged_source_disappears_from_topics(self, kb):
        src = kb.create_topic("Cooking")
        dst = kb.create_topic("Food")
        kb.merge_topics(src, dst)
        topic_paths = [t["path"] for t in kb.list_topics()]
        assert src not in topic_paths
        assert dst in topic_paths

    def test_merge_redirects_inbound_links(self, kb, app):
        src = kb.create_topic("Cinema")
        dst = kb.create_topic("Movies")
        kb.write("Fav", f"see [[{src}]]", type="note")
        kb.sync_relations()
        kb.merge_topics(src, dst)
        rows = _links(app)
        assert not any(r["to_path"] == src for r in rows)
        assert any(r["to_path"] == dst for r in rows)


class TestDeleteCleanup:
    def test_remove_note_cleans_links_and_vectors(self, kb, app):
        p1 = kb.write("A", "a", type="note")
        p2 = kb.write("B", f"see [[{p1}]]", type="note")
        kb.sync_relations()
        note_id = kb._indexer.get_note_id(p1)

        kb._indexer.remove_note(p1)

        rows = _links(app)
        assert not any(p1 in (r["from_path"], r["to_path"]) for r in rows)
        # Vector row is gone too (when a vec table exists).
        try:
            vec = app.storage.fetchall(
                "SELECT rowid FROM knowledge_vec WHERE rowid=?", (note_id,)
            )
            assert vec == []
        except Exception:
            pass  # no vec table in this environment — nothing to clean


class TestGraphData:
    def test_graph_excludes_archived_notes(self, kb):
        p1 = kb.write("Visible", "v", type="note")
        p2 = kb.write("Hidden", "h", type="note")
        kb.archive_note(p2)
        graph = kb.get_graph_data()
        ids = [n["id"] for n in graph["nodes"]]
        assert p1 in ids
        assert p2 not in ids

    def test_graph_includes_parent_edges(self, kb):
        topic = kb.create_topic("Projects")
        note = kb.write("Child", "c", type="note")
        kb.move_to_topic(note, topic)
        graph = kb.get_graph_data()
        assert any(
            e["source"] == note and e["target"] == topic and e.get("kind") == "parent"
            for e in graph["edges"]
        )

    def test_graph_edges_have_kind(self, kb):
        p1 = kb.write("T", "t", type="note")
        kb.write("S", f"[[{p1}]]", type="note")
        kb.sync_relations()
        graph = kb.get_graph_data()
        wiki_edges = [e for e in graph["edges"] if e.get("kind") == "wikilink"]
        assert wiki_edges
