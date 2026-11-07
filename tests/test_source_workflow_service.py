import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mycelos.app import App
from mycelos.knowledge.local_sources import LocalSourceService
from mycelos.gateway.routers.local_sources import router


@pytest.fixture
def env(tmp_path):
    app = App(tmp_path / 'data')
    app.storage.initialize()
    app.memory.set('default', 'system', 'knowledge_config', json.dumps({'embedding_provider': 'none'}))
    root = tmp_path / 'docs'
    root.mkdir()
    (root / 'keep.md').write_text('Sharewise portfolio import')
    (root / 'skip.md').write_text('Excluded private draft')
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(router)
    return app, root, TestClient(api)


def test_rules_persist_and_control_followup_without_model(env):
    app, root, client = env
    svc = LocalSourceService(app, summarize=lambda *_: pytest.fail('No model expected'))
    preview = svc.inspect('folder', str(root))
    workflow = preview['workflow']
    workflow['exclude'].append('skip.md')
    source = svc.add('Docs', 'folder', str(root), mode='direct', workflow=workflow)
    assert source['workflow'] == workflow
    assert svc.preview(source['id'])['files'] == 1
    assert svc.ingest(source['id'])['created'] == 1
    assert svc.ingest(source['id'])['unchanged'] == 1
    (root / 'new.md').write_text('Vorfina planning')
    assert svc.ingest(source['id'])['created'] == 1
    assert not svc.search('Excluded')
    updated = client.put('/api/local-sources/' + source['id'], json={
        'name': 'Renamed', 'mode': 'direct'})
    assert updated.status_code == 200
    assert updated.json()['workflow'] == workflow


def test_file_and_mercurial_persist(env):
    app, root, client = env
    svc = LocalSourceService(app)
    file = svc.add('File', 'file', str(root / 'keep.md'), mode='direct')
    assert svc.ingest(file['id'])['created'] == 1
    (root / '.hg').mkdir()
    (root / 'README.md').write_text('Docustore document storage')
    hg = svc.add('Repository', 'mercurial', str(root), mode='direct')
    assert svc.preview(hg['id'])['files'] == 1
    assert svc.ingest(hg['id'])['created'] == 1
    assert client.get('/api/local-sources/templates').status_code == 200
    assert len(client.get('/api/local-sources/templates').json()) >= 4


def test_rules_are_validated_before_save(env):
    app, root, client = env
    response = client.post('/api/local-sources', json={
        'name':'Bad', 'kind':'folder', 'root':str(root), 'mode':'direct',
        'workflow':{'template_id':'missing', 'version':1, 'include':['*'], 'exclude':[]}})
    assert response.status_code == 422
    assert LocalSourceService(app).list() == []


def test_old_database_migration_preserves_sources_and_item_references(tmp_path):
    import sqlite3
    from pathlib import Path
    from mycelos.storage.database import SQLiteStorage
    import mycelos.storage.database as storage_module
    schema = Path(storage_module.__file__).with_name('schema.sql').read_text()
    schema = schema.replace("'file', 'folder', 'git', 'mercurial', 'codex', 'claude', 'obsidian'", "'folder', 'codex', 'claude'")
    schema = schema.replace('    workflow TEXT,\n', '')
    path = tmp_path / 'old.db'
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute("INSERT INTO local_sources(id,user_id,name,kind,root,mode) VALUES('s','default','Old','folder','/missing','direct')")
    conn.execute("INSERT INTO local_source_versions(id,user_id,fingerprint,recipe,content,summary) VALUES('v','default','f','r','text','text')")
    conn.execute("INSERT INTO local_source_items(id,source_id,external_id,version_id,files,seen_run) VALUES('i','s','doc','v','[]','run')")
    conn.commit()
    conn.close()
    db = SQLiteStorage(path)
    db.initialize()
    assert db.fetchone("SELECT name FROM local_sources WHERE id='s'")['name'] == 'Old'
    assert db.fetchone("SELECT source_id FROM local_source_items WHERE id='i'")['source_id'] == 's'
    db.execute("INSERT INTO local_sources(id,user_id,name,kind,root,mode) VALUES('hg','default','New','mercurial','/missing','direct')")
    assert not db.fetchall('PRAGMA foreign_key_check')
    assert db.fetchone('PRAGMA foreign_keys')['foreign_keys'] == 1
    # A second connection must not rebuild or lose imported rows.
    reopened = SQLiteStorage(path)
    assert reopened.fetchone("SELECT name FROM local_sources WHERE id='hg'")['name'] == 'New'


def test_rule_change_does_not_reanalyze_unchanged_documents(env):
    app, root, _ = env
    calls = []
    def summarize(unit, source):
        calls.append(unit.external_id)
        return unit.content
    svc = LocalSourceService(app, summarize=summarize)
    source = svc.add('Docs', 'folder', str(root), model='test-only')
    assert svc.ingest(source['id'])['created'] == 2
    assert len(calls) == 2
    workflow = source['workflow']
    workflow['exclude'].append('skip.md')
    svc.update(source['id'], 'Docs', '', 'analyze', 'test-only', workflow)
    assert svc.ingest(source['id'])['unchanged'] == 1
    assert len(calls) == 2
    (root / 'keep.md').write_text('Sharewise portfolio import with validation')
    assert svc.ingest(source['id'])['updated'] == 1
    assert len(calls) == 3


def test_legacy_source_freezes_default_workflow_even_when_offline(env):
    app, root, _ = env
    app.storage.execute("INSERT INTO local_sources(id,user_id,name,kind,root,mode) VALUES('old','default','Old','folder','/missing-source','direct')")
    svc = LocalSourceService(app)
    source = svc.get('old')
    stored = app.storage.fetchone("SELECT workflow FROM local_sources WHERE id='old'")['workflow']
    assert json.loads(stored) == source['workflow']
    assert next(s for s in svc.list() if s['id'] == 'old')['workflow'] == source['workflow']
