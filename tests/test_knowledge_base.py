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

    def test_update_preserves_parent_path(self, kb, app):
        """Editing content/status must not detach a note from its topic.

        Regression for P0-1: update() dropped parent_path because it never
        forwarded the existing value to index_note's UPDATE branch.
        """
        kb.create_topic("Coffee")
        path = kb.write("Espresso tuning", "notes on grind", type="note")
        assert kb.move_to_topic(path, "topics/coffee")
        before = app.storage.fetchone(
            "SELECT parent_path FROM knowledge_notes WHERE path=?", (path,)
        )
        assert before["parent_path"] == "topics/coffee"

        kb.update(path, status="done")

        after = app.storage.fetchone(
            "SELECT parent_path FROM knowledge_notes WHERE path=?", (path,)
        )
        assert after["parent_path"] == "topics/coffee"

    def test_update_preserves_reminder(self, kb, app):
        """Editing a note must not clear a pending reminder (the scheduler
        reads the DB, so wiping it silently drops the reminder)."""
        path = kb.write("Call dentist", "ring them", type="task")
        kb.set_reminder(path, due="2026-07-01", remind_at="2026-07-01T09:00:00Z")
        before = app.storage.fetchone(
            "SELECT reminder, remind_at FROM knowledge_notes WHERE path=?", (path,)
        )
        assert before["reminder"] == 1
        assert before["remind_at"] == "2026-07-01T09:00:00Z"

        kb.update(path, tags=["health"])

        after = app.storage.fetchone(
            "SELECT reminder, remind_at FROM knowledge_notes WHERE path=?", (path,)
        )
        assert after["reminder"] == 1
        assert after["remind_at"] == "2026-07-01T09:00:00Z"

    def test_update_preserves_source_file(self, kb, app):
        """Editing a document note must not destroy its source_file pointer
        (the provenance link to the original document)."""
        path = kb.store_document(b"%PDF-1.4 fake", "report.pdf",
                                 title="Report", summary="A summary")
        before = app.storage.fetchone(
            "SELECT source_file FROM knowledge_notes WHERE path=?", (path,)
        )
        assert before["source_file"]

        kb.update(path, content="extra OCR text", append=True)

        after = app.storage.fetchone(
            "SELECT source_file FROM knowledge_notes WHERE path=?", (path,)
        )
        assert after["source_file"] == before["source_file"]

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

    def test_append_related_link_returns_true_when_written(self, kb):
        p1 = kb.write("Note A", "aaa", type="note")
        p2 = kb.write("Note B", "bbb", type="note")
        assert kb.append_related_link(p1, p2) is True
        result = kb.read(p1)
        assert f"[[{p2}]]" in result["content"]

    def test_append_related_link_returns_false_when_source_missing(self, kb):
        """Fail-closed contract for the organizer's auto-accept path: a
        stale suggestion whose source note was deleted must be reported as
        a no-op, not silently treated as success."""
        assert kb.append_related_link("notes/does-not-exist", "notes/other") is False

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


class _StubEmbeddingProvider:
    """Deterministic, normalized embeddings keyed by substring, for testing
    the distance→similarity math without a real model."""
    name = "stub"
    dimension = 3

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    def _vec(self, text: str) -> list[float]:
        for key, v in self._vectors.items():
            if key in text:
                return v
        return [0.0, 0.0, 1.0]

    def compute(self, text: str) -> list[float]:
        return self._vec(text)

    def compute_batch(self, texts):
        return [self._vec(t) for t in texts]


def _kb_with_stub_embeddings(app, vectors):
    """Build a KnowledgeBase whose embedding provider is the stub, with the
    vec table sized to the stub dimension."""
    from mycelos.knowledge.service import KnowledgeBase
    kb = KnowledgeBase(app)
    kb._embedding_provider = _StubEmbeddingProvider(vectors)
    # Reset the vec table to the stub's dimension.
    app.storage.execute("DELETE FROM knowledge_config WHERE key='embedding_dimension'")
    try:
        app.storage._get_connection().execute("DROP TABLE IF EXISTS knowledge_vec")
    except Exception:
        pass
    kb._ensure_vec_table()
    return kb


class TestVectorSimilarityCalibration:
    """Regression for P0-3: distance was treated as cosine while sqlite-vec
    defaults to L2, so near-identical notes scored far apart and duplicate
    detection was effectively dead."""

    def test_orthogonal_notes_are_not_similar(self, app):
        # Two orthogonal unit vectors → cosine similarity 0.
        kb = _kb_with_stub_embeddings(app, {
            "alpha topic": [1.0, 0.0, 0.0],
            "beta topic": [0.0, 1.0, 0.0],
        })
        kb.write("Alpha", "alpha topic body", type="note")
        kb.write("Beta", "beta topic body", type="note")
        results = kb.find_relevant("alpha topic", threshold=0.7)
        # Beta must not surface as relevant to Alpha at a 0.7 cosine threshold.
        beta = [r for r in results if "beta" in r.get("path", "")]
        assert beta == []

    def test_near_identical_notes_are_detected_as_duplicates(self, app):
        # Two nearly-parallel unit vectors → cosine ≈ 0.999.
        kb = _kb_with_stub_embeddings(app, {
            "shopping list one": [1.0, 0.0, 0.0],
            "shopping list two": [0.9994, 0.0349, 0.0],  # ~2° apart
        })
        p1 = kb.write("List one", "shopping list one body", type="note")
        kb.write("List two", "shopping list two body", type="note")
        dups = kb.find_duplicates(p1, threshold=0.92)
        assert any("list-two" in d.get("path", "") for d in dups)

    def test_find_duplicates_returns_empty_without_embeddings(self, app):
        """The worst-case data-loss path: with no embedding provider,
        find_duplicates must NOT fall back to keyword search (which ignores
        the threshold) — it must return []. Otherwise the organizer would
        propose merges from mere keyword overlap."""
        from mycelos.knowledge.service import KnowledgeBase
        from mycelos.knowledge.embeddings import FallbackProvider
        kb = KnowledgeBase(app)
        kb._embedding_provider = FallbackProvider()
        p1 = kb.write("Invoice March", "invoice total 100 eur", type="note")
        kb.write("Invoice April", "invoice total 200 eur", type="note")
        # Shared keyword "invoice" would match under FTS, but these are NOT
        # duplicates.
        assert kb.find_duplicates(p1, threshold=0.92) == []


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


# ─── FTS tokenizer: diacritics-insensitive search + self-detecting rebuild ─────


def test_search_is_diacritics_insensitive(kb) -> None:
    kb.write(title="Ernährung", content="Gemüse und Obst täglich", topic="notes")
    hits = kb.search("ernahrung")
    assert any(h["title"] == "Ernährung" for h in hits)
    hits = kb.search("gemuse")
    assert any(h["title"] == "Ernährung" for h in hits)


def test_outdated_fts_index_is_rebuilt(app, kb) -> None:
    # Simulate a pre-existing index built with the old tokenizer.
    kb.write(title="Ernährung", content="Gemüse", topic="notes")
    app.storage.execute("DROP TABLE knowledge_fts")
    app.storage.executescript(
        "CREATE VIRTUAL TABLE knowledge_fts USING fts5(title, content, tags);"
    )
    # Old-tokenizer index is empty and diacritics-sensitive. Re-running the
    # service bootstrap must detect the DDL mismatch and rebuild from files.
    from mycelos.knowledge.service import KnowledgeBase
    kb2 = KnowledgeBase(app)
    hits = kb2.search("gemuse")
    assert any(h["title"] == "Ernährung" for h in hits)


def test_rebuilt_fts_tags_match_normal_index_tokenization(app, kb) -> None:
    """Tags re-indexed by ensure_fts's rebuild path must be tokenized the
    same way index_note tokenizes them (space-separated, not raw JSON) —
    otherwise tag search recall depends on which path last touched a note."""
    kb.write(
        title="Kaffeeprojekt",
        content="Notizen",
        tags=["projekt", "kaffee"],
        topic="notes",
    )
    path = kb.list_notes(type="note")[0]["path"]
    note_id = app.storage.fetchone(
        "SELECT id FROM knowledge_notes WHERE path = ?", (path,)
    )["id"]

    # Capture how the normal index_note path tokenizes these tags, then
    # force a rebuild (old-tokenizer table, like test_outdated_fts_index_is_rebuilt).
    normal_row = app.storage.fetchone(
        "SELECT tags FROM knowledge_fts WHERE rowid = ?", (note_id,)
    )
    app.storage.execute("DROP TABLE knowledge_fts")
    app.storage.executescript(
        "CREATE VIRTUAL TABLE knowledge_fts USING fts5(title, content, tags);"
    )
    from mycelos.knowledge.service import KnowledgeBase
    kb2 = KnowledgeBase(app)

    hits = kb2.search("projekt")
    assert any(h["title"] == "Kaffeeprojekt" for h in hits)

    rebuilt_row = app.storage.fetchone(
        "SELECT tags FROM knowledge_fts WHERE rowid = ?", (note_id,)
    )
    assert rebuilt_row["tags"] == normal_row["tags"] == "projekt kaffee"


class TestHybridSearch:
    """Task 3: search() fuses the FTS and vector arms via RRF."""

    def test_search_fuses_fts_and_vector_results(self, app) -> None:
        # Both notes' embed text ("title content") contains "Kaffee" as a
        # substring, and so does the query — the stub maps all three to the
        # same vector, so the vector arm returns both notes at similarity 1.0.
        kb = _kb_with_stub_embeddings(app, {"Kaffee": [1.0, 0.0, 0.0]})
        kb.write(title="Kaffeemaschine entkalken", content="Essig und Wasser", topic="notes")
        kb.write(title="Espresso Bohnen", content="Kaffee Röstung dunkel", topic="notes")

        hits = kb.search("Kaffee")
        paths = [h["path"] for h in hits]
        # FTS hit (title/content contains Kaffee) present:
        assert any("espresso-bohnen" in p for p in paths)
        # fused results carry the rrf score:
        assert all("rrf_score" in h for h in hits)

    def test_search_without_provider_behaves_like_today(self, kb) -> None:
        # dimension == 0 → FTS-only, no rrf_score requirement, LIKE fallback intact
        kb.write(title="Solitaire", content="Kartenspiel", topic="notes")
        hits = kb.search("Solitaire")
        assert hits and hits[0]["title"] == "Solitaire"
        # typo → LIKE fallback path still works
        hits = kb.search("Solitair")
        assert hits and hits[0]["title"] == "Solitaire"

    def test_search_type_filter_applies_to_vector_arm(self, app) -> None:
        # a vector-armed search with type="task" must not return notes of other
        # types even if they are semantically close (filter before fusion)
        kb = _kb_with_stub_embeddings(app, {"Kaffee": [1.0, 0.0, 0.0]})
        kb.write(title="Kaffee kochen", content="Kaffee Task-Erinnerung", type="task", topic="tasks")
        kb.write(title="Kaffee Notiz", content="Kaffee Gedanke", type="note", topic="notes")

        hits = kb.search("Kaffee", type="task")
        assert hits
        assert all(h["type"] == "task" for h in hits)


class TestHybridFindRelevant:
    """Task 4: find_relevant() fuses the FTS and vector arms via RRF."""

    def test_find_relevant_includes_keyword_only_matches(self, app) -> None:
        # a note matching only by keyword must appear in fused results
        # (today it is invisible whenever the vector arm returns anything)
        kb = _kb_with_stub_embeddings(app, {
            "Kaffee": [1.0, 0.0, 0.0],
            "Wachmacher": [1.0, 0.0, 0.0],
        })
        # Semantic-only: embed text has no literal "kaffee" token, so FTS
        # misses it, but it shares the stub vector with the query "Kaffee".
        kb.write(title="Wachmacher Getraenk", content="das uebliche Morgenritual", topic="notes")
        # Keyword-only: FTS matches "Kaffee" (case-insensitive tokenizer),
        # but the uppercase spelling dodges the stub's case-sensitive
        # substring lookup, so it falls back to the unrelated default
        # vector [0, 0, 1] — orthogonal to the query, excluded by threshold.
        keyword_only_path = kb.write(
            title="Espresso Bohnen", content="KAFFEE Roestung dunkel", topic="notes"
        )

        results = kb.find_relevant("Kaffee")
        paths = [r["path"] for r in results]
        assert keyword_only_path in paths

    def test_find_relevant_without_provider_is_fts_only(self, kb) -> None:
        kb.write(title="Backup Strategie", content="Restic und Hetzner", topic="notes")
        results = kb.find_relevant("Backup")
        assert results and results[0]["title"] == "Backup Strategie"

    def test_find_duplicates_never_uses_fts_or_fusion(self, kb, monkeypatch) -> None:
        # Pin the June P0-3 decision: duplicate detection is vector-only.
        from mycelos.knowledge import ranking

        def _boom(*args, **kwargs):
            raise AssertionError("find_duplicates must not use FTS/fusion")

        monkeypatch.setattr(ranking, "rrf_fuse", _boom)
        monkeypatch.setattr(kb._indexer, "search_fts", _boom)
        monkeypatch.setattr(kb._indexer, "search_like", _boom)
        path = kb.write(title="Doppelt", content="inhalt", topic="notes")
        assert kb.find_duplicates(path) == []  # no provider → fail closed, no FTS
