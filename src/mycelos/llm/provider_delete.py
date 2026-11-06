"""Preview and atomically remove a provider with explicit model replacements."""
from contextlib import ExitStack
import hashlib
import json
import re

from mycelos.llm.providers import PROVIDERS
from mycelos.llm.eu_mode import get_eu_mode
from mycelos.llm.eu_enforcement import is_eu_model
from mycelos.knowledge.source_lock import source_lock


def preview(app, provider):
    if provider not in PROVIDERS:
        raise ValueError('Unbekannter Anbieter.')
    db = app.storage
    models = db.fetchall('SELECT * FROM llm_models ORDER BY id')
    removed = {m['id']: m for m in models if m['provider'] == provider}
    configured = {r['service'] for r in db.fetchall("SELECT service FROM credentials WHERE user_id='default'")}
    if app.memory.get('default', 'system', 'ollama_url'):
        configured.add('ollama')
    eu = get_eu_mode(app, 'default')
    order = list(PROVIDERS)
    candidates = [m for m in models if m['provider'] != provider and m['provider'] in configured
                  and m['status'] == 'available'
                  and not re.search(r'embed|tts|transcrib|image|realtime|audio|sora|veo|imagen|container', m['id'], re.I)
                  and (not eu or is_eu_model(m['id']))]
    candidates.sort(key=lambda m: (order.index(m['provider']) if m['provider'] in order else 999, m['id']))
    usable = {m['id'] for m in candidates}
    def affected(mid):
        return mid in removed or str(mid or '').startswith(provider + '/')
    def replacement(mid):
        tier = removed.get(mid, {}).get('tier')
        same = [m for m in candidates if m['tier'] == tier]
        return (same or candidates)[0]['id'] if candidates else None

    changes, blockers = [], []
    from mycelos.llm.task_settings import TASKS, read
    for purpose, label in TASKS.items():
        before = read(db, purpose)['fallbacks']
        if any(affected(mid) for mid in before):
            after = [mid for mid in before if not affected(mid)]
            changes.append(dict(kind='task_settings', id=purpose, label='Ersatzmodelle / '+label,
                                before=before, after=after))
    chains = {}
    for row in db.fetchall('SELECT * FROM agent_llm_models ORDER BY priority, model_id'):
        chains.setdefault((row['agent_id'], row['purpose']), []).append(row['model_id'])
    for (agent, purpose), before in chains.items():
        if not any(affected(mid) for mid in before):
            continue
        after = [mid for mid in before if not affected(mid)]
        if not any(mid in usable for mid in after):
            new = replacement(next(mid for mid in before if affected(mid)))
            if new:
                after.insert(0, new)
            else:
                blockers.append('Kein Ersatzmodell für ' + (agent or 'System') + ' / ' + purpose + '.')
        changes.append(dict(kind='chain', id=agent, purpose=purpose, label=(agent or 'System')+' / '+purpose, before=before, after=after))
    for table, label in [('local_sources','Quelle'), ('workflows','Workflow'), ('agents','Agent')]:
        for row in db.fetchall(f'SELECT * FROM {table} ORDER BY id'):
            if not affected(row.get('model')):
                continue
            new = replacement(row['model'])
            if not new:
                blockers.append('Kein Ersatzmodell für '+label+' '+row['name']+'.')
            if table == 'local_sources' and row['status'] in ('running','queued'):
                blockers.append('Die Quelle '+row['name']+' läuft noch.')
            changes.append(dict(kind=table, id=row['id'], label=label+' / '+row['name'], before=[row['model']], after=[new] if new else []))
    # Running workflows retain their model in memory, including tier-based selections.
    if changes and db.fetchone("SELECT id FROM workflow_runs WHERE status IN ('running','queued') LIMIT 1"):
        blockers.append('Ein Workflow läuft noch. Bitte nach dem Abschluss erneut versuchen.')
    result = dict(provider=provider, name=PROVIDERS[provider].name, models=sorted(removed), changes=changes, blockers=blockers)
    # Include eligible models and configured providers so a changed selection invalidates consent.
    fingerprint = dict(result=result, candidates=candidates, configured=sorted(configured), eu=eu)
    result['token'] = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    return result


def delete(app, provider, token):
    with ExitStack() as stack:
        initial = preview(app, provider)
        for change in initial['changes']:
            if change['kind'] == 'local_sources':
                stack.enter_context(source_lock(app.data_dir, change['id']))
        with app.storage.transaction():
            plan = preview(app, provider)
            if not token or token != plan['token']:
                raise ValueError('Die Auswahl hat sich geändert. Bitte die Vorschau erneut öffnen.')
            if plan['blockers']:
                raise ValueError(' '.join(plan['blockers']))
            for change in plan['changes']:
                if change['kind'] == 'task_settings':
                    from mycelos.llm.task_settings import read, save
                    value = read(app.storage, change['id'])
                    save(app, change['id'], {**value, 'fallbacks': change['after']})
                elif change['kind'] == 'chain':
                    app.storage.execute('DELETE FROM agent_llm_models WHERE agent_id IS ? AND purpose=?', (change['id'],change['purpose']))
                    for priority, mid in enumerate(change['after'], 1):
                        app.storage.execute('INSERT INTO agent_llm_models(agent_id,model_id,priority,purpose) VALUES(?,?,?,?)', (change['id'],mid,priority,change['purpose']))
                else:
                    app.storage.execute(f"UPDATE {change['kind']} SET model=? WHERE id=?", (change['after'][0],change['id']))
            app.storage.execute('DELETE FROM llm_models WHERE provider=?', (provider,))
            # Delete encrypted rows in the same transaction. No secret is decrypted or
            # sent to the proxy; a remote credential call would use a second connection.
            app.storage.execute("DELETE FROM credentials WHERE user_id='default' AND service=?", (provider,))
            app.storage.execute("DELETE FROM memory_entries WHERE user_id='default' AND scope='system' AND key=?", (provider+'_url',))
            app.audit.log('provider.deleted', details={'provider':provider,'changes':plan['changes']})
    app.config_notifier.notify_change('Provider removed: '+provider, 'provider_delete')
    return {'ok':True, 'changes':plan['changes']}
