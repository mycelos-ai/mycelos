"""Tests for Knowledge Base — notes, search, embeddings, context enrichment."""

import os
import tempfile
from pathlib import Path
import pytest

from mycelos.knowledge.note import Note, parse_frontmatter, render_note


# ─── Service fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-kb"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def kb(app):
    from mycelos.knowledge.service import KnowledgeBase
    return KnowledgeBase(app)


class TestNoteModel:
    def test_create_note(self):
        note = Note(title="Test Note", content="Hello world", type="note")
        assert note.title == "Test Note"
        assert note.type == "note"
        assert note.status == "active"
        assert note.priority == 0

    def test_note_to_markdown(self):
        note = Note(title="My Task", content="Do something", type="task",
                    tags=["urgent"], status="open", due="2026-03-28", priority=2)
        md = render_note(note)
        assert "title: My Task" in md
        assert "type: task" in md
        assert "priority: 2" in md
        assert "Do something" in md

    def test_parse_frontmatter(self):
        md = "---\ntitle: Test\ntype: fact\ntags:\n- a\n- b\nstatus: active\npriority: 1\ndue: null\n---\n\nSome content here."
        note = parse_frontmatter(md)
        assert note.title == "Test"
        assert note.type == "fact"
        assert note.tags == ["a", "b"]
        assert note.priority == 1
        assert note.content == "Some content here."

    def test_parse_frontmatter_no_frontmatter(self):
        md = "Just plain text without frontmatter"
        note = parse_frontmatter(md)
        assert note.title == ""
        assert note.content == "Just plain text without frontmatter"

    def test_note_path_generation(self):
        note = Note(title="Security Fail Closed", type="decision")
        assert note.generate_path() == "decisions/security-fail-closed"

    def test_task_path_generation(self):
        note = Note(title="Fix Planner", type="task")
        assert note.generate_path() == "tasks/fix-planner"

    def test_fact_path_generation(self):
        note = Note(title="Stefan likes Python", type="fact")
        assert note.generate_path() == "facts/stefan-likes-python"

    def test_render_and_parse_roundtrip(self):
        original = Note(title="Roundtrip", content="Test content",
                        type="decision", tags=["test"], priority=1)
        md = render_note(original)
        parsed = parse_frontmatter(md)
        assert parsed.title == original.title
        assert parsed.type == original.type
        assert parsed.tags == original.tags
        assert parsed.priority == original.priority
        assert parsed.content == original.content


class TestKnowledgeBaseCRUD:
    def test_write_creates_file(self, kb, app):
        path = kb.write("Test Note", "Hello world", type="note")
        full_path = app.data_dir / "knowledge" / (path + ".md")
        assert full_path.exists()

    def test_write_creates_index_entry(self, kb):
        path = kb.write("My Task", "Do something", type="task", due="2026-03-28")
        result = kb.read(path)
        assert result is not None
        assert result["title"] == "My Task"
        assert result["due"] == "2026-03-28"

    def test_read_nonexistent(self, kb):
        assert kb.read("nonexistent/path") is None

    def test_list_by_type(self, kb):
        kb.write("Task 1", "Do A", type="task", status="open")
        kb.write("Task 2", "Do B", type="task", status="open")
        kb.write("Note 1", "Info", type="note")
        tasks = kb.list_notes(type="task")
        assert len(tasks) == 2

    def test_update_status(self, kb):
        path = kb.write("My Task", "stuff", type="task", status="open")
        kb.update(path, status="done")
        result = kb.read(path)
        assert result["status"] == "done"

    def test_update_preserves_content(self, kb):
        path = kb.write("My Note", "Important content", type="note")
        kb.update(path, tags=["updated"])
        result = kb.read(path)
        assert "Important content" in result["content"]

    def test_update_append_content(self, kb):
        path = kb.write("My Note", "First line", type="note")
        kb.update(path, content="Second line", append=True)
        result = kb.read(path)
        assert "First line" in result["content"]
        assert "Second line" in result["content"]

    def test_link_creates_backlink(self, kb):
        p1 = kb.write("Note A", "aaa", type="note")
        p2 = kb.write("Note B", "bbb", type="note")
        kb.link(p1, p2)
        result = kb.read(p2)
        assert p1 in result.get("backlinks", [])

    def test_sync_relations_detects_wikilinks(self, kb):
        p1 = kb.write("Alpha", "aaa", type="note")
        p2 = kb.write("Beta", f"Links to [[{p1}]] and [[Alpha]]", type="note")
        kb.sync_relations()
        result = kb.read(p1)
        assert p2 in result.get("backlinks", [])

    def test_sync_relations_replaces_removed_links(self, kb):
        p1 = kb.write("Source", "Links [[Target One]]", type="note")
        p2 = kb.write("Target One", "t1", type="note")
        p3 = kb.write("Target Two", "t2", type="note")
        kb.sync_relations()
        assert p1 in kb.read(p2).get("backlinks", [])

        kb.update(p1, content="Now links [[Target Two]]")
        kb.sync_relations()
        assert p1 not in kb.read(p2).get("backlinks", [])
        assert p1 in kb.read(p3).get("backlinks", [])

    def test_sync_relations_ignores_unknown_and_self_links(self, kb):
        p1 = kb.write("Self Note", "Points to [[Self Note]] and [[Missing Note]]", type="note")
        stats = kb.sync_relations()
        assert stats["notes"] >= 1
        graph = kb.get_graph_data()
        assert all(edge["target"] != p1 for edge in graph["edges"])

    def test_sync_relations_reads_frontmatter_links(self, kb):
        target = kb.write("Linked Target", "target", type="note")
        source = kb.write("Linked Source", "body", type="note", links=[target])
        kb.sync_relations()
        assert source in kb.read(target).get("backlinks", [])

    def test_read_returns_frontmatter_links(self, kb):
        target = kb.write("T", "x", type="note")
        source = kb.write("S", "x", type="note", links=[target])
        note = kb.read(source)
        assert note is not None
        assert target in note.get("links", [])

    def test_get_graph_data_returns_nodes_edges_stats(self, kb):
        p1 = kb.write("Graph A", "A", type="note")
        p2 = kb.write("Graph B", f"[[{p1}]]", type="note")
        kb.sync_relations()
        graph = kb.get_graph_data()
        assert graph["stats"]["notes"] >= 2
        assert any(node["id"] == p1 for node in graph["nodes"])
        assert any(edge["source"] == p2 and edge["target"] == p1 for edge in graph["edges"])

    def test_extract_wikilinks_handles_aliases_and_whitespace(self, kb):
        links = kb._extract_wikilinks("One [[notes/a|Alias A]], two [[ notes/b ]] and [[Title Only]]")
        assert links == ["notes/a", "notes/b", "Title Only"]

    def test_search_fts(self, kb):
        kb.write("Python Guide", "Python is a programming language", type="reference")
        kb.write("Cooking Recipe", "How to make pasta", type="note")
        results = kb.search("Python programming")
        assert len(results) >= 1
        assert any("Python" in str(r.get("title", "")) for r in results)

    def test_write_with_priority(self, kb):
        path = kb.write("Urgent Task", "ASAP", type="task", priority=2)
        result = kb.read(path)
        assert result["priority"] == 2

    def test_duplicate_path_handling(self, kb):
        p1 = kb.write("Same Title", "First", type="note")
        p2 = kb.write("Same Title", "Second", type="note")
        assert p1 != p2
        assert kb.read(p1) is not None
        assert kb.read(p2) is not None

    def test_app_knowledge_base_property(self, app):
        kb = app.knowledge_base
        assert kb is not None
        assert kb is app.knowledge_base  # Same instance


class TestEmbeddings:
    def test_embedding_provider_fallback(self):
        from mycelos.knowledge.embeddings import get_embedding_provider
        provider = get_embedding_provider(openai_key=None)
        # Without sentence-transformers installed, returns FallbackProvider
        # With it installed, returns LocalEmbeddingProvider
        assert provider.name in ("local", "none")

    def test_serialize_deserialize_embedding(self):
        from mycelos.knowledge.embeddings import serialize_embedding, deserialize_embedding
        original = [0.1, 0.2, 0.3, 0.4, 0.5]
        serialized = serialize_embedding(original)
        assert isinstance(serialized, bytes)
        deserialized = deserialize_embedding(serialized, 5)
        for a, b in zip(original, deserialized):
            assert abs(a - b) < 0.0001

    def test_find_relevant_works(self, kb):
        kb.write("Python Guide", "Python is a great programming language", type="reference")
        kb.write("Cooking Tips", "How to make perfect pasta", type="note")
        results = kb.find_relevant("programming language")
        assert len(results) >= 1

    def test_sqlite_vec_available(self):
        """Verify sqlite-vec extension can be loaded when enable_load_extension is supported."""
        import sqlite3
        import sqlite_vec
        conn = sqlite3.connect(":memory:")
        if not hasattr(conn, "enable_load_extension"):
            pytest.skip("enable_load_extension not available in this Python build")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        # Create a test vector table
        conn.execute("CREATE VIRTUAL TABLE test_vec USING vec0(embedding float[3])")
        conn.commit()
        conn.close()


class TestContextEnrichment:
    def test_find_relevant_returns_notes(self, kb):
        kb.write("Important Decision", "We chose Python over JavaScript", type="decision")
        results = kb.find_relevant("Python JavaScript decision")
        assert len(results) >= 1

    def test_find_relevant_empty_kb(self, kb):
        results = kb.find_relevant("anything")
        assert results == [] or isinstance(results, list)


class TestIndexGeneration:
    def test_index_generated_after_explicit_call(self, kb, app):
        kb.write("Test Note", "Hello", type="note")
        kb.regenerate_index()
        index_path = app.data_dir / "knowledge" / "index.md"
        assert index_path.exists()
        content = index_path.read_text()
        assert "Test Note" in content
        assert "Knowledge Base" in content

    def test_index_shows_open_tasks(self, kb, app):
        kb.write("My Task", "Do it", type="task", status="open", due="2026-03-28")
        kb.regenerate_index()
        index_path = app.data_dir / "knowledge" / "index.md"
        content = index_path.read_text()
        assert "Open Tasks" in content
        assert "My Task" in content

    def test_index_shows_priority(self, kb, app):
        kb.write("Urgent", "Now", type="task", status="open", priority=2)
        kb.regenerate_index()
        index_path = app.data_dir / "knowledge" / "index.md"
        content = index_path.read_text()
        assert "[P2]" in content


class TestLlmTools:
    def test_note_tools_exist_in_tool_list(self, app):
        """Verify note tools are registered."""
        from mycelos.chat.service import CHAT_AGENT_TOOLS
        tool_names = [t["function"]["name"] for t in CHAT_AGENT_TOOLS]
        assert "note_write" in tool_names
        assert "note_read" in tool_names
        assert "note_search" in tool_names
        assert "note_list" in tool_names
        assert "note_update" in tool_names
        assert "note_link" in tool_names


class TestSetReminderRemindAt:
    """set_reminder now accepts an optional remind_at datetime for precise firing."""

    def test_set_reminder_with_remind_at_persists_datetime(self, kb):
        kb.write("Clean grill", "ask Isabella", type="task", status="open")
        notes = kb.list_notes(type="task")
        path = notes[0]["path"]

        ok = kb.set_reminder(path, due="2026-04-12", remind_at="2026-04-12T09:00:00Z")
        assert ok is True

        note = kb.read(path)
        assert note["due"] == "2026-04-12"
        assert note["remind_at"] == "2026-04-12T09:00:00Z"
        assert note["reminder"] is True

    def test_set_reminder_without_remind_at_leaves_it_null(self, kb):
        kb.write("Plain task", "just a task", type="task", status="open")
        path = kb.list_notes(type="task")[0]["path"]

        ok = kb.set_reminder(path, due="2026-04-20")
        assert ok is True

        note = kb.read(path)
        assert note["due"] == "2026-04-20"
        assert note["remind_at"] is None
        assert note["reminder"] is True

    def test_set_reminder_update_clears_previous_remind_at(self, kb):
        """Calling set_reminder without remind_at on a note that previously
        had one clears the datetime — the scheduler should no longer have
        a specific time to fire at."""
        kb.write("Changing task", "changing", type="task", status="open")
        path = kb.list_notes(type="task")[0]["path"]

        kb.set_reminder(path, due="2026-04-12", remind_at="2026-04-12T09:00:00Z")
        kb.set_reminder(path, due="2026-04-13")  # no remind_at

        note = kb.read(path)
        assert note["remind_at"] is None
