"""Local checks must preserve evidence without starting model work."""
import hashlib
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from mycelos.knowledge.local_sources import LocalSourceService
from mycelos.knowledge.source_lock import source_lock
from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def env(tmp_path):
    from mycelos.knowledge.source_checks import SourceCheckService
    data = tmp_path / 'data'
    data.mkdir()
    db = SQLiteStorage(data / 'test.db')
    db.initialize()
    # This app cannot provide a broker, model or knowledge base.
    app = SimpleNamespace(storage=db, data_dir=data)
    root = tmp_path / 'source'
    root.mkdir()
    local = LocalSourceService(app)
    source = local.add('Test', 'folder', str(root), mode='direct')
    return app, root, source['id'], SourceCheckService(app)


def imported(app, sid, external_id, text, available=1, recipe=None):
    from uuid import uuid4
    from mycelos.knowledge.local_sources import source_recipe
    recipe = recipe or source_recipe(LocalSourceService(app).get(sid))
    vid = uuid4().hex
    app.storage.execute('INSERT INTO local_source_versions(id,user_id,fingerprint,recipe,content,summary) VALUES(?,?,?,?,?,?)',
                        (vid, 'default', hashlib.sha256(text.encode()).hexdigest(), recipe, text, text))
    app.storage.execute('INSERT INTO local_source_items(id,source_id,external_id,version_id,files,seen_run,available) VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_id,external_id) DO UPDATE SET version_id=excluded.version_id,available=excluded.available',
                        (uuid4().hex, sid, external_id, vid, '[]', 'test', available))


def test_new_changed_removed_persist_without_model_access(env):
    app, root, sid, svc = env
    path = root / 'a.md'
    path.write_text('First')
    assert svc.status(sid)['interval_minutes'] == 0
    assert svc.check(sid)['pending_count'] == 1
    assert svc.pending(sid)[0]['change'] == 'new'
    assert svc.check(sid)['pending_count'] == 1
    path.write_text('Second')
    svc.check(sid)
    assert svc.pending(sid)[0]['change'] == 'changed'
    stored = app.storage.fetchone('SELECT content FROM local_source_check_items WHERE source_id=?', (sid,))
    assert stored['content'] == 'Second'
    path.unlink()
    assert svc.check(sid)['removed_count'] == 1
    assert svc.check(sid)['removed_count'] == 1
    assert 'content' not in svc.pending(sid)[0]
    assert app.storage.fetchone('SELECT count(*) n FROM knowledge_notes')['n'] == 0
    assert LocalSourceService(app).get(sid)['status'] == 'ready'


def test_imported_baseline_and_explicit_import_clear_pending(env):
    app, root, sid, svc = env
    (root / 'a.md').write_text('First')
    imported(app, sid, 'a.md', 'First')
    assert svc.check(sid)['pending_count'] == 0
    (root / 'a.md').write_text('Second')
    svc.check(sid)
    assert svc.pending(sid)[0]['change'] == 'changed'
    imported(app, sid, 'a.md', 'Second')
    assert svc.status(sid)['pending_count'] == 0
    (root / 'a.md').unlink()
    assert svc.check(sid)['removed_count'] == 1
    app.storage.execute('UPDATE local_source_items SET available=0 WHERE source_id=?', (sid,))
    assert svc.status(sid)['pending_count'] == 0


def test_read_failure_keeps_complete_inventory_and_resets_failures(env):
    app, root, sid, svc = env
    (root / 'a.md').write_text('First')
    svc.check(sid)
    old = svc.pending(sid)
    (root / 'a.md').write_bytes(b'\xff')
    assert svc.check(sid)['failure_count'] == 1
    assert svc.check(sid)['failure_count'] == 2
    assert svc.pending(sid) == old
    (root / 'a.md').write_text('First')
    state = svc.check(sid)
    assert state['failure_count'] == 0
    assert state['error'] is None
    assert state['checked_units'] == 1


def test_schedule_claim_lock_and_owner_boundaries(env):
    from mycelos.knowledge.source_checks import SourceCheckService
    app, root, sid, svc = env
    for invalid in (-1, 1, 30, True, '15', 15.0):
        with pytest.raises(ValueError):
            svc.configure(sid, invalid)
    assert svc.due() == []
    state = svc.configure(sid, 15)
    assert datetime.fromisoformat(state['next_check'].replace('Z', '+00:00')).utcoffset().total_seconds() == 0
    assert not svc.claim(sid, due_only=True)
    app.storage.execute("UPDATE local_source_checks SET next_check='2000-01-01T00:00:00Z'")
    assert svc.due() == [{'source_id': sid, 'user_id': 'default'}]
    with source_lock(app.data_dir, sid):
        assert not svc.claim(sid)
        with pytest.raises(ValueError):
            svc.check(sid)
    assert svc.claim(sid)
    assert not svc.claim(sid)
    assert svc.status(sid)['status'] == 'queued'
    assert svc.fail_enqueue(sid)['status'] == 'error'
    assert svc.claim(sid)
    svc.check(sid)
    assert svc.status(sid)['failure_count'] == 0
    other = SourceCheckService(app, 'other')
    for method in (other.status, other.pending, other.claim, other.check, other.fail_enqueue):
        with pytest.raises(ValueError):
            method(sid)
    assert other.due() == []
    LocalSourceService(app).pause(sid)
    assert not svc.claim(sid)
    assert svc.due() == []


def test_recovery_does_not_take_active_lock_or_queued_work(env):
    app, root, sid, svc = env
    svc.claim(sid)
    svc.recover()
    assert svc.status(sid)['status'] == 'queued'
    app.storage.execute("UPDATE local_source_checks SET status='running'")
    with source_lock(app.data_dir, sid):
        svc.recover()
        assert svc.status(sid)['status'] == 'running'
    svc.recover()
    assert svc.status(sid)['status'] == 'error'
    assert svc.status(sid)['failure_count'] == 1


@pytest.mark.parametrize('kind', ['codex', 'claude'])
def test_continued_conversation_has_same_identity(env, kind):
    app, root, _, svc = env
    if kind == 'claude':
        first = {'type': 'user', 'sessionId': 'session-1', 'uuid': 'u1', 'message': {'content': 'First'}}
        second = {'type': 'user', 'sessionId': 'session-1', 'uuid': 'u2', 'message': {'content': 'Second'}}
    else:
        first = {'type': 'session_meta', 'payload': {'id': 'session-1'}}
        second = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': 'Second'}}
    path = root / 'session.jsonl'
    path.write_text(json.dumps(first) + '\n' + json.dumps(second) + '\n')
    sid = LocalSourceService(app).add('Conversation', kind, str(root), mode='direct')['id']
    svc.check(sid)
    identity = svc.pending(sid)[0]['external_id']
    third = {'type': 'assistant', 'sessionId': 'session-1', 'uuid': 'u3', 'message': {'content': 'Third'}} if kind == 'claude' else {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': 'Third'}}
    path.write_text(path.read_text() + json.dumps(third) + '\n')
    svc.check(sid)
    assert len(svc.pending(sid)) == 1
    assert svc.pending(sid)[0]['external_id'] == identity
    assert svc.pending(sid)[0]['change'] == 'changed'


def test_removed_single_file_is_pending(env):
    app, root, _, svc = env
    path = root / 'a.md'
    path.write_text('First')
    sid = LocalSourceService(app).add('File', 'file', str(path), mode='direct')['id']
    svc.check(sid)
    path.unlink()
    assert svc.check(sid)['removed_count'] == 1


def test_expired_queue_lease_recovers_only_without_source_lock(env):
    app, root, sid, svc = env
    svc.claim(sid)
    app.storage.execute("UPDATE local_source_checks SET lease_until='2000-01-01T00:00:00Z'")
    with source_lock(app.data_dir, sid):
        svc.recover()
        assert svc.status(sid)['status'] == 'queued'
    svc.recover()
    assert svc.status(sid)['status'] == 'error'
    assert svc.claim(sid)


def test_pending_list_is_bounded_but_counts_all_units(env):
    app, root, sid, svc = env
    for i in range(205):
        (root / f'{i}.md').write_text(f'Note {i}')
    state = svc.check(sid)
    assert state['pending_count'] == 205
    assert state['checked_units'] == 205
    assert len(svc.pending(sid)) == 200


@pytest.mark.parametrize('kind,marker', [('git', '.git'), ('mercurial', '.hg')])
def test_repository_check_uses_saved_file_selection(env, kind, marker):
    app, root, _, svc = env
    (root / marker).mkdir()
    (root / 'README.md').write_text('First')
    (root / 'main.py').write_text('print(1)')
    local = LocalSourceService(app)
    sid = local.add('Repo', kind, str(root), mode='direct')['id']
    state = svc.check(sid)
    assert state['checked_units'] >= 1
    before = svc.pending(sid)
    (root / 'README.md').write_text('Second')
    svc.check(sid)
    after = svc.pending(sid)
    assert any(row['change'] == 'changed' for row in after)
    assert before != after


def test_missing_folder_does_not_publish_removals(env):
    app, root, sid, svc = env
    (root / 'a.md').write_text('First')
    svc.check(sid)
    root.rename(root.with_name('moved'))
    assert svc.check(sid)['status'] == 'error'
    assert svc.status(sid)['removed_count'] == 0
    assert svc.pending(sid)[0]['change'] == 'new'


def test_partial_read_does_not_replace_complete_scan(env):
    app, root, sid, svc = env
    (root / 'a.md').write_text('First')
    svc.check(sid)
    before = svc.pending(sid)
    (root / 'a.md').write_text('Second')
    (root / 'z.md').write_bytes(b'\xff')
    assert svc.check(sid)['status'] == 'error'
    assert svc.pending(sid) == before


def test_saved_rules_exclude_unselected_files(env):
    app, root, sid, svc = env
    (root / 'a.md').write_text('First')
    (root / 'ignore.md').write_text('Ignored')
    LocalSourceService(app).update(sid, 'Test', '', 'direct', workflow={'exclude': ['ignore.md']})
    assert svc.check(sid)['checked_units'] == 1
    assert [r['external_id'] for r in svc.pending(sid)] == ['a.md']


def test_existing_database_adds_check_tables_without_enabling_schedule(env):
    app, root, sid, svc = env
    app.storage.execute('DROP TABLE local_source_check_items')
    app.storage.execute('DROP TABLE local_source_checks')
    app.storage.close()
    app.storage = SQLiteStorage(app.data_dir / 'test.db')
    from mycelos.knowledge.source_checks import SourceCheckService
    restored = SourceCheckService(app)
    assert restored.status(sid)['interval_minutes'] == 0
    assert restored.due() == []


def test_duplicate_transcript_identity_keeps_last_complete_scan(env):
    app, root, _, svc = env
    first = {'type': 'user', 'sessionId': 'same', 'uuid': 'u1', 'message': {'content': 'First'}}
    path = root / 'first.jsonl'
    path.write_text(json.dumps(first) + '\n')
    sid = LocalSourceService(app).add('Conversation', 'claude', str(root), mode='direct')['id']
    assert svc.check(sid)['checked_units'] == 1
    before = svc.pending(sid)
    (root / 'second.jsonl').write_text(path.read_text())
    assert svc.check(sid)['status'] == 'error'
    assert svc.pending(sid) == before


def test_publish_failure_rolls_back_inventory(env, monkeypatch):
    app, root, sid, svc = env
    (root / 'a.md').write_text('First')
    svc.check(sid)
    before = svc.pending(sid)
    (root / 'a.md').write_text('Second')
    execute = app.storage.execute

    def fail_snapshot_write(sql, params=()):
        if sql.startswith('INSERT INTO local_source_check_items'):
            raise RuntimeError('Simulate full disk during publication')
        return execute(sql, params)

    monkeypatch.setattr(app.storage, 'execute', fail_snapshot_write)
    assert svc.check(sid)['status'] == 'error'
    assert svc.pending(sid) == before


def test_old_queue_request_cannot_read_after_recovery_and_new_claim(env):
    app, root, sid, svc = env
    (root / 'a.md').write_text('First')
    assert svc.claim(sid)
    old_request = svc.status(sid)['request_id']
    app.storage.execute("UPDATE local_source_checks SET lease_until='2000-01-01T00:00:00Z'")
    svc.recover()
    assert svc.status(sid)['request_id'] is None
    assert svc.claim(sid)
    new_request = svc.status(sid)['request_id']
    assert new_request != old_request
    assert svc.check(sid, request_id=old_request)['status'] == 'queued'
    assert svc.status(sid)['checked_units'] == 0
    assert svc.pending(sid) == []
    state = svc.check(sid, request_id=new_request)
    assert state['checked_units'] == 1
    assert state['request_id'] is None
    (root / 'a.md').write_text('Second')
    svc.check(sid, request_id=new_request)
    assert svc.pending(sid)[0]['change'] == 'new'
