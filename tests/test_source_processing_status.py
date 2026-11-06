"""Expose pending processing from saved metadata without model or file access."""
import hashlib
import json

import pytest

from tests.test_source_checks import env, imported  # noqa: F401
from tests.test_source_card_browser import card_server  # noqa: F401
from mycelos.knowledge.local_sources import LocalSourceService


def current_recipe(app, sid):
    source = LocalSourceService(app).get(sid)
    return hashlib.sha256(json.dumps([source['mode'], source['model'], source['purpose'],
                                     2 if source['mode'] == 'analyze' else 1]).encode()).hexdigest()


@pytest.mark.parametrize('scanned', [False, True])
def test_recipe_change_exposes_unchanged_import_without_reading_files(env, scanned):
    app, root, sid, svc = env
    (root / 'a.md').write_text('First')
    imported(app, sid, 'a.md', 'First', recipe='legacy-recipe')
    if scanned:
        svc.check(sid)
    root.rename(root.with_name('offline'))
    assert svc.status(sid)['pending_count'] == 1
    assert svc.status(sid)['reprocess_count'] == 1
    assert [(row['external_id'], row['change']) for row in svc.pending(sid)] == [('a.md', 'reprocess')]
    app.storage.execute('UPDATE local_source_versions SET recipe=?', (current_recipe(app, sid),))
    assert svc.status(sid)['pending_count'] == 0
    assert svc.pending(sid) == []


def test_removed_and_unavailable_files_do_not_become_reprocess_work(env):
    app, root, sid, svc = env
    imported(app, sid, 'gone.md', 'Gone', recipe='legacy-recipe')
    imported(app, sid, 'unavailable.md', 'Unavailable', available=0, recipe='legacy-recipe')
    assert svc.check(sid)['removed_count'] == 1
    assert [(row['external_id'], row['change']) for row in svc.pending(sid)] == [('gone.md', 'removed')]
    assert svc.status(sid)['reprocess_count'] == 0
    assert svc.status(sid)['pending_count'] == 1


def test_changed_text_takes_priority_over_recipe_change(env):
    app, root, sid, svc = env
    imported(app, sid, 'a.md', 'First', recipe='legacy-recipe')
    (root / 'a.md').write_text('Second')
    assert svc.check(sid)['pending_count'] == 1
    assert svc.pending(sid)[0]['change'] == 'changed'
    assert svc.status(sid)['reprocess_count'] == 0


def test_relations_count_only_current_active_articles_once(env):
    app, root, sid, svc = env
    imported(app, sid, 'a.md', 'Old')
    historical = app.storage.fetchone('SELECT version_id FROM local_source_items')['version_id']
    imported(app, sid, 'a.md', 'First', recipe='legacy-recipe')
    version = app.storage.fetchone('SELECT version_id FROM local_source_items')['version_id']
    app.storage.execute('UPDATE local_source_versions SET recipe=? WHERE id=?', (current_recipe(app, sid), version))
    for number, version_id, state, note_status in [(1, version, 'pending', 'active'),
                                                 (2, version, 'pending', 'archived'),
                                                 (3, version, 'done', 'active'),
                                                 (4, historical, 'pending', 'active')]:
        app.storage.execute('INSERT INTO knowledge_notes(id,path,title,status) VALUES(?,?,?,?)',
                            (number, f'{number}.md', f'Article {number}', note_status))
        app.storage.execute('INSERT INTO source_articles(version_id,article_key,note_id,evidence,relation_state) VALUES(?,?,?,?,?)',
                            (version_id, str(number), number, '[]', state))
    app.storage.execute("INSERT INTO source_articles(version_id,article_key,note_id,evidence) VALUES(?,?,NULL,'[]')", (version, 'deleted'))
    app.storage.execute("INSERT INTO local_source_items(id,source_id,external_id,version_id,files,seen_run) VALUES('duplicate',?,'b.md',?,'[]','test')", (sid, version))
    assert svc.status(sid)['pending_count'] == 0
    assert svc.status(sid)['relations_pending_count'] == 1
    app.storage.execute("UPDATE source_articles SET relation_state='done' WHERE note_id=1")
    assert svc.status(sid)['relations_pending_count'] == 0


def test_card_distinguishes_reprocess_and_pending_relations_without_starting_work(page, card_server):
    from playwright.sync_api import expect
    from tests.test_source_check_browser import prepare
    card, _, requests = prepare(page, card_server[0], {
        'pending_count': 3, 'reprocess_count': 2, 'relations_pending_count': 4,
    }, [{'external_id': 'a.md', 'change': 'reprocess', 'files': []}])
    expect(card.get_by_text('1 Dateiänderungen · 2 Inhalte erneut verarbeiten', exact=True)).to_be_visible()
    expect(card.get_by_text('4 Artikel mit offenen Querverbindungen', exact=True)).to_be_visible()
    card.get_by_text('Vorgemerkte Inhalte ansehen', exact=True).click()
    expect(card.get_by_text('Erneut verarbeiten', exact=True)).to_be_visible()
    expect(card.get_by_role('button', name='Import starten', exact=True)).to_be_enabled()
    assert not [request for request in requests if request[0] == 'POST']


def test_old_analysis_recipe_requires_new_article_extraction(env):
    app, root, sid, svc = env
    LocalSourceService(app).update(sid, 'Test', '', 'analyze', model='explicit-test-model')
    previous_recipe = hashlib.sha256(json.dumps(['analyze', 'explicit-test-model', '', 1]).encode()).hexdigest()
    imported(app, sid, 'a.md', 'First', recipe=previous_recipe)
    (root / 'a.md').write_text('First')
    assert svc.check(sid)['reprocess_count'] == 1
    assert svc.pending(sid)[0]['change'] == 'reprocess'
    app.storage.execute('UPDATE local_source_versions SET recipe=?', (current_recipe(app, sid),))
    assert svc.status(sid)['pending_count'] == 0
