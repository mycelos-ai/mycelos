import json
import pytest
from mycelos.app import App
from mycelos.knowledge.local_sources import LocalSourceService
from mycelos.knowledge.local_readers import read_units


@pytest.fixture
def vault(tmp_path):
    root=tmp_path/'vault';root.mkdir()
    (root/'.obsidian').mkdir();(root/'.git').mkdir()
    (root/'.obsidian'/'private.md').write_text('Do not import')
    (root/'Templates').mkdir();(root/'Templates'/'daily.md').write_text('Template')
    (root/'Project.md').write_text('---\ntitle: Sharewise\ntags: [idea]\naliases: [Portfolio]\n---\nPortfolio import. [[Design#Details]] ![[diagram.png]]')
    (root/'Design.md').write_text('# Details\nCSV mapping for Sharewise.')
    (root/'diagram.png').write_bytes(b'demo attachment')
    return root


def test_vault_in_git_preserves_notes_properties_and_links(vault):
    units=list(read_units('obsidian',vault))
    assert len(units)==2
    project=next(u for u in units if u.title=='Sharewise')
    assert project.metadata['properties']['tags']==['idea']
    assert project.metadata['links'][0]['target']=='Design.md'
    assert project.metadata['links'][0]['anchor']=='Details'
    assert project.metadata['links'][1]['kind']=='embed'
    assert project.metadata['links'][1]['target']=='diagram.png'


def test_import_preserves_links_and_note_identity_on_move(vault,tmp_path):
    app=App(tmp_path/'data');app.storage.initialize()
    app.memory.set('default','system','knowledge_config',{'embedding_provider':'none'})
    svc=LocalSourceService(app)
    source=svc.add('Obsidian','obsidian',str(vault),mode='direct')
    assert svc.preview(source['id'])['units']==2
    svc.ingest(source['id'])
    old=app.storage.fetchone("SELECT v.note_id FROM local_source_items i JOIN local_source_versions v ON v.id=i.version_id WHERE i.source_id=? AND i.external_id='Design.md'",(source['id'],))
    relations=app.storage.fetchall("SELECT * FROM knowledge_relations WHERE created_by=?",('obsidian:'+source['id'],))
    assert len(relations)==1
    item=app.storage.fetchone("SELECT id FROM local_source_items WHERE source_id=? AND external_id='Project.md'",(source['id'],))
    assert svc.evidence(item['id'])['source_metadata']['properties']['aliases']==['Portfolio']
    (vault/'Design.md').rename(vault/'Design moved.md')
    (vault/'Project.md').write_text((vault/'Project.md').read_text().replace('Design#','Design moved#'))
    svc.ingest(source['id'])
    new=app.storage.fetchone("SELECT v.note_id FROM local_source_items i JOIN local_source_versions v ON v.id=i.version_id WHERE i.source_id=? AND i.external_id='Design moved.md'",(source['id'],))
    assert new['note_id']==old['note_id']
    relations=app.storage.fetchall("SELECT * FROM knowledge_relations WHERE created_by=?",('obsidian:'+source['id'],))
    assert len(relations)==1
    assert relations[0]['to_id']==old['note_id']


def test_ambiguous_links_and_outside_paths_remain_unresolved(vault,tmp_path):
    for folder in ['a','b']:
        (vault/folder).mkdir();(vault/folder/'Same.md').write_text(folder)
    (vault/'Project.md').write_text('[[Same]] [[../secret]] [Design](Design.md#Details) `[[Ignored]]`')
    note=next(u for u in read_units('obsidian',vault) if u.external_id=='Project.md')
    links=note.metadata['links']
    assert [l['state'] for l in links]==['ambiguous','outside','resolved']
    assert links[2]['target']=='Design.md'


def test_attachment_short_name_and_symlink_are_handled(vault,tmp_path):
    (vault/'Assets').mkdir()
    (vault/'diagram.png').rename(vault/'Assets'/'diagram.png')
    (vault/'alias.md').symlink_to(vault/'Design.md')
    (vault/'Project.md').write_text('![[diagram.png]] [[alias]]')
    note=next(u for u in read_units('obsidian',vault) if u.external_id=='Project.md')
    assert note.metadata['links'][0]['target']=='Assets/diagram.png'
    assert note.metadata['links'][0]['state']=='attachment'
    assert note.metadata['links'][1]['state']!='resolved'


def test_removed_link_is_removed_without_model(vault,tmp_path):
    app=App(tmp_path/'data');app.storage.initialize()
    app.memory.set('default','system','knowledge_config',{'embedding_provider':'none'})
    svc=LocalSourceService(app);source=svc.add('Vault','obsidian',str(vault),mode='direct')
    svc.ingest(source['id'])
    (vault/'Project.md').write_text('No link now.')
    svc.ingest(source['id'])
    assert not app.storage.fetchall('SELECT * FROM knowledge_relations WHERE created_by=?',('obsidian:'+source['id'],))


def test_analysis_keeps_original_links_between_note_overviews(vault,tmp_path):
    from types import SimpleNamespace
    app=App(tmp_path/'data');app.storage.initialize()
    app.memory.set('default','system','knowledge_config',{'embedding_provider':'none'})
    app._llm=SimpleNamespace(complete=lambda *a,**kw:SimpleNamespace(content='{"relations":[]}'))
    def summarize(unit, source):
        return json.dumps({'format':'source-articles-v1','articles':[{'title':unit.title,'content':'Summary '+unit.title,
            'evidence':[{'quote':unit.content[:10],'start':0,'end':10}]}]})
    svc=LocalSourceService(app,summarize=summarize)
    source=svc.add('Vault','obsidian',str(vault),mode='analyze',model='test')
    svc.ingest(source['id'])
    rows=app.storage.fetchall('SELECT r.*,n.path,n.type FROM knowledge_relations r JOIN knowledge_notes n ON n.id=r.from_id WHERE r.created_by=?',('obsidian:'+source['id'],))
    assert len(rows)==1
    assert rows[0]['type']=='topic'
    assert app.knowledge_base._indexer.article_relations(rows[0]['path'])


def test_source_results_include_direct_notes_and_current_metadata(vault, tmp_path):
    app = App(tmp_path/'data'); app.storage.initialize()
    app.memory.set('default', 'system', 'knowledge_config', {'embedding_provider': 'none'})
    svc = LocalSourceService(app)
    source = svc.add('Vault', 'obsidian', str(vault), mode='direct')
    svc.ingest(source['id'])
    results = svc.articles(source['id'])
    assert len(results) == 2
    note = next(row for row in results if row['title'] == 'Sharewise')
    assert note['relation_state'] == 'original'
    assert note['origins'][0]['source_metadata']['properties']['aliases'] == ['Portfolio']
    assert note['origins'][0]['source_metadata']['links'][1]['state'] == 'attachment'
    (vault/'Design.md').unlink()
    svc.ingest(source['id'])
    assert len(svc.articles(source['id'])) == 1
