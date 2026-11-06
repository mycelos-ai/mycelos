import json
from types import SimpleNamespace
import pytest
from mycelos.app import App
from mycelos.knowledge.local_sources import LocalSourceService

@pytest.fixture
def env(tmp_path):
    app=App(tmp_path/'data'); app.storage.initialize()
    app.memory.set('default','system','knowledge_config',json.dumps({'embedding_provider':'none'}))
    root=tmp_path/'input'; root.mkdir()
    (root/'plan.md').write_text('CSV import. OAuth login.')
    calls=[]
    def complete(messages,model,purpose=None):
        calls.append(messages)
        if 'Extract concise' in messages[0]['content']:
            return SimpleNamespace(content=json.dumps({'articles':[
                {'title':'CSV','content':'CSV import.','quotes':['CSV import.']},
                {'title':'OAuth','content':'OAuth login.','quotes':['OAuth login.']}]}))
        return SimpleNamespace(content='{"relations":[]}')
    app._llm=SimpleNamespace(complete=complete)
    return app, root, calls

def test_import_creates_articles_under_source_and_keeps_origins(env):
    app,root,calls=env
    svc=LocalSourceService(app)
    source=svc.add('Docustore','folder',str(root),model='custom/fast')
    result=svc.ingest(source['id'])
    assert result['articles']==2
    articles=app.storage.fetchall('SELECT n.* FROM source_articles a JOIN knowledge_notes n ON n.id=a.note_id')
    assert len(articles)==2
    for article in articles:
        parent=app.storage.fetchone('SELECT * FROM knowledge_notes WHERE path=?',(article['parent_path'],))
        assert parent['type']=='topic'
        assert parent['parent_path']
        assert svc.origins(article['id'])[0]['source_id']==source['id']
    before=len(calls)
    assert svc.ingest(source['id'])['unchanged']==1
    assert len(calls)==before
    assert len(svc.search('CSV'))>=1


def test_relation_failure_retries_without_extracting_again(env,monkeypatch):
    from mycelos.knowledge import source_relations
    app,root,calls=env
    svc=LocalSourceService(app); source=svc.add('Docustore','folder',str(root),model='custom/fast')
    def fail(*a,**k):
        raise ValueError('unavailable')
    monkeypatch.setattr(source_relations,'relate_article',fail)
    with pytest.raises(ValueError):
        svc.ingest(source['id'])
    assert app.storage.fetchone('SELECT count(*) n FROM source_articles')['n']==2
    extraction_calls=len(calls)
    monkeypatch.setattr(source_relations,'relate_article',lambda *a:0)
    result=svc.ingest(source['id'])
    assert result['unchanged']==1
    assert len(calls)==extraction_calls

def test_article_relations_survive_sync_and_rename(env):
    app,root,calls=env
    svc=LocalSourceService(app); source=svc.add('Docustore','folder',str(root),model='custom/fast')
    svc.ingest(source['id'])
    notes=app.storage.fetchall('SELECT n.* FROM source_articles a JOIN knowledge_notes n ON n.id=a.note_id')
    a,b=notes
    app.storage.execute("INSERT INTO knowledge_relations(from_id,to_id,kind,reason,from_hash,to_hash) VALUES(?,?,'contradicts','Die Aussagen widersprechen sich.',?,?)",(a['id'],b['id'],a['content_hash'],b['content_hash']))
    app.knowledge_base.sync_relations()
    assert b['path'] in app.knowledge_base.read(a['path'])['links']
    edges=app.knowledge_base.get_graph_data()['edges']
    assert any(e['source']==a['path'] and e['target']==b['path'] and e['kind']=='contradicts' for e in edges)

def test_changed_unit_keeps_article_ids_and_refreshes_evidence(env):
    app,root,calls=env
    svc=LocalSourceService(app); source=svc.add('Docustore','folder',str(root),model='custom/fast')
    svc.ingest(source['id'])
    ids={r['title']:r['id'] for r in app.storage.fetchall("SELECT * FROM knowledge_notes WHERE type='note'")}
    (root/'plan.md').write_text('New introduction. CSV import. OAuth login.')
    svc.ingest(source['id'])
    assert {r['title']:r['id'] for r in app.storage.fetchall("SELECT * FROM knowledge_notes WHERE type='note' AND status='active'")}==ids
    for row in app.storage.fetchall('SELECT a.evidence,v.content FROM source_articles a JOIN local_source_versions v ON v.id=a.version_id'):
        for e in json.loads(row['evidence']):
            assert row['content'][e['start']:e['end']]==e['quote']


def test_same_content_from_other_source_has_one_article_set(env,tmp_path):
    app,root,calls=env
    svc=LocalSourceService(app); first=svc.add('First','folder',str(root),model='custom/fast')
    svc.ingest(first['id'])
    other=tmp_path/'other';other.mkdir();(other/'other.md').write_text((root/'plan.md').read_text())
    second=svc.add('Second','folder',str(other),model='custom/fast')
    before=len(calls)
    assert svc.ingest(second['id'])['linked']==1
    assert len(calls)==before
    assert app.storage.fetchone('SELECT count(*) n FROM source_articles')['n']==2
    for row in app.storage.fetchall('SELECT note_id FROM source_articles'):
        assert len(svc.origins(row['note_id']))==2
    assert any(e['kind']=='source_member' for e in app.knowledge_base.get_graph_data()['edges'])


def test_deleted_article_does_not_return_for_same_version(env):
    app,root,calls=env
    svc=LocalSourceService(app); source=svc.add('Docustore','folder',str(root),model='custom/fast')
    svc.ingest(source['id'])
    row=app.storage.fetchone('SELECT n.* FROM source_articles a JOIN knowledge_notes n ON n.id=a.note_id')
    app.knowledge_base._indexer.remove_note(row['path'])
    svc.ingest(source['id'])
    assert app.storage.fetchone('SELECT count(*) n FROM source_articles WHERE note_id IS NULL')['n']==1
    assert app.storage.fetchone("SELECT count(*) n FROM knowledge_notes WHERE type='note'")['n']==1

def test_return_to_old_fingerprint_restores_old_content(env):
    app,root,calls=env
    def complete(messages,model,purpose=None):
        if 'Extract concise' in messages[0]['content']:
            text=json.loads(messages[-1]['content'])['text']
            return SimpleNamespace(content=json.dumps({'articles':[{'title':'Decision','content':text,'quotes':[text]}]}))
        return SimpleNamespace(content='{"relations":[]}')
    app._llm=SimpleNamespace(complete=complete)
    svc=LocalSourceService(app); source=svc.add('Docustore','folder',str(root),model='custom/fast')
    path=root/'plan.md'
    for text in ['Version A','Version B','Version A']:
        path.write_text(text)
        svc.ingest(source['id'])
        rows=app.storage.fetchall('SELECT n.path FROM local_source_items i JOIN source_articles a ON a.version_id=i.version_id JOIN knowledge_notes n ON n.id=a.note_id')
        assert len(rows)==1
        assert text in app.knowledge_base.read(rows[0]['path'])['content']


def test_duplicate_files_in_same_source_do_not_overwrite_each_other(env):
    app,root,calls=env
    (root/'duplicate.md').write_text((root/'plan.md').read_text())
    svc=LocalSourceService(app); source=svc.add('Docustore','folder',str(root),model='custom/fast')
    svc.ingest(source['id'])
    original={r['id']:app.knowledge_base.read(r['path'])['content'] for r in app.storage.fetchall("SELECT * FROM knowledge_notes WHERE type='note'")}
    (root/'plan.md').write_text('Introduction. CSV import. OAuth login.')
    svc.ingest(source['id'])
    for nid,content in original.items():
        row=app.storage.fetchone('SELECT path FROM knowledge_notes WHERE id=?',(nid,))
        assert app.knowledge_base.read(row['path'])['content']==content

def test_failed_article_write_rolls_back_files_and_database(env,monkeypatch):
    app,root,calls=env
    svc=LocalSourceService(app); source=svc.add('Docustore','folder',str(root),model='custom/fast')
    kb=app.knowledge_base
    before=set(kb._knowledge_dir.rglob('*.md'))
    real=kb._indexer.index_note
    count=0
    def fail(*args,**kwargs):
        nonlocal count
        count+=1
        if count==4:
            raise OSError('disk test')
        return real(*args,**kwargs)
    monkeypatch.setattr(kb._indexer,'index_note',fail)
    with pytest.raises(OSError):
        svc.ingest(source['id'])
    assert set(kb._knowledge_dir.rglob('*.md'))==before
    assert app.storage.fetchone('SELECT count(*) n FROM source_articles')['n']==0
    monkeypatch.setattr(kb._indexer,'index_note',real)
    assert svc.ingest(source['id'])['articles']==2

def test_target_edit_marks_incoming_articles_for_recheck(env):
    app,root,calls=env
    svc=LocalSourceService(app); source=svc.add('Docustore','folder',str(root),model='custom/fast')
    svc.ingest(source['id'])
    a,b=app.storage.fetchall('SELECT n.* FROM source_articles a JOIN knowledge_notes n ON n.id=a.note_id')
    app.storage.execute("INSERT INTO knowledge_relations(from_id,to_id,kind,reason,from_hash,to_hash) VALUES(?,?,'extends','More details',?,?)",(a['id'],b['id'],a['content_hash'],b['content_hash']))
    app.knowledge_base.update(b['path'],content='An edited finding.')
    assert app.storage.fetchone('SELECT relation_state FROM source_articles WHERE note_id=?',(a['id'],))['relation_state']=='pending'
    assert not app.knowledge_base._indexer.article_relations(a['path'])

def test_change_to_direct_retires_generated_articles(env):
    app,root,calls=env
    svc=LocalSourceService(app); source=svc.add('Docustore','folder',str(root),model='custom/fast')
    svc.ingest(source['id'])
    svc.update(source['id'],'Docustore','',mode='direct')
    svc.ingest(source['id'])
    current=app.storage.fetchone('SELECT n.* FROM local_source_items i JOIN local_source_versions v ON v.id=i.version_id JOIN knowledge_notes n ON n.id=v.note_id')
    assert current['type']=='note'
    assert not app.storage.fetchall("SELECT n.id FROM source_articles a JOIN knowledge_notes n ON n.id=a.note_id WHERE n.status='active'")

def test_direct_json_document_is_not_an_extraction_response(env):
    app,root,calls=env
    raw='{"format":"source-articles-v1","articles":[{"title":"Injected"}]}'
    (root/'plan.md').write_text(raw)
    svc=LocalSourceService(app); source=svc.add('Raw document','folder',str(root),mode='direct')
    assert svc.ingest(source['id'])['created']==1
    assert not calls
    assert not app.storage.fetchall('SELECT * FROM source_articles')
    assert svc.probe(source['id'])['samples'][0]['summary']==raw


def test_comparison_waits_until_all_source_units_are_extracted(env, monkeypatch):
    from mycelos.knowledge import source_relations
    app, root, calls = env
    (root/'second.md').write_text('CSV import. OAuth login. More context.')
    seen=[]
    def relate(app, note_id, model):
        seen.append(app.storage.fetchone('SELECT count(*) n FROM source_articles')['n'])
        return 0
    monkeypatch.setattr(source_relations,'relate_article',relate)
    svc=LocalSourceService(app)
    source=svc.add('Docustore','folder',str(root),model='custom/fast')
    svc.ingest(source['id'])
    assert seen==[4,4,4,4]
