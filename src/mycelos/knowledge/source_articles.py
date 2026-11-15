"""Materialize evidence articles and source hierarchy without model calls."""
import hashlib
import json
from mycelos.knowledge.file_journal import file_changes
from mycelos.i18n import t as _t


def _write(kb, *args, **kwargs):
    return kb.write(*args, **kwargs)


def _update(kb, path, **kwargs):
    return kb.update(path, **kwargs)


def payload(summary):
    if not isinstance(summary,str) or not summary.startswith('{'):
        return None
    try:
        value=json.loads(summary)
    except ValueError:
        return None
    return value if isinstance(value,dict) and value.get('format')=='source-articles-v1' else None


def overview(summary):
    data=payload(summary)
    if not data:
        return summary
    return "## " + _t("sources.articles_heading") + "\n\n"+'\n'.join('- '+a['title'] for a in data['articles'])


def source_root(app, source):
    db=app.storage
    row=db.fetchone('SELECT n.* FROM source_article_roots r JOIN knowledge_notes n ON n.id=r.note_id WHERE r.source_id=?',(source['id'],))
    if row:
        return row
    # An attached source (slice 5) keeps its root inside the attached folder.
    attached=db.fetchone("SELECT topic_path FROM source_attachments WHERE source_id=? ORDER BY id LIMIT 1",
                         ('local:'+source['id'],))
    topic=(attached or {}).get('topic_path') or None
    path=_write(app.knowledge_base,source['name'],_t("sources.root_content"),type='topic',topic=topic,created_by='source-import',source={'source_id':source['id'],'kind':source['kind']})
    row=db.fetchone('SELECT * FROM knowledge_notes WHERE path=?',(path,))
    db.execute('INSERT INTO source_article_roots(source_id,note_id) VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET note_id=excluded.note_id',(source['id'],row['id']))
    db.execute("UPDATE knowledge_notes SET organizer_state='done' WHERE id=?",(row['id'],))
    return row


def write_overview(app,unit,summary,source,old):
    db=app.storage; kb=app.knowledge_base
    root=source_root(app,source)
    if old and old['note_id']:
        shared=db.fetchone('SELECT count(*) n FROM local_source_items i JOIN local_source_versions v ON v.id=i.version_id WHERE v.note_id=? AND i.id!=?',(old['note_id'],old['id']))['n']
        note=db.fetchone('SELECT * FROM knowledge_notes WHERE id=?',(old['note_id'],))
        if not shared and note and note['type']=='topic' and note['status']=='active':
            _update(kb,note['path'],content=overview(summary))
            db.execute('UPDATE local_source_versions SET note_id=NULL WHERE note_id=?',(note['id'],))
            return note['id'],True
    path=_write(kb,unit.title,overview(summary),type='topic',topic=root['path'],created_by='source-import',
                  source={'source_id':source['id'],'kind':source['kind'],'external_id':unit.external_id})
    row=db.fetchone('SELECT id FROM knowledge_notes WHERE path=?',(path,))
    db.execute("UPDATE knowledge_notes SET organizer_state='done' WHERE id=?",(row['id'],))
    return row['id'],False


def materialize(app,source,version_id,old=None):
    db=app.storage; kb=app.knowledge_base
    version=db.fetchone('SELECT * FROM local_source_versions WHERE id=?',(version_id,))
    data=payload(version['summary']) if source['mode']=='analyze' else None
    if not data:
        retire_previous(app, old)
        return 0
    parent=db.fetchone('SELECT * FROM knowledge_notes WHERE id=?',(version['note_id'],))
    if not parent:
        return 0
    root=source_root(app,source)
    if parent['parent_path']!=root['path']:
        db.execute("INSERT OR IGNORE INTO knowledge_relations(from_id,to_id,kind,reason,created_by) VALUES(?,?,'source_member','Diese Quelle enthält dieselben Inhalte.','source-import')",(root['id'],parent['id']))
    created=0; used=set()
    for article in data['articles']:
        key=hashlib.sha256(article['title'].casefold().encode()).hexdigest()
        if key in used:
            key=hashlib.sha256((key+article['content']+json.dumps(article['evidence'])).encode()).hexdigest()
        used.add(key)
        existing=db.fetchone('SELECT * FROM source_articles WHERE version_id=? AND article_key=?',(version_id,key))
        if existing and existing['relation_state'] != 'superseded':
            # A NULL note_id without superseded means an explicit deletion.
            if existing['note_id'] and existing['auto_archived']:
                restored=db.fetchone('SELECT path FROM knowledge_notes WHERE id=?',(existing['note_id'],))
                if restored:
                    _update(kb,restored['path'],status='active')
                    db.execute("UPDATE source_articles SET auto_archived=0,relation_state='pending' WHERE note_id=?",(existing['note_id'],))
                    db.execute("UPDATE knowledge_notes SET organizer_state='done' WHERE id=?",(existing['note_id'],))
            continue
        if existing:
            db.execute('DELETE FROM source_articles WHERE version_id=? AND article_key=?',(version_id,key))
        evidence=article['evidence']
        body=article['content']+'\n\n## Quellenbelege\n\n'+'\n\n'.join(
            '> '+e['quote'].replace('\n','\n> ')+f"\n\nQuelltext: Zeichen {e['start']}–{e['end']}" for e in evidence)
        reused=None
        if old:
            previous=db.fetchone('SELECT a.*,n.path,n.content_hash,n.status FROM source_articles a JOIN knowledge_notes n ON n.id=a.note_id WHERE a.version_id=? AND a.article_key=?',(old['version_id'],key))
            if previous and previous['status']=='active' and previous['generated_hash']==previous['content_hash']:
                shared=db.fetchone('SELECT count(*) n FROM local_source_items i JOIN source_articles a ON a.version_id=i.version_id WHERE a.note_id=? AND i.id!=?',(previous['note_id'],old['id']))['n']
                if not shared and _update(kb,previous['path'],content=body):
                    reused=previous['note_id']
                    db.execute("UPDATE source_articles SET note_id=NULL,relation_state='superseded' WHERE note_id=?",(reused,))
                    db.execute("UPDATE source_articles SET relation_state='pending' WHERE note_id IN (SELECT from_id FROM knowledge_relations WHERE to_id=? AND created_by='source-analysis')",(reused,))
        if reused is None:
            path=_write(kb,article['title'],body,topic=parent['path'],created_by='source-import',
                          source={'source_id':source['id'],'kind':source['kind'],'version_id':version_id,'article_key':key})
            reused=db.fetchone('SELECT id FROM knowledge_notes WHERE path=?',(path,))['id']
            created+=1
        note=db.fetchone('SELECT content_hash FROM knowledge_notes WHERE id=?',(reused,))
        db.execute('INSERT INTO source_articles(version_id,article_key,note_id,evidence,generated_hash) VALUES(?,?,?,?,?)',
                   (version_id,key,reused,json.dumps(evidence,ensure_ascii=False),note['content_hash']))
        db.execute("UPDATE knowledge_notes SET organizer_state='done' WHERE id=?",(reused,))
    retire_previous(app, old)
    return created


def link_pending(app,version_id,model):
    from mycelos.knowledge.source_relations import relate_article
    count=0
    rows=app.storage.fetchall("SELECT a.note_id FROM source_articles a JOIN knowledge_notes n ON n.id=a.note_id WHERE a.version_id=? AND a.relation_state='pending' AND n.status='active'",(version_id,))
    for row in rows:
        count+=relate_article(app,row['note_id'],model)
        app.storage.execute("UPDATE source_articles SET relation_state='done' WHERE version_id=? AND note_id=?",(version_id,row['note_id']))
    return count


def retire_previous(app, old):
    db=app.storage; kb=app.knowledge_base
    if old:
        for previous in db.fetchall('SELECT n.*,a.generated_hash FROM source_articles a JOIN knowledge_notes n ON n.id=a.note_id WHERE a.version_id=?',(old['version_id'],)):
            live=db.fetchone('SELECT 1 FROM local_source_items i JOIN source_articles a ON a.version_id=i.version_id WHERE a.note_id=? LIMIT 1',(previous['id'],))
            if not live and previous['status']=='active' and previous['content_hash']==previous['generated_hash']:
                _update(kb,previous['path'],status='archived')
                db.execute('UPDATE source_articles SET auto_archived=1 WHERE note_id=?',(previous['id'],))
