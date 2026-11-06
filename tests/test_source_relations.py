"""Source relations validate model output before changing persistent links."""
import json
from types import SimpleNamespace

import pytest

from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def app(tmp_path):
    storage = SQLiteStorage(tmp_path / 'test.db')
    storage.initialize()
    storage.execute('''CREATE TABLE IF NOT EXISTS knowledge_relations (
        from_id INTEGER REFERENCES knowledge_notes(id), to_id INTEGER REFERENCES knowledge_notes(id),
        kind TEXT, reason TEXT, from_hash TEXT, to_hash TEXT,
        created_by TEXT DEFAULT 'source-analysis', PRIMARY KEY(from_id,to_id,kind))''')
    contents = {}
    result = SimpleNamespace(storage=storage, contents=contents, hits=[], calls=[], answer={'relations': []})
    def complete(messages, model, purpose=None):
        result.calls.append((messages, model))
        if hasattr(result, 'during_call'):
            result.during_call()
        return SimpleNamespace(content=result.answer if isinstance(result.answer, str) else json.dumps(result.answer))
    result.llm = SimpleNamespace(complete=complete)
    result.knowledge_base = SimpleNamespace(
        read=lambda path: {'content': contents[path]} if path in contents else None,
        search=lambda query, limit: result.hits[:limit])
    return result


def note(app, id, parent='topic', status='active', type='note', content='Evidence'):
    path = f'notes/{id}.md'
    app.storage.execute('''INSERT INTO knowledge_notes(id,path,title,parent_path,status,type,content_hash)
                        VALUES (?,?,?,?,?,?,?)''', (id, path, f'Article {id}', parent, status, type, f'hash{id}'))
    app.contents[path] = content
    return {'id': id, 'path': path}


def old_link(app, kind='related', creator='source-analysis'):
    app.storage.execute('''INSERT INTO knowledge_relations
        (from_id,to_id,kind,reason,from_hash,to_hash,created_by) VALUES (1,2,?,'old','old','old',?)''',
        (kind, creator))


def run(app):
    from mycelos.knowledge.source_relations import relate_article
    return relate_article(app, 1, 'explicit-test-model')


def test_persists_typed_reasoned_links_and_preserves_manual_links(app):
    note(app, 1)
    note(app, 2)
    old_link(app)
    old_link(app, 'supports', 'user')
    app.answer = {'relations': [{'target_id': 2, 'kind': 'supports', 'reason': 'Beide Tests bestätigen die Aussage.'},
                               {'target_id': 2, 'kind': 'extends', 'reason': 'Der Artikel ergänzt den Versuch.'}]}
    assert run(app) == 1
    rows = app.storage.fetchall('SELECT * FROM knowledge_relations ORDER BY kind')
    assert [(row['kind'], row['created_by']) for row in rows] == [('extends', 'source-analysis'), ('supports', 'user')]
    assert rows[0]['from_hash'] == 'hash1'
    assert rows[0]['to_hash'] == 'hash2'
    assert rows[0]['reason'] == 'Der Artikel ergänzt den Versuch.'
    assert app.calls[0][1] == 'explicit-test-model'


def test_candidates_include_hierarchy_and_global_with_bounded_evidence(app):
    note(app, 1, content='S' * 15000)
    for id in range(2, 18):
        hit = note(app, id, parent='topic' if id < 10 else 'elsewhere', content='T' * 6000)
        if id >= 10:
            app.hits.append(hit)
    app.hits.extend([note(app, 30, status='archived'), note(app, 31, type='topic')])
    run(app)
    payload = json.loads(app.calls[0][0][-1]['content'])
    assert len(payload['source']['content']) <= 12000
    assert len(payload['candidates']) <= 8
    assert any(c['id'] < 10 for c in payload['candidates'])
    assert any(10 <= c['id'] < 18 for c in payload['candidates'])
    assert all(len(c['content']) <= 4000 for c in payload['candidates'])
    assert not {1, 30, 31} & {c['id'] for c in payload['candidates']}


@pytest.mark.parametrize('answer', [
    {}, {'relations': [], 'extra': True}, {'relations': 'wrong'},
    {'relations': [{'target_id': 999, 'kind': 'related', 'reason': 'Grund'}]},
    {'relations': [{'target_id': 1, 'kind': 'related', 'reason': 'Grund'}]},
    {'relations': [{'target_id': True, 'kind': 'related', 'reason': 'Grund'}]},
    {'relations': [{'target_id': 2, 'kind': 'merge', 'reason': 'Grund'}]},
    {'relations': [{'target_id': 2, 'kind': 'related', 'reason': ''}]},
    {'relations': [{'target_id': 2, 'kind': 'related', 'reason': 'Grund', 'extra': 1}]},
    {'relations': [{'target_id': 2, 'kind': 'related', 'reason': 'Grund'}] * 2},
    'x' * 20000,
])
def test_invalid_response_preserves_existing_links(app, answer):
    note(app, 1)
    note(app, 2)
    old_link(app)
    app.answer = answer
    with pytest.raises(ValueError):
        run(app)
    assert app.storage.fetchone('SELECT reason FROM knowledge_relations')['reason'] == 'old'


@pytest.mark.parametrize('changed_id', [1, 2])
def test_hash_change_during_model_call_preserves_existing_links(app, changed_id):
    note(app, 1)
    note(app, 2)
    old_link(app)
    app.during_call = lambda: app.storage.execute('UPDATE knowledge_notes SET content_hash=? WHERE id=?', ('new', changed_id))
    with pytest.raises(ValueError, match='geändert'):
        run(app)
    assert app.storage.fetchone('SELECT reason FROM knowledge_relations')['reason'] == 'old'


def test_no_candidates_clears_only_automatic_links_without_model(app):
    note(app, 1)
    note(app, 2, status='archived')
    old_link(app)
    old_link(app, 'supports', 'user')
    assert run(app) == 0
    assert not app.calls
    assert app.storage.fetchall('SELECT created_by FROM knowledge_relations') == [{'created_by': 'user'}]


def test_unindexed_content_change_preserves_existing_links(app):
    note(app, 1)
    note(app, 2)
    old_link(app)
    app.during_call = lambda: app.contents.update({'notes/2.md': 'Changed evidence'})
    with pytest.raises(ValueError, match='geändert'):
        run(app)
    assert app.storage.fetchone('SELECT reason FROM knowledge_relations')['reason'] == 'old'


def test_storage_failure_rolls_back_removed_links(app):
    import sqlite3
    note(app, 1)
    note(app, 2)
    old_link(app)
    app.answer = {'relations': [{'target_id': 2, 'kind': 'extends', 'reason': 'Zusätzlicher Versuch.'}]}
    app.storage.execute('''CREATE TRIGGER fail_relation BEFORE INSERT ON knowledge_relations
                          BEGIN SELECT RAISE(ABORT, 'write failure'); END''')
    with pytest.raises(sqlite3.IntegrityError):
        run(app)
    assert app.storage.fetchone('SELECT reason FROM knowledge_relations')['reason'] == 'old'


def test_search_filters_self_topics_inactive_and_missing_content(app):
    app.hits = [note(app, 1), note(app, 2, type='topic'), note(app, 3, status='archived'), note(app, 4)]
    del app.contents['notes/4.md']
    assert run(app) == 0
    assert not app.calls


def test_model_required_before_any_model_request(app):
    from mycelos.knowledge.source_relations import relate_article
    note(app, 1)
    note(app, 2)
    with pytest.raises(ValueError, match='Analysemodell'):
        relate_article(app, 1, '')
    assert not app.calls


def test_considers_articles_in_sibling_topics(app):
    note(app,10,parent='source-root',type='topic')
    note(app,11,parent='source-root',type='topic')
    note(app,1,parent='notes/10.md')
    note(app,2,parent='notes/11.md')
    app.answer={'relations':[{'target_id':2,'kind':'related','reason':'Beide beschreiben dieselbe Anwendung.'}]}
    assert run(app)==1


def test_link_response_accepts_single_json_code_block():
    from mycelos.knowledge.source_relations import _validate_response
    raw = '```json\n{"relations":[{"target_id":7,"kind":"related","reason":"Gleiche Entscheidung"}]}\n```'
    assert _validate_response(raw, {7: {}})[0]['target_id'] == 7
    with pytest.raises(ValueError):
        _validate_response(raw, {8: {}})
