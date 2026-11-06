"""Collector boundaries use artificial files only."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest


def collector(tmp_path):
    from mycelos.collector.local import LocalCollector
    return LocalCollector(tmp_path / 'state')


def grant(tmp_path, kind='folder', workflow=None):
    root = tmp_path / 'source'
    root.mkdir()
    client = collector(tmp_path)
    source = client.add_source('Example', kind, root, workflow=workflow, source_id='preserved-id')
    return client, source, root


def test_recursive_scan_retry_checkpoint_and_removal(tmp_path):
    client, source, root = grant(tmp_path)
    (root / 'nested').mkdir()
    path = root / 'nested' / 'note.md'
    path.write_text('First version')
    first = client.scan(source['id'])
    assert first['complete'] is True
    assert first['source_id'] == 'preserved-id'
    assert first['units'][0]['files'] == ['nested/note.md']
    assert first['units'][0]['fingerprint'] == hashlib.sha256(b'First version').hexdigest()
    path.write_text('Second version')
    assert collector(tmp_path).scan(source['id']) == first
    client.acknowledge(first['scan_id'])
    second = client.scan(source['id'])
    assert second['units'][0]['content'] == 'Second version'
    client.acknowledge(second['scan_id'])
    unchanged = client.scan(source['id'])
    assert unchanged['units'] == []
    client.acknowledge(unchanged['scan_id'])
    path.unlink()
    removed = client.scan(source['id'])
    assert removed['removed'] == ['nested/note.md']


def test_pause_blocks_cached_scan_and_original_across_restart(tmp_path):
    client, source, root = grant(tmp_path)
    (root / 'note.md').write_text('Text')
    scan = client.scan(source['id'])
    client.pause(True)
    client = collector(tmp_path)
    with pytest.raises(PermissionError):
        client.scan(source['id'])
    with pytest.raises(PermissionError):
        client.original(source['id'], 'note.md', scan['units'][0]['fingerprint'])
    client.pause(False)
    assert client.scan(source['id']) == scan


def test_original_requires_known_id_current_rules_and_expected_version(tmp_path):
    client, source, root = grant(tmp_path)
    path = root / 'note.md'
    path.write_text('Old')
    scan = client.scan(source['id'])
    unit = scan['units'][0]
    assert client.original(source['id'], 'note.md', unit['fingerprint'])['content'] == 'Old'
    with pytest.raises((PermissionError, ValueError, KeyError)):
        client.original(source['id'], '../outside.md', unit['fingerprint'])
    path.write_text('New')
    mismatch = client.original(source['id'], 'note.md', unit['fingerprint'])
    assert mismatch == {'status': 'version_mismatch', 'expected': unit['fingerprint'],
                        'fingerprint': hashlib.sha256(b'New').hexdigest()}
    assert 'content' not in mismatch
    path.unlink()
    with pytest.raises((FileNotFoundError, ValueError)):
        client.original(source['id'], 'note.md', unit['fingerprint'])


@pytest.mark.parametrize('target', ['file', 'directory', 'ancestor'])
def test_reject_links_without_reading_outside_grant(tmp_path, target):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.md').write_text('DO NOT READ')
    if target == 'ancestor':
        link = tmp_path / 'linked'
        link.symlink_to(outside, target_is_directory=True)
        with pytest.raises((ValueError, OSError)):
            collector(tmp_path).add_source('Bad', 'file', link / 'secret.md')
        return
    client, source, root = grant(tmp_path)
    (root / 'note.md').write_text('Allowed')
    (root / ('leak.md' if target == 'file' else 'leak')).symlink_to(
        outside / 'secret.md' if target == 'file' else outside,
        target_is_directory=target == 'directory')
    result = client.scan(source['id'])
    assert 'DO NOT READ' not in json.dumps(result)
    assert result['complete'] is False
    assert result['removed'] == []


def test_replaced_root_does_not_expand_grant(tmp_path):
    client, source, root = grant(tmp_path)
    root.rename(tmp_path / 'old')
    root.mkdir()
    (root / 'secret.md').write_text('Other owner')
    with pytest.raises((PermissionError, ValueError, OSError)):
        client.scan(source['id'])


def test_file_swap_between_stat_and_open_does_not_read_link(tmp_path, monkeypatch):
    client, source, root = grant(tmp_path)
    path = root / 'note.md'
    path.write_text('Allowed')
    outside = tmp_path / 'outside.md'
    outside.write_text('SECRET')
    real_open = os.open
    def swap(name, flags, *args, **kwargs):
        if name == 'note.md' and kwargs.get('dir_fd') is not None and path.is_file() and not path.is_symlink():
            path.unlink()
            path.symlink_to(outside)
        return real_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', swap)
    result = client.scan(source['id'])
    assert result['complete'] is False
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('kind, marker', [('git', '.git'), ('mercurial', '.hg')])
def test_repository_documents_keep_project_identity(tmp_path, kind, marker):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / marker).mkdir()
    (root / 'README.md').write_text('Project overview')
    (root / 'random.txt').write_text('Exclude this')
    client = collector(tmp_path)
    source = client.add_source('Repo', kind, root)
    unit = client.scan(source['id'])['units'][0]
    assert unit['external_id'] == 'project:.'
    assert unit['title'] == 'repo'
    assert unit['files'] == ['README.md']
    assert 'Exclude this' not in unit['content']


def test_obsidian_properties_links_and_attachment_refs_are_relative(tmp_path):
    client, source, root = grant(tmp_path, 'obsidian')
    (root / 'note.md').write_text('---\ntitle: Example\nlocation: /private/secret/file\n---\n[[target]] ![[image.png]]')
    (root / 'target.md').write_text('Target text')
    (root / 'image.png').write_bytes(b'NOT AN IMAGE')
    (root / 'Templates').mkdir()
    (root / 'Templates' / 'secret.md').write_text('Excluded')
    result = client.scan(source['id'])
    note = next(u for u in result['units'] if u['external_id'] == 'note.md')
    assert note['title'] == 'Example'
    assert note['metadata']['links'][0]['target'] == 'target.md'
    assert note['metadata']['links'][1]['state'] == 'attachment'
    assert '/private/' not in json.dumps(note['metadata'])
    assert len(result['units']) == 2


@pytest.mark.parametrize('kind', ['claude', 'codex'])
def test_sessions_nested_and_archived_only_inside_grant(tmp_path, kind):
    client, source, root = grant(tmp_path, kind)
    sessions = root / 'archived_sessions' / '2026'
    sessions.mkdir(parents=True)
    if kind == 'codex':
        records = [{'type': 'session_meta', 'payload': {'id': 'session-one', 'cwd': '/Users/example/project'}},
                   {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'User text'}]}},
                   {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'tool-1', 'output': 'Tool result'}}]
    else:
        records = [{'type': 'user', 'sessionId': 'session-one', 'cwd': '/Users/example/project', 'uuid': 'one', 'parentUuid': None, 'message': {'content': 'User text'}},
                   {'type': 'assistant', 'sessionId': 'session-one', 'uuid': 'discarded', 'parentUuid': 'one', 'message': {'content': 'Discarded branch'}},
                   {'type': 'assistant', 'sessionId': 'session-one', 'uuid': 'active', 'parentUuid': 'one', 'message': {'content': [{'type': 'tool_result', 'content': 'Tool result'}]}}]
    (sessions / 'session.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records) + '{"partial":')
    scan = client.scan(source['id'])
    unit = scan['units'][0]
    assert unit['external_id'] == 'session-one'
    assert unit['project'] == 'project'
    assert unit['files'] == ['archived_sessions/2026/session.jsonl']
    assert 'User text' in unit['content'] and 'Tool result' in unit['content']
    assert 'Discarded branch' not in unit['content']
    assert str(tmp_path) not in json.dumps(scan)
    assert '/Users/example/' not in json.dumps(scan)


def test_incomplete_scan_does_not_remove_previously_known_files(tmp_path):
    client, source, root = grant(tmp_path, 'codex')
    path = root / 'session.jsonl'
    path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'a'}}) + '\n' + json.dumps({'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'Hi'}}) + '\n')
    first = client.scan(source['id'])
    client.acknowledge(first['scan_id'])
    path.write_text('{malformed}\n')
    broken = client.scan(source['id'])
    assert broken['complete'] is False
    assert broken['removed'] == []
    client.acknowledge(broken['scan_id'])
    path.unlink()
    assert client.scan(source['id'])['removed'] == ['a']


def test_discovery_only_known_paths_and_never_grants(tmp_path):
    home = tmp_path / 'home'
    (home / '.codex' / 'sessions').mkdir(parents=True)
    (home / '.codex' / 'archived_sessions').mkdir()
    (home / '.claude' / 'projects').mkdir(parents=True)
    (home / 'private').mkdir()
    (home / 'private' / 'do-not-discover.md').write_text('Secret')
    client = collector(tmp_path)
    results = client.discover(home)
    assert {r['kind'] for r in results} == {'codex', 'claude'}
    assert len(results) == 3
    assert client.list_sources() == []
    assert 'do-not-discover' not in json.dumps(results)


def test_outbox_expiry_cleanup_and_private_content_free_audit(tmp_path, monkeypatch):
    import mycelos.collector.local as module
    client, source, root = grant(tmp_path)
    (root / 'note.md').write_text('PRIVATE CONTENT SENTINEL')
    monkeypatch.setattr(module, 'OUTBOX_TTL_SECONDS', 1)
    first = client.scan(source['id'])
    real_time = module.time.time
    monkeypatch.setattr(module.time, 'time', lambda: real_time() + 3)
    next_client = collector(tmp_path)
    second = next_client.scan(source['id'])
    assert second['scan_id'] != first['scan_id']
    next_client.acknowledge(second['scan_id'])
    state = tmp_path / 'state'
    assert state.stat().st_mode & 0o077 == 0
    for dbpath in state.glob('*.sqlite3'):
        assert dbpath.stat().st_mode & 0o077 == 0
        with sqlite3.connect(dbpath) as db:
            audit = db.execute('SELECT * FROM audit').fetchall()
            assert audit
            assert 'PRIVATE CONTENT SENTINEL' not in repr(audit)
    assert not any(p.name.startswith('snapshot-') for p in state.iterdir())


def test_file_limit_marks_incomplete_without_removal(tmp_path, monkeypatch):
    import mycelos.collector.local as module
    client, source, root = grant(tmp_path)
    (root / 'large.md').write_text('Too much content')
    monkeypatch.setattr(module, 'MAX_FILE_BYTES', 4)
    result = client.scan(source['id'])
    assert result['complete'] is False
    assert result['units'] == []
    assert result['removed'] == []


def test_wire_workflow_and_external_links_keep_their_meaning(tmp_path):
    client, source, root = grant(tmp_path, 'obsidian')
    (root / 'note.md').write_text('[Reference](https://example.com/reference)')
    result = client.scan(source['id'])
    assert result['workflow']['include'] == ['**/*.md', '**/*.txt', '**/*.pdf']
    assert result['units'][0]['metadata']['links'][0]['reference'] == 'https://example.com/reference'


def test_status_reports_persisted_pause(tmp_path):
    client = collector(tmp_path)
    assert client.status()['paused'] is False
    client.pause(True)
    assert collector(tmp_path).status()['paused'] is True


def test_workflow_excluded_project_files_are_never_opened(tmp_path, monkeypatch):
    client, source, root = grant(tmp_path)
    (root / '.git').mkdir()
    (root / 'README.md').write_text('Allowed')
    (root / 'private.txt').write_text('Do not open')
    real_open = os.open
    def block_private(name, flags, *args, **kwargs):
        if name == 'private.txt' and kwargs.get('dir_fd') is not None:
            raise AssertionError('The adapter opened a file outside the selected project documents')
        return real_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', block_private)
    result = client.scan(source['id'])
    assert result['complete'] is True
    assert result['units'][0]['files'] == ['README.md']


def test_exclude_rule_stops_original_after_local_rule_change(tmp_path):
    client, source, root = grant(tmp_path)
    (root / 'note.md').write_text('Selected before rule change')
    unit = client.scan(source['id'])['units'][0]
    # A local rule update models a grant restriction; no remote path can do this.
    workflow = source['workflow']
    workflow['exclude'] = ['note.md']
    with sqlite3.connect(client.db_path) as db:
        db.execute('UPDATE sources SET workflow=? WHERE id=?', (json.dumps(workflow), source['id']))
    with pytest.raises((FileNotFoundError, PermissionError, ValueError)):
        client.original(source['id'], unit['external_id'], unit['fingerprint'])


def test_outbox_capacity_preserves_first_unacknowledged_scan(tmp_path, monkeypatch):
    import mycelos.collector.local as module
    client, first_source, root = grant(tmp_path)
    (root / 'note.md').write_text('First source')
    first = client.scan(first_source['id'])
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'note.md').write_text('Second source')
    second_source = client.add_source('Other', 'folder', other)
    monkeypatch.setattr(module, 'MAX_OUTBOX_BYTES', 1)
    with pytest.raises(ValueError, match='outbox'):
        client.scan(second_source['id'])
    assert client.scan(first_source['id']) == first


def test_expired_content_does_not_remain_in_database(tmp_path, monkeypatch):
    import mycelos.collector.local as module
    client, source, root = grant(tmp_path)
    (root / 'note.md').write_text('SENTINEL_TO_REMOVE_ON_ACK')
    scan = client.scan(source['id'])
    client.acknowledge(scan['scan_id'])
    assert b'SENTINEL_TO_REMOVE_ON_ACK' not in client.db_path.read_bytes()


def test_document_parse_cannot_block_past_scan_deadline(tmp_path, monkeypatch):
    import time
    import mycelos.collector.local as module
    client, source, root = grant(tmp_path)
    (root / 'note.md').write_text('Small input with a slow reader')
    reader_script = tmp_path / 'slow_reader.py'
    reader_script.write_text('import time\ntime.sleep(30)\n')
    monkeypatch.setattr(module, 'READER_SCRIPT', reader_script, raising=False)
    monkeypatch.setattr(module, 'MAX_SCAN_SECONDS', 0.05)
    start = time.monotonic()
    result = client.scan(source['id'])
    assert time.monotonic() - start < 0.5
    assert result['complete'] is False
    assert result['units'] == []
    assert not list((tmp_path / 'state').glob('snapshot-*'))


def test_directory_swap_to_outside_during_open_cannot_read_new_content(tmp_path, monkeypatch):
    client, source, root = grant(tmp_path)
    directory = root / 'notes'
    directory.mkdir()
    (directory / 'note.md').write_text('Allowed')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.md').write_text('OUTSIDE CONTENT')
    real_open = os.open
    changed = False
    def swap(name, flags, *args, **kwargs):
        nonlocal changed
        if name == 'notes' and kwargs.get('dir_fd') is not None and not changed:
            changed = True
            directory.rename(root / 'moved')
            directory.symlink_to(outside, target_is_directory=True)
        return real_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', swap)
    result = client.scan(source['id'])
    assert result['complete'] is False
    assert 'OUTSIDE CONTENT' not in json.dumps(result)


def test_file_source_and_idempotent_acknowledgement(tmp_path):
    path = tmp_path / 'one.txt'
    path.write_text('One file')
    client = collector(tmp_path)
    source = client.add_source('File', 'file', path)
    result = client.scan(source['id'])
    assert result['complete'] is True
    assert result['units'][0]['files'] == ['one.txt']
    client.acknowledge(result['scan_id'])
    client.acknowledge(result['scan_id'])
    client.acknowledge('unknown-job')
    assert client.scan(source['id'])['units'] == []


def test_session_grant_does_not_include_sibling_archive(tmp_path):
    client, source, root = grant(tmp_path, 'codex')
    archive = tmp_path / 'archived_sessions'
    archive.mkdir()
    (archive / 'session.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'a'}}) + '\n' + json.dumps({'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'Not granted'}}) + '\n')
    assert client.scan(source['id'])['units'] == []


def test_hard_link_cannot_introduce_an_outside_file(tmp_path):
    client, source, root = grant(tmp_path)
    outside = tmp_path / 'outside.md'
    outside.write_text('OUTSIDE HARD LINK')
    os.link(outside, root / 'note.md')
    result = client.scan(source['id'])
    assert result['complete'] is False
    assert 'OUTSIDE HARD LINK' not in json.dumps(result)


def test_denied_operation_creates_no_content_audit(tmp_path):
    client = collector(tmp_path)
    client.pause(True)
    with pytest.raises(PermissionError):
        client.scan('unknown-private-source')
    with sqlite3.connect(client.db_path) as db:
        rows = db.execute("SELECT operation,status FROM audit WHERE status='denied'").fetchall()
    assert rows == [('scan', 'denied')]


@pytest.mark.parametrize('depth', [1, 2])
def test_directory_moved_after_open_never_reaches_reader(tmp_path, monkeypatch, depth):
    import mycelos.collector.local as module
    client, source, root = grant(tmp_path)
    (root / 'independent.md').write_text('Stable independent file')
    child = root / 'notes' if depth == 1 else root / 'group' / 'notes'
    child.mkdir(parents=True)
    relative = child.relative_to(root)
    (child / 'allowed.md').write_text('Known before the move')
    child_inode = child.stat().st_ino
    real_scandir = os.scandir
    moved = False
    def move_after_open(path):
        nonlocal moved
        if isinstance(path, int) and os.fstat(path).st_ino == child_inode and not moved:
            moved = True
            child.rename(tmp_path / 'outside')
            (tmp_path / 'outside' / 'secret.md').write_text('ADDED AFTER MOVE')
        return real_scandir(path)
    monkeypatch.setattr(os, 'scandir', move_after_open)
    real_run = module.subprocess.run
    def reader_boundary(*args, **kwargs):
        request = json.loads(kwargs['input'])
        assert not (Path(request['root']) / relative).exists(), 'Rejected subtree reached the reader'
        return real_run(*args, **kwargs)
    monkeypatch.setattr(module.subprocess, 'run', reader_boundary)
    result = client.scan(source['id'])
    assert result['complete'] is False
    assert 'ADDED AFTER MOVE' not in json.dumps(result)
    assert result['removed'] == []
    if depth == 2:
        assert result['units'][0]['content'] == 'Stable independent file'


@pytest.mark.parametrize('kind', ['folder', 'git', 'mercurial'])
def test_incomplete_project_never_replaces_complete_checkpoint(tmp_path, monkeypatch, kind):
    import mycelos.collector.local as module
    root = tmp_path / 'source'
    root.mkdir()
    project = root / 'repo' if kind == 'folder' else root
    project.mkdir(exist_ok=True)
    (project / ('.hg' if kind == 'mercurial' else '.git')).mkdir()
    (project / 'README.md').write_text('Project overview')
    (project / 'docs').mkdir()
    spec = project / 'docs' / 'spec.md'
    spec.write_text('Complete project specification')
    if kind == 'folder':
        (root / 'independent.md').write_text('Independent old text')
    client = collector(tmp_path)
    source = client.add_source('Project', kind, root)
    first = client.scan(source['id'])
    project_id = 'project:repo' if kind == 'folder' else 'project:.'
    assert 'Complete project specification' in next(u for u in first['units'] if u['external_id'] == project_id)['content']
    client.acknowledge(first['scan_id'])
    monkeypatch.setattr(module, 'MAX_FILE_BYTES', 64)
    spec.write_text('x' * 100)
    if kind == 'folder':
        (root / 'independent.md').write_text('Independent new text')
    broken = client.scan(source['id'])
    assert broken['complete'] is False
    assert all(u['external_id'] != project_id for u in broken['units'])
    if kind == 'folder':
        assert broken['units'][0]['content'] == 'Independent new text'
    client.acknowledge(broken['scan_id'])
    spec.write_text('Complete project specification')
    restored = client.scan(source['id'])
    assert restored['complete'] is True
    assert restored['units'] == []


def test_session_path_change_emits_same_content_with_new_provenance(tmp_path):
    client, source, root = grant(tmp_path, 'codex')
    session = root / 'session.jsonl'
    session.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'stable-session', 'cwd': '/Users/test/project'}}) + '\n' + json.dumps({'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'Same content'}}) + '\n')
    first = client.scan(source['id'])
    previous_unit = first['units'][0]
    client.acknowledge(first['scan_id'])
    (root / 'archive').mkdir()
    session.rename(root / 'archive' / 'session.jsonl')
    client = collector(tmp_path)
    moved = client.scan(source['id'])
    assert len(moved['units']) == 1
    updated = moved['units'][0]
    assert updated['fingerprint'] == previous_unit['fingerprint']
    assert updated['files'] == ['archive/session.jsonl']
    assert moved['removed'] == []
    client.acknowledge(moved['scan_id'])
    assert collector(tmp_path).scan(source['id'])['units'] == []
    assert client.original(source['id'], 'stable-session', updated['fingerprint'])['files'] == ['archive/session.jsonl']


def test_unchanged_obsidian_text_emits_changed_link_details(tmp_path):
    client, source, root = grant(tmp_path, 'obsidian')
    (root / 'note.md').write_text('---\nrelative: notes/example.md\nurl: https://example.com/path\nprivate: /Users/test/private.txt\n---\n[[target]]')
    first = client.scan(source['id'])
    note = first['units'][0]
    assert note['metadata']['links'][0]['state'] == 'missing'
    client.acknowledge(first['scan_id'])
    (root / 'target.md').write_text('The newly available target')
    changed = client.scan(source['id'])
    updated = next((u for u in changed['units'] if u['external_id'] == 'note.md'), None)
    assert updated is not None
    assert updated['fingerprint'] == note['fingerprint']
    assert updated['metadata']['links'][0]['state'] == 'resolved'
    assert updated['metadata']['links'][0]['target'] == 'target.md'
    assert updated['metadata']['properties'] == {'relative': 'notes/example.md', 'url': 'https://example.com/path', 'private': 'private.txt'}
    original = client.original(source['id'], 'note.md', note['fingerprint'])
    assert original['metadata'] == updated['metadata']
    client.acknowledge(changed['scan_id'])
    assert client.scan(source['id'])['units'] == []


def test_session_project_change_emits_updated_title_and_project(tmp_path):
    client, source, root = grant(tmp_path, 'codex')
    session = root / 'session.jsonl'
    content = json.dumps({'type': 'session_meta', 'payload': {'id': 'stable-session', 'cwd': '/Users/test/project-one'}}) + '\n' + json.dumps({'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'Same content'}}) + '\n'
    session.write_text(content)
    first = client.scan(source['id'])
    client.acknowledge(first['scan_id'])
    session.write_text(content.replace('project-one', 'project-two'))
    changed = client.scan(source['id'])
    assert len(changed['units']) == 1
    assert changed['units'][0]['project'] == 'project-two'
    assert changed['units'][0]['title'].startswith('project-two')
    assert changed['units'][0]['fingerprint'] == first['units'][0]['fingerprint']


def test_old_checkpoint_schema_keeps_content_identity_and_adds_source_details(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    with sqlite3.connect(state / 'collector.sqlite3') as db:
        db.execute('CREATE TABLE checkpoints (source_id TEXT NOT NULL, external_id TEXT NOT NULL, fingerprint TEXT NOT NULL, files TEXT NOT NULL, PRIMARY KEY(source_id,external_id))')
        db.execute('INSERT INTO checkpoints VALUES(?,?,?,?)', ('preserved-id', 'note.md', hashlib.sha256(b'Old content').hexdigest(), '["note.md"]'))
    client, source, root = grant(tmp_path)
    (root / 'note.md').write_text('Old content')
    migrated = client.scan(source['id'])
    assert len(migrated['units']) == 1
    assert migrated['units'][0]['fingerprint'] == hashlib.sha256(b'Old content').hexdigest()
    client.acknowledge(migrated['scan_id'])
    assert collector(tmp_path).scan(source['id'])['units'] == []


def test_rule_update_retains_grant_and_reports_filtered_items_removed(tmp_path):
    client, source, root = grant(tmp_path)
    (root / 'keep.md').write_text('Keep this document')
    (root / 'exclude.md').write_text('Remove this from the source')
    first = client.scan(source['id'])
    client.acknowledge(first['scan_id'])
    stale = client.scan(source['id'])
    updated = client.update_rules(source['id'], {'exclude': ['exclude.md']})
    assert updated['id'] == source['id']
    assert updated['root'] == str(root)
    assert updated['rule_version'] == 2
    assert updated['workflow']['version'] == 1
    assert collector(tmp_path).list_sources()[0] == updated
    revised = client.scan(source['id'])
    assert revised['scan_id'] != stale['scan_id']
    assert revised['rule_version'] == 2
    assert revised['removed'] == ['exclude.md']
    assert revised['units'] == []
    client.acknowledge(stale['scan_id'])
    assert client.scan(source['id']) == revised
    with pytest.raises((PermissionError, FileNotFoundError, ValueError)):
        client.original(source['id'], 'exclude.md', next(u for u in first['units'] if u['external_id'] == 'exclude.md')['fingerprint'])
    with sqlite3.connect(client.db_path) as db:
        assert db.execute("SELECT operation,status FROM audit WHERE operation='update_rules'").fetchall() == [('update_rules', 'ok')]


@pytest.mark.parametrize('workflow', [
    {'root': '/private/elsewhere'}, {'include': ['../outside.md']},
    {'version': 2}, {'steps': []}, {'kind': 'file'}, {'include': ['x'] * 101},
])
def test_invalid_rule_update_preserves_grant_and_pending_scan(tmp_path, workflow):
    client, source, root = grant(tmp_path)
    (root / 'note.md').write_text('Existing content')
    scan = client.scan(source['id'])
    with pytest.raises(ValueError):
        client.update_rules(source['id'], workflow)
    assert client.list_sources()[0] == source
    assert client.scan(source['id']) == scan


def test_rule_revision_increases_only_for_changed_rules_and_survives_restart(tmp_path):
    client, source, root = grant(tmp_path)
    first = client.scan(source['id'])
    assert first['rule_version'] == 1
    assert client.update_rules(source['id'], source['workflow'])['rule_version'] == 1
    assert client.scan(source['id']) == first
    assert client.update_rules(source['id'], {'include': ['**/*.md']})['rule_version'] == 2
    client = collector(tmp_path)
    assert client.update_rules(source['id'], {'include': ['**/*.txt']})['rule_version'] == 3
    assert client.scan(source['id'])['rule_version'] == 3


def test_rule_update_cannot_replace_granted_root_identity(tmp_path):
    client, source, root = grant(tmp_path)
    root.rename(tmp_path / 'original')
    root.mkdir()
    (root / 'secret.md').write_text('Replacement directory')
    with pytest.raises(PermissionError):
        client.update_rules(source['id'], {'include': ['**/*']})
    assert client.list_sources()[0] == source
    with pytest.raises(PermissionError):
        client.scan(source['id'])


def test_remove_source_erases_local_grant_and_pending_content_but_not_files(tmp_path):
    client, source, root = grant(tmp_path)
    path = root / 'note.md'
    path.write_text('ORIGINAL_MUST_REMAIN')
    first = client.scan(source['id'])
    fingerprint = first['units'][0]['fingerprint']
    client.acknowledge(first['scan_id'])
    path.write_text('PENDING_CONTENT_TO_REMOVE')
    pending = client.scan(source['id'])
    client.remove_source(source['id'])
    assert path.read_text() == 'PENDING_CONTENT_TO_REMOVE'
    assert client.list_sources() == []
    assert client.status()['pending_scans'] == 0
    with pytest.raises((KeyError, PermissionError)):
        client.original(source['id'], 'note.md', fingerprint)
    with pytest.raises((KeyError, PermissionError)):
        collector(tmp_path).scan(source['id'])
    client.acknowledge(pending['scan_id'])
    with sqlite3.connect(client.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0] == 0
        assert db.execute("SELECT operation,status FROM audit WHERE operation='remove_source'").fetchall() == [('remove_source', 'ok')]
    assert b'PENDING_CONTENT_TO_REMOVE' not in client.db_path.read_bytes()


def test_source_management_works_while_paused(tmp_path):
    client, source, root = grant(tmp_path)
    client.pause(True)
    assert client.update_rules(source['id'], {'exclude': ['private/**']})['rule_version'] == 2
    client.remove_source(source['id'])
    assert collector(tmp_path).list_sources() == []
    assert client.status()['paused'] is True


def test_existing_source_schema_migrates_with_same_identity_and_initial_revision(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    with sqlite3.connect(state / 'collector.sqlite3') as db:
        db.execute('CREATE TABLE sources (id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,root TEXT NOT NULL,device INTEGER NOT NULL,inode INTEGER NOT NULL,workflow TEXT NOT NULL)')
    client, source, root = grant(tmp_path)
    assert source['rule_version'] == 1
    assert client.scan(source['id'])['rule_version'] == 1
    assert client.update_rules(source['id'], {'exclude': ['private/**']})['rule_version'] == 2
    assert collector(tmp_path).list_sources()[0]['root'] == str(root)


def test_rule_update_remembers_processed_but_unacknowledged_item_ids(tmp_path):
    client, source, root = grant(tmp_path)
    (root / 'keep.md').write_text('Still selected')
    (root / 'excluded.md').write_text('The server can already have this content')
    delivered = client.scan(source['id'])
    assert {u['external_id'] for u in delivered['units']} == {'keep.md', 'excluded.md'}
    # The server can process this delivery before its acknowledgement arrives.
    client.update_rules(source['id'], {'exclude': ['excluded.md']})
    client = collector(tmp_path)
    revised = client.scan(source['id'])
    assert revised['removed'] == ['excluded.md']
    assert [u['external_id'] for u in revised['units']] == ['keep.md']
    client.acknowledge(delivered['scan_id'])
    assert client.scan(source['id']) == revised
    client.acknowledge(revised['scan_id'])
    next_scan = client.scan(source['id'])
    assert next_scan['units'] == []
    assert next_scan['removed'] == []


def test_unconfirmed_removal_ids_survive_incomplete_scan_and_another_rule_edit(tmp_path, monkeypatch):
    import mycelos.collector.local as module
    client, source, root = grant(tmp_path)
    (root / 'excluded.md').write_text('Previously sent')
    (root / 'remaining.md').write_text('Other document')
    first = client.scan(source['id'])
    client.update_rules(source['id'], {'exclude': ['excluded.md']})
    monkeypatch.setattr(module, 'MAX_FILE_BYTES', 1)
    incomplete = client.scan(source['id'])
    assert incomplete['complete'] is False
    assert incomplete['removed'] == []
    client.acknowledge(incomplete['scan_id'])
    client.update_rules(source['id'], {'exclude': ['excluded.md', 'unused.md']})
    monkeypatch.setattr(module, 'MAX_FILE_BYTES', 1024)
    complete = client.scan(source['id'])
    assert complete['complete'] is True
    assert complete['removed'] == ['excluded.md']
    assert [u['external_id'] for u in complete['units']] == ['remaining.md']
    client.acknowledge(first['scan_id'])
    assert client.scan(source['id']) == complete


def test_rule_edit_does_not_confirm_unacknowledged_data(tmp_path):
    client, source, root = grant(tmp_path)
    (root / 'note.md').write_text('No server receipt yet')
    first = client.scan(source['id'])
    client.update_rules(source['id'], {'exclude': ['unused.md']})
    revised = client.scan(source['id'])
    assert revised['units'] == first['units']
    assert revised['removed'] == []


def test_expired_unacknowledged_inventory_still_supports_later_removal(tmp_path, monkeypatch):
    import mycelos.collector.local as module
    client, source, root = grant(tmp_path)
    (root / 'note.md').write_text('Processed before receipt expired')
    monkeypatch.setattr(module, 'OUTBOX_TTL_SECONDS', 1)
    client.scan(source['id'])
    real_time = module.time.time
    monkeypatch.setattr(module.time, 'time', lambda: real_time() + 3)
    client = collector(tmp_path)
    client.update_rules(source['id'], {'exclude': ['note.md']})
    assert client.scan(source['id'])['removed'] == ['note.md']
def test_project_build_output_does_not_exhaust_document_scan(tmp_path, monkeypatch):
    import mycelos.collector.local as module
    root = tmp_path / 'project'
    root.mkdir()
    (root / '.git').mkdir()
    (root / 'README.md').write_text('Project documentation')
    build = root / 'target' / 'debug'
    build.mkdir(parents=True)
    for i in range(30):
        (build / f'object-{i}.o').write_bytes(b'build output')
    monkeypatch.setattr(module, 'MAX_ENTRIES', 20)
    client = collector(tmp_path)
    source = client.add_source('Project', 'folder', root)
    scan = client.scan(source['id'])
    assert scan['complete'] is True
    assert scan['units'][0]['files'] == ['README.md']
