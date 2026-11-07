import json

import pytest

from mycelos.app import App
from mycelos.knowledge.local_sources import LocalSourceService


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    app = App(data)
    app.storage.initialize()
    # Tests deliberately use deterministic FTS rather than external providers.
    app.memory.set("default", "system", "knowledge_config", json.dumps({"embedding_provider": "none"}))
    root = tmp_path / "source"
    root.mkdir()
    return app, root


def test_import_persists_and_links_identical_content_from_two_sources(env, tmp_path):
    app, root = env
    (root / "idea.md").write_text("Docustore uses OCR for scanned invoices.")
    svc = LocalSourceService(app)
    a = svc.add("Documents", "folder", str(root), mode="direct")
    assert LocalSourceService(app).list()[0]["id"] == a["id"]
    assert svc.preview(a["id"])["units"] == 1
    assert app.storage.fetchone("SELECT count(*) AS n FROM knowledge_notes WHERE type='note'")["n"] == 0
    result = svc.ingest(a["id"])
    assert result["created"] == 1
    assert svc.ingest(a["id"])["unchanged"] == 1
    other = tmp_path / "other"
    other.mkdir()
    (other / "renamed.md").write_text("Docustore uses OCR for scanned invoices.")
    b = svc.add("Second source", "folder", str(other), mode="direct")
    assert svc.ingest(b["id"])["linked"] == 1
    assert len(svc.search("OCR")) == 1
    hit = svc.search("OCR")[0]
    assert len(hit["origins"]) == 2
    assert "scanned invoices" in svc.evidence(hit["origins"][0]["item_id"])["content"]


def test_conversation_continuation_updates_same_note_and_retains_versions(env):
    app, root = env
    path = root / "session.jsonl"
    def turn(text, uid):
        return json.dumps({"type": "user", "sessionId": "session-1", "uuid": uid,
                           "message": {"content": text}}) + "\n"
    path.write_text(turn("Use OCR", "u1"))
    summaries = []
    def summarize(unit, source):
        summaries.append(unit.content)
        return "Summary: " + unit.content
    svc = LocalSourceService(app, summarize=summarize)
    source = svc.add("Claude", "claude", str(root), model="test-model")
    first = svc.ingest(source["id"])
    path.write_text(path.read_text() + turn("Use local OCR", "u2"))
    second = svc.ingest(source["id"])
    assert first["created"] == 1
    assert second["updated"] == 1
    assert len(summaries) == 2
    assert app.storage.fetchone("SELECT count(*) AS n FROM knowledge_notes WHERE type='note'")["n"] == 1
    assert app.storage.fetchone("SELECT count(*) AS n FROM local_source_versions")["n"] == 2


def test_missing_source_object_keeps_knowledge(env):
    app, root = env
    path = root / "note.md"
    path.write_text("Physics quantum entanglement")
    svc = LocalSourceService(app)
    source = svc.add("Physics", "folder", str(root), mode="direct")
    svc.ingest(source["id"])
    path.unlink()
    svc.ingest(source["id"])
    hit = svc.search("entanglement")[0]
    assert hit["origins"][0]["available"] == 0


def test_failed_analysis_does_not_mark_content_processed(env):
    app, root = env
    (root / "note.md").write_text("Import failed")
    def fail(unit, source):
        raise RuntimeError("model unavailable")
    svc = LocalSourceService(app, summarize=fail)
    source = svc.add("Documents", "folder", str(root), model="test-model")
    with pytest.raises(RuntimeError):
        svc.ingest(source["id"])
    assert svc.list()[0]["status"] == "error"
    assert svc.list()[0]["error"] == "Import fehlgeschlagen. Quelle und Modell prüfen."
    assert app.storage.fetchone("SELECT count(*) AS n FROM local_source_items")["n"] == 0
    recovered = LocalSourceService(app, summarize=lambda unit, source: "Recovered")
    assert recovered.ingest(source["id"])["created"] == 1


def test_reject_root_and_unknown_mode(env):
    app, root = env
    svc = LocalSourceService(app)
    with pytest.raises(ValueError):
        svc.add("All", "folder", "/", mode="direct")
    with pytest.raises(ValueError):
        svc.add("All", "folder", str(root), mode="unknown")


def test_source_ownership_is_checked(env):
    app, root = env
    source = LocalSourceService(app, user_id="alice").add("A", "folder", str(root), mode="direct")
    bob = LocalSourceService(app, user_id="bob")
    assert bob.list() == []
    with pytest.raises(ValueError):
        bob.preview(source["id"])


def test_new_source_with_old_version_does_not_point_to_updated_summary(env, tmp_path):
    app, root = env
    p = root / "note.md"
    p.write_text("The old decision")
    svc = LocalSourceService(app)
    a = svc.add("A", "folder", str(root), mode="direct")
    svc.ingest(a["id"])
    p.write_text("The new decision")
    svc.ingest(a["id"])
    other = tmp_path / "other"
    other.mkdir()
    (other / "note.md").write_text("The old decision")
    b = svc.add("B", "folder", str(other), mode="direct")
    svc.ingest(b["id"])
    hits = svc.search("old")
    assert len(hits) == 1
    assert app.knowledge_base.read(hits[0]["path"])["content"] == "The old decision"


def test_probe_analyzes_sample_without_creating_knowledge(env):
    app, root = env
    (root / "note.md").write_text("An architecture decision")
    svc = LocalSourceService(app, summarize=lambda unit, source: "A concise decision")
    source = svc.add("Project", "folder", str(root), model="test-model")
    result = svc.probe(source["id"])
    assert result["samples"][0]["summary"] == "A concise decision"
    assert result["samples"][0]["seconds"] >= 0
    assert result["estimated_cost"] is None
    assert app.storage.fetchone("SELECT count(*) AS n FROM knowledge_notes WHERE type='note'")["n"] == 0


def test_huey_uses_existing_queue_and_suppresses_duplicate_enqueues(env):
    from huey import MemoryHuey
    from mycelos.scheduler.local_source_task import register_local_source_task
    app, root = env
    (root / "note.md").write_text("Persistent local knowledge")
    queue = MemoryHuey()
    app.local_source_task = register_local_source_task(queue, app)
    svc = LocalSourceService(app)
    source = svc.add("Project", "folder", str(root), mode="direct")
    assert svc.enqueue(source["id"])["status"] == "queued"
    assert svc.enqueue(source["id"])["status"] == "queued"
    assert queue.pending_count() == 1
    queue.execute(queue.dequeue())
    assert svc.get(source["id"])["status"] == "ready"
    assert svc.search("Persistent")


def test_import_recovers_after_process_disappears(env):
    from mycelos.scheduler.run_recorder import RunRecorder
    app, root = env
    (root / "note.md").write_text("Resumable knowledge")
    svc = LocalSourceService(app)
    source = svc.add("Project", "folder", str(root), mode="direct")
    run = RunRecorder(app.storage).start("source_sync", source["id"])
    app.storage.execute("UPDATE local_sources SET status='running' WHERE id=?", (source["id"],))
    # Durable state remains, but no process owns the source lock anymore.
    assert svc.ingest(source["id"])["created"] == 1
    assert app.storage.fetchone("SELECT status FROM workflow_runs WHERE id=?", (run,))["status"] == "failed"


def test_changed_content_matching_another_item_archives_old_note(env):
    app, root = env
    a = root / "a.md"
    a.write_text("Old alpha")
    (root / "b.md").write_text("New beta")
    svc = LocalSourceService(app)
    source = svc.add("Docs", "folder", str(root), mode="direct")
    svc.ingest(source["id"])
    old_path = svc.search("alpha")[0]["path"]
    a.write_text("New beta")
    svc.ingest(source["id"])
    assert app.knowledge_base.read(old_path)["status"] == "archived"
    assert len(svc.search("beta")[0]["origins"]) == 2


def test_active_process_lock_cannot_be_stolen(env):
    from mycelos.knowledge.source_lock import source_lock
    app, root = env
    svc = LocalSourceService(app)
    source = svc.add("Docs", "folder", str(root), mode="direct")
    with source_lock(app.data_dir, source["id"]):
        with pytest.raises(ValueError, match="bereits"):
            svc.ingest(source["id"])


@pytest.mark.parametrize("manual_archive", [False, True])
def test_previous_content_becomes_active_when_source_returns_to_it(env, manual_archive):
    app, root = env
    a = root / "a.md"
    a.write_text("Old alpha")
    (root / "b.md").write_text("New beta")
    svc = LocalSourceService(app)
    source = svc.add("Docs", "folder", str(root), mode="direct")
    svc.ingest(source["id"])
    old_path = svc.search("alpha")[0]["path"]
    if manual_archive:
        app.knowledge_base.update(old_path, status="archived")
        app.knowledge_base.archive_note(old_path)
    a.write_text("New beta")
    svc.ingest(source["id"])
    a.write_text("Old alpha")
    svc.ingest(source["id"])
    assert app.knowledge_base.read(old_path)["status"] == ("archived" if manual_archive else "active")


def test_analysis_error_is_visible_without_exposing_arbitrary_errors(env):
    from mycelos.knowledge.source_analysis import SourceAnalysisError
    app, root = env
    (root / 'note.md').write_text('Project documentation')
    def fail(unit, source):
        raise SourceAnalysisError('Das Modell hat eine leere Antwort geliefert.')
    svc = LocalSourceService(app, summarize=fail)
    source = svc.add('Documents', 'folder', str(root), model='test-model')
    with pytest.raises(SourceAnalysisError):
        svc.ingest(source['id'])
    assert svc.get(source['id'])['error'] == 'Das Modell hat eine leere Antwort geliefert.'
