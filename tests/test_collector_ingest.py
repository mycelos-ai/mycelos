"""Synthetic collector input through the existing knowledge service."""
import hashlib
import json
from copy import deepcopy
from uuid import uuid4

import pytest

from mycelos.app import App
from mycelos.collector.crypto import Identity, encode
from mycelos.collector.server import CollectorServer
from mycelos.knowledge.local_sources import LocalSourceService


@pytest.fixture
def env(tmp_path):
    app = App(tmp_path / 'server')
    app.storage.initialize()
    app.memory.set('default', 'system', 'knowledge_config', json.dumps({'embedding_provider': 'none'}))
    server = CollectorServer(app)
    for identifier, user in [('device', 'default'), ('other', 'default'), ('bob-device', 'bob')]:
        app.storage.execute('INSERT INTO collector_devices(id,user_id,name,public_key,fingerprint,attempt,status,created) VALUES(?,?,?,?,?,?,?,1)',
                            (identifier, user, identifier, encode(Identity.generate().public), identifier, 'attempt', 'active'))
    return app, server, server._device('device')


def unit(name='a.md', content='The first test document.', metadata=None):
    return dict(external_id=name, title=name, content=content, files=[name], project='',
                metadata=metadata or {}, fingerprint=hashlib.sha256(content.encode()).hexdigest())


def scan(*units, source_id='source', removed=None, complete=True, kind='folder'):
    return dict(scan_id=uuid4().hex, source_id=source_id, name='Test source', kind=kind, workflow=None,
                rule_version=1, units=list(units), removed=removed or [], complete=complete)


def configure(app, device, body, mode='direct', model=None):
    from mycelos.collector.ingest import register_source
    source = register_source(app, device, body)
    svc = LocalSourceService(app, device['user_id'])
    svc.update(source['id'], source['name'], '', mode, model)
    svc.pause(source['id'], False)
    return svc


def test_registration_requires_server_configuration_before_import(env):
    from mycelos.collector.ingest import register_source, ingest_scan
    app, server, device = env
    body = scan(unit())
    source = register_source(app, device, body)
    assert source['id'] == 'source'
    assert source['root'] == 'collector://device/source'
    assert (source['mode'], source['model'], source['status']) == ('analyze', None, 'paused')
    with pytest.raises(ValueError):
        ingest_scan(app, device, body)
    assert app.storage.fetchone('SELECT count(*) n FROM local_source_items')['n'] == 0


def test_remote_import_and_management_do_not_read_server_source_paths(env, monkeypatch):
    from mycelos.collector.ingest import ingest_scan
    from mycelos.knowledge.source_checks import SourceCheckService
    app, server, device = env
    body = scan(unit())
    svc = configure(app, device, body)
    def forbidden(*args, **kwargs):
        raise AssertionError('The server attempted to inspect a collector root')
    monkeypatch.setattr('mycelos.knowledge.local_sources.read_units', forbidden)
    monkeypatch.setattr('mycelos.knowledge.local_sources.inspect_source', forbidden)
    assert ingest_scan(app, device, body)['created'] == 1
    for call in [svc.preview, svc.probe, svc.enqueue, svc.ingest, SourceCheckService(app).check]:
        with pytest.raises(ValueError, match='[Ss]ammler|[Cc]ollector'):
            call('source')
    assert svc.search('first')


def test_delta_keeps_untouched_items_and_only_complete_scan_removes(env):
    from mycelos.collector.ingest import ingest_scan
    app, server, device = env
    body = scan(unit(), unit('b.md', 'The second test document.'))
    configure(app, device, body)
    ingest_scan(app, device, body)
    ingest_scan(app, device, scan(unit(content='The changed first document.')))
    assert app.storage.fetchall('SELECT external_id,available FROM local_source_items ORDER BY external_id') == [
        {'external_id': 'a.md', 'available': 1}, {'external_id': 'b.md', 'available': 1}]
    with pytest.raises(ValueError):
        ingest_scan(app, device, scan(removed=['b.md'], complete=False))
    ingest_scan(app, device, scan(removed=['b.md']))
    assert app.storage.fetchone("SELECT available FROM local_source_items WHERE external_id='b.md'")['available'] == 0
    assert app.storage.fetchone("SELECT count(*) n FROM knowledge_notes WHERE type='note'")['n'] == 2


def test_retry_after_interrupted_run_does_not_duplicate_completed_unit(env, monkeypatch):
    from mycelos.collector.ingest import ingest_scan
    app, server, device = env
    body = scan(unit(), unit('b.md', 'A second independent document.'))
    configure(app, device, body)
    original = LocalSourceService._write_note
    def crash(self, unit, *args):
        if unit.external_id == 'b.md':
            raise KeyboardInterrupt('simulated process loss')
        return original(self, unit, *args)
    with monkeypatch.context() as patch:
        patch.setattr(LocalSourceService, '_write_note', crash)
        with pytest.raises(KeyboardInterrupt):
            ingest_scan(app, device, body)
    result = ingest_scan(app, device, body)
    assert (result['unchanged'], result['created']) == (1, 1)
    assert app.storage.fetchone("SELECT count(*) n FROM knowledge_notes WHERE type='note'")['n'] == 2


def test_deleted_version_stays_suppressed_but_changed_content_returns(env):
    from mycelos.collector.ingest import ingest_scan
    app, server, device = env
    body = scan(unit())
    configure(app, device, body)
    ingest_scan(app, device, body)
    app.storage.execute('DELETE FROM knowledge_notes')
    ingest_scan(app, device, scan(unit()))
    assert app.storage.fetchone('SELECT count(*) n FROM knowledge_notes')['n'] == 0
    other = scan(unit(), source_id='second-source')
    configure(app, device, other)
    ingest_scan(app, device, other)
    assert app.storage.fetchone('SELECT count(*) n FROM knowledge_notes')['n'] == 0
    ingest_scan(app, device, scan(unit(content='A changed document.')))
    assert app.storage.fetchone('SELECT count(*) n FROM knowledge_notes')['n'] == 1


@pytest.mark.parametrize('mutation', ['device', 'user', 'fingerprint', 'rules', 'source_path', 'workflow'])
def test_rejects_wrong_identity_content_and_rules(env, mutation):
    from mycelos.collector.ingest import register_source, ingest_scan
    app, server, device = env
    body = scan(unit())
    configure(app, device, body)
    if mutation == 'device':
        device = server._device('other')
    elif mutation == 'user':
        device = {**device, 'user_id': 'bob'}
    elif mutation == 'fingerprint':
        body['units'][0]['fingerprint'] = '0' * 64
    elif mutation == 'rules':
        body['rule_version'] = 0
    elif mutation == 'source_path':
        body['source_id'] = '../outside'
    else:
        body['workflow'] = {'include': ['**/*.txt']}
    with pytest.raises(ValueError):
        ingest_scan(app, device, body)
    assert app.storage.fetchone('SELECT count(*) n FROM local_source_items')['n'] == 0


def test_distinct_users_do_not_share_versions_or_source_access(env):
    from mycelos.collector.ingest import ingest_scan
    app, server, device = env
    body = scan(unit())
    svc = configure(app, device, body)
    ingest_scan(app, device, body)
    bob = server._device('bob-device')
    other = scan(unit(), source_id='bob-source')
    configure(app, bob, other)
    assert ingest_scan(app, bob, other)['created'] == 1
    assert app.storage.fetchone('SELECT count(*) n FROM local_source_versions')['n'] == 2
    with pytest.raises(ValueError):
        LocalSourceService(app, 'bob').get('source')


def test_explicit_adoption_keeps_existing_source_and_item_ids(env, tmp_path):
    from mycelos.collector.ingest import register_source, adopt_source, ingest_scan
    app, server, device = env
    root = tmp_path / 'old-root'
    root.mkdir()
    (root / 'a.md').write_text('The first test document.')
    svc = LocalSourceService(app)
    old = svc.add('Existing source', 'folder', str(root), mode='direct')
    svc.ingest(old['id'])
    item = app.storage.fetchone('SELECT * FROM local_source_items')
    body = scan(unit(name=item['external_id']), source_id=old['id'])
    with pytest.raises(ValueError):
        register_source(app, device, body)
    with pytest.raises(ValueError):
        adopt_source(app, 'bob', device, body, local_grant=True)
    with pytest.raises(ValueError):
        adopt_source(app, 'default', device, body, local_grant=False)
    adopted = adopt_source(app, 'default', device, body, local_grant=True)
    assert adopted['id'] == old['id']
    assert adopted['root'].startswith('collector://')
    assert ingest_scan(app, device, body)['unchanged'] == 1
    assert app.storage.fetchone('SELECT id FROM local_source_items')['id'] == item['id']


def test_revocation_during_analysis_prevents_knowledge_write(env, monkeypatch):
    from mycelos.collector.ingest import ingest_scan
    app, server, device = env
    body = scan(unit())
    configure(app, device, body, mode='analyze', model='test-model')
    def revoke(self, unit, source):
        server.set_status('default', 'device', 'revoked')
        return 'A summary'
    monkeypatch.setattr(LocalSourceService, '_summarize', revoke)
    with pytest.raises(ValueError):
        ingest_scan(app, device, body)
    assert app.storage.fetchone('SELECT count(*) n FROM knowledge_notes')['n'] == 0
    assert app.storage.fetchone('SELECT count(*) n FROM local_source_items')['n'] == 0


def test_deleted_version_does_not_return_after_an_intermediate_change(env):
    from mycelos.collector.ingest import ingest_scan
    app, server, device = env
    body = scan(unit())
    configure(app, device, body)
    ingest_scan(app, device, body)
    app.storage.execute('DELETE FROM knowledge_notes')
    ingest_scan(app, device, scan(unit(content='An intermediate change.')))
    ingest_scan(app, device, body)
    row = app.storage.fetchone('SELECT v.note_id FROM local_source_items i JOIN local_source_versions v ON v.id=i.version_id')
    assert row['note_id'] is None


def test_analysis_reuses_evidence_articles_and_deleted_article_stays_absent(env):
    from mycelos.collector.ingest import ingest_scan
    from types import SimpleNamespace
    app, server, device = env
    body = scan(unit(content='CSV import. OAuth login.'))
    svc = configure(app, device, body, mode='analyze', model='test-model')
    def complete(messages, model, purpose=None):
        assert model == 'test-model'
        if purpose == 'source_analysis':
            return SimpleNamespace(content=json.dumps({'articles': [
                {'title': 'CSV', 'content': 'CSV import.', 'quotes': ['CSV import.']},
                {'title': 'OAuth', 'content': 'OAuth login.', 'quotes': ['OAuth login.']},
            ]}))
        return SimpleNamespace(content='{"relations":[]}')
    app._llm = SimpleNamespace(complete=complete)
    assert ingest_scan(app, device, body)['articles'] == 2
    article = app.storage.fetchone('SELECT n.* FROM source_articles a JOIN knowledge_notes n ON n.id=a.note_id')
    assert svc.origins(article['id'])[0]['source_id'] == 'source'
    app.knowledge_base._indexer.remove_note(article['path'])
    ingest_scan(app, device, body)
    assert app.storage.fetchone('SELECT count(*) n FROM source_articles WHERE note_id IS NULL')['n'] == 1
    assert app.storage.fetchone("SELECT count(*) n FROM knowledge_notes WHERE type='note'")['n'] == 1
    for row in app.storage.fetchall('SELECT a.evidence,v.content FROM source_articles a JOIN local_source_versions v ON v.id=a.version_id'):
        for evidence in json.loads(row['evidence']):
            assert row['content'][evidence['start']:evidence['end']] == evidence['quote']


def test_obsidian_delta_preserves_metadata_and_updates_explicit_links(env):
    from mycelos.collector.ingest import ingest_scan
    app, server, device = env
    metadata = {'path': 'a.md', 'properties': {'aliases': ['First']}, 'links': [
        {'target': 'b.md', 'state': 'resolved', 'reference': 'b'}]}
    body = scan(unit(metadata=metadata), unit('b.md', 'A target note.', {'path': 'b.md', 'links': []}), kind='obsidian')
    svc = configure(app, device, body)
    assert ingest_scan(app, device, body)['relations'] == 1
    item = app.storage.fetchone("SELECT id FROM local_source_items WHERE external_id='a.md'")
    assert svc.evidence(item['id'])['source_metadata']['properties']['aliases'] == ['First']
    assert ingest_scan(app, device, scan(kind='obsidian'))['relations'] == 1
    ingest_scan(app, device, scan(removed=['b.md'], kind='obsidian'))
    assert app.storage.fetchone("SELECT count(*) n FROM knowledge_relations WHERE created_by='obsidian:source'")['n'] == 0


def test_revocation_between_model_sections_stops_further_calls(env):
    from mycelos.collector.ingest import ingest_scan
    from types import SimpleNamespace
    app, server, device = env
    body = scan(unit(content='Evidence. ' * 3000))
    configure(app, device, body, mode='analyze', model='test-model')
    requests = []
    def complete(messages, model, purpose=None):
        requests.append(purpose)
        server.set_status('default', 'device', 'revoked')
        return SimpleNamespace(content=json.dumps({'articles': [
            {'title': 'Evidence', 'content': 'Evidence.', 'quotes': ['Evidence.']}]}))
    app._llm = SimpleNamespace(complete=complete)
    with pytest.raises(ValueError):
        ingest_scan(app, device, body)
    assert requests == ['source_analysis']
    assert app.storage.fetchone('SELECT count(*) n FROM local_source_items')['n'] == 0
    assert app.storage.fetchone('SELECT count(*) n FROM knowledge_notes')['n'] == 0


def test_provenance_only_delta_refreshes_files_project_and_obsidian_links(env):
    from mycelos.collector.ingest import ingest_scan
    app, server, device = env
    body = scan(unit(metadata={'path': 'a.md', 'links': []}), kind='obsidian')
    configure(app, device, body)
    ingest_scan(app, device, body)
    changed = deepcopy(body)
    changed['scan_id'] = uuid4().hex
    changed['units'][0].update(files=['moved/a.md'], project='New project', metadata={'path': 'moved/a.md', 'links': []})
    result = ingest_scan(app, device, changed)
    assert (result['unchanged'], result['created']) == (1, 0)
    row = app.storage.fetchone('SELECT files,project FROM local_source_items')
    assert json.loads(row['files']) == ['moved/a.md']
    assert row['project'] == 'New project'
    assert json.loads(app.storage.fetchone('SELECT metadata FROM local_source_metadata')['metadata'])['path'] == 'moved/a.md'
    assert app.storage.fetchone('SELECT count(*) n FROM knowledge_notes')['n'] == 1


@pytest.mark.parametrize('metadata', [{'links': 'not a list'}, {'links': [{}]}, {'links': [], 'properties': []}])
def test_malformed_obsidian_metadata_fails_before_any_unit_write(env, metadata):
    from mycelos.collector.ingest import ingest_scan
    app, server, device = env
    valid = scan(unit(metadata={'links': []}), kind='obsidian')
    configure(app, device, valid)
    body = scan(unit(metadata={'links': []}), unit('b.md', 'Another note.', metadata), kind='obsidian')
    with pytest.raises(ValueError):
        ingest_scan(app, device, body)
    assert app.storage.fetchone('SELECT count(*) n FROM knowledge_notes')['n'] == 0


def test_deleted_content_stays_suppressed_after_recipe_change_without_model_call(env):
    from mycelos.collector.ingest import ingest_scan
    from types import SimpleNamespace
    app, server, device = env
    body = scan(unit())
    svc = configure(app, device, body)
    ingest_scan(app, device, body)
    app.storage.execute('DELETE FROM knowledge_notes')
    svc.update('source', 'Test source', 'A new purpose', 'analyze', 'test-model')
    def forbidden(*args, **kwargs):
        raise AssertionError('Deleted source text reached the model')
    app._llm = SimpleNamespace(complete=forbidden)
    ingest_scan(app, device, body)
    assert app.storage.fetchone('SELECT count(*) n FROM knowledge_notes')['n'] == 0


def test_remote_status_remains_readable_and_legacy_schedule_stops(env):
    from mycelos.knowledge.source_checks import SourceCheckService
    app, server, device = env
    configure(app, device, scan(unit()))
    checks = SourceCheckService(app)
    assert checks.status('source')['pending_count'] == 0
    app.storage.execute("UPDATE local_source_checks SET interval_minutes=15,next_check='2000-01-01T00:00:00Z' WHERE source_id='source'")
    assert checks.due() == []
    with pytest.raises(ValueError):
        checks.configure('source', 15)
    with pytest.raises(ValueError):
        checks.check('source')


def test_confirmed_local_rule_revision_updates_same_source_and_keeps_history(env):
    from mycelos.collector.ingest import register_source,ingest_scan
    app,server,device=env
    initial=scan(unit())
    configure(app,device,initial)
    ingest_scan(app,device,initial)
    changed=scan(source_id=initial['source_id'])
    changed['rule_version']=2
    changed['workflow']={'include':['**/*.txt']}
    changed['removed']=[initial['units'][0]['external_id']]
    result=register_source(app,device,changed)
    assert result['id']==initial['source_id']
    ingest_scan(app,device,changed)
    assert app.storage.fetchone('SELECT available FROM local_source_items')['available']==0
    assert app.storage.fetchone('SELECT count(*) AS n FROM collector_rule_history')['n']==2
    with pytest.raises(ValueError):
        register_source(app,device,initial)
