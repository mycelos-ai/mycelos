"""Native command boundary tests with artificial profile state."""
import copy
import json

import pytest
from playwright.sync_api import expect
from tests.test_collector_browser import collector_web


@pytest.fixture
def native(page, collector_web):
    url, server, project = collector_web
    source = dict(source_id='notes', name='Meine Notizen', kind='obsidian', root='/test/Notizen',
                  workflow={'include':['**/*.md'], 'exclude':[]}, last_synced=1789305500,
                  sync_requested=False)
    state = dict(server_url=url, onboarding_complete=True, daemon_running=True, paused=False,
                 fingerprint='AA:BB:CC', server_fingerprint='DD:EE:FF', public_key='public',
                 server_key='server-public', runtime={'status':'active','last_contact':1789305500}, sources=[source])
    data = {'state':state,'calls':[], 'history_error':False, 'history':[
        dict(id=3,at=1789305600,source_id='codex',source_name='Codex',file='sessions/test.jsonl',status='failed'),
        dict(id=2,at=1789305500,source_id='notes',source_name='Meine Notizen',file='<script>note.md',status='transferred'),
        dict(id=1,at=1789305400,source_id='notes',source_name='Meine Notizen',file='old.md',status='pending')]}
    def invoke(command, options=None):
        if command=='choose_source':return '/test/Weitere Notizen'
        action=options['action']; payload=options.get('payload') or {}
        data['calls'].append((action,copy.deepcopy(payload)))
        current=data['state']
        if action=='status':return copy.deepcopy(current)
        if action=='start':current['daemon_running']=True;return {'status':'starting'}
        if action=='pair':current['server_url']=url;current['runtime']['status']='pending';return copy.deepcopy(current)
        if action=='open-server':return {'status':'opened'}
        if action=='pause':current['paused']=True;return copy.deepcopy(current)
        if action=='resume':current['paused']=False;return copy.deepcopy(current)
        if action=='stop':current['paused']=True;current['daemon_running']=False;return {'status':'stopping'}
        if action=='sync':
            next(s for s in current['sources'] if s['source_id']==payload['source_id'])['sync_requested']=True
            return {'status':'queued'}
        if action=='history':
            if data['history_error']:raise RuntimeError('Protokoll nicht erreichbar')
            entries=[e for e in data['history'] if not payload.get('source_id') or e['source_id']==payload['source_id']]
            entries=[e for e in entries if not payload.get('before') or e['id']<payload['before']]
            return {'entries':copy.deepcopy(entries[:2]),'sources':[{'source_id':'notes','name':'Meine Notizen'},{'source_id':'codex','name':'Codex'}], 'next_before':entries[1]['id'] if len(entries)>2 else None}
        if action=='discover':return []
        if action=='add':
            added=dict(payload,source_id='new-source',last_synced=None,sync_requested=False)
            current['sources'].append(added);return copy.deepcopy(added)
        if action=='rules':
            target=next(s for s in current['sources'] if s['source_id']==payload['source_id'])
            target['workflow']=payload['workflow'];return copy.deepcopy(target)
        if action=='remove':current['sources']=[s for s in current['sources'] if s['source_id']!=payload['source_id']];return {'status':'removed'}
        if action=='disconnect':
            current.update(server_url=None,onboarding_complete=False,daemon_running=False,paused=True,sources=[])
            current['runtime']={};return {'status':'disconnected'}
        raise AssertionError(action)
    page.expose_function('nativeControl',invoke)
    page.add_init_script("window.__TAURI__={core:{invoke:(command,options)=>window.nativeControl(command,options)}}")
    data.update(url=url,project=project)
    return data


def test_existing_device_opens_sources_and_sync_is_local(page,native):
    page.goto(native['url']+'/desktop/')
    expect(page.get_by_role('heading',name='Deine Quellen')).to_be_visible()
    expect(page.get_by_label('Einladung von deinem Server')).to_be_hidden()
    expect(page.get_by_text('Verbunden',exact=True)).to_be_visible()
    page.get_by_role('button',name='Meine Notizen jetzt synchronisieren',exact=True).click()
    expect(page.get_by_text('Abgleich vorgemerkt',exact=True)).to_be_visible()
    assert ('sync',{'source_id':'notes'}) in native['calls']
    page.set_viewport_size({'width':480,'height':800})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(native['project']/'tmp/collector-sources-new.png'),full_page=True)


def test_first_start_waits_for_actual_server_confirmation(page,native):
    native['state'].update(server_url=None,onboarding_complete=False,daemon_running=False,sources=[])
    native['state']['runtime']={}
    page.goto(native['url']+'/desktop/')
    expect(page.get_by_role('heading',name='Verbinde deinen Server.')).to_be_visible()
    expect(page.get_by_role('tab',name='Quellen',exact=True)).to_be_hidden()
    page.get_by_label('Einladung von deinem Server').fill(json.dumps({'version':1,'url':native['url']}))
    page.get_by_role('button',name='Einladung prüfen',exact=True).click()
    expect(page.get_by_role('heading',name='Bestätige dein Gerät.')).to_be_visible()
    page.get_by_role('button',name='Bestätigung am Server öffnen').click()
    assert any(c[0]=='open-server' for c in native['calls'])
    expect(page.get_by_role('heading',name='Bestätige dein Gerät.')).to_be_visible()
    native['state']['onboarding_complete']=True
    native['state']['runtime']['status']='active'
    page.get_by_role('button',name='Status prüfen',exact=True).click()
    expect(page.get_by_role('heading',name='Deine Quellen')).to_be_visible()
    expect(page.get_by_role('button',name='Quelle hinzufügen',exact=True)).to_be_visible()


def test_history_is_newest_first_filtered_and_safe(page,native):
    page.goto(native['url']+'/desktop/')
    page.get_by_role('tab',name='Protokoll',exact=True).click()
    rows=page.locator('#history-list tr')
    expect(rows).to_have_count(2)
    expect(rows.first).to_contain_text('Codex')
    expect(rows.last).to_contain_text('<script>note.md')
    assert page.locator('#history-list script').count()==0
    page.get_by_role('button',name='Ältere Einträge laden').click()
    expect(rows).to_have_count(3)
    page.get_by_label('Quelle im Protokoll').select_option('notes')
    expect(rows).to_have_count(2)
    expect(rows.first).to_contain_text('Übertragen')
    expect(rows.last).to_contain_text('Ausstehend')
    expect(page.get_by_role('button',name='Ältere Einträge laden')).to_be_hidden()
    page.set_viewport_size({'width':480,'height':800})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(native['project']/'tmp/collector-history-new.png'),full_page=True)


def test_history_error_can_retry_without_losing_filter(page,native):
    native['history_error']=True
    page.goto(native['url']+'/desktop/')
    page.get_by_role('tab',name='Protokoll',exact=True).click()
    expect(page.locator('#history-error')).to_be_visible()
    native['history_error']=False
    page.get_by_role('button',name='Protokoll aktualisieren').click()
    expect(page.locator('#history-list tr')).to_have_count(2)


@pytest.mark.parametrize(('error','message'), [
    ('Delivery expired','Die Frist für die Übertragung ist abgelaufen.'),
    ('Source rules changed','Die Dateiauswahl hat sich geändert.'),
    ('Source removed','Die Freigabe der Quelle ist entfernt.'),
    ('Server disconnected','Die Verbindung zum Server ist getrennt.'),
    ('Transfer failed','Die Übertragung ist fehlgeschlagen.'),
    ('Unexpected error with private contents','Die Übertragung ist fehlgeschlagen.')])
def test_history_errors_use_safe_german_messages(page,native,error,message):
    native['history'][0]['error']=error
    page.goto(native['url']+'/desktop/')
    page.get_by_role('tab',name='Protokoll',exact=True).click()
    first=page.locator('#history-list tr').first
    expect(first).to_contain_text(message)
    expect(first).not_to_contain_text(error)


def test_history_filter_error_clears_previous_page_before_retry(page,native):
    native['history'][:0]=[
        dict(id=5,at=1789305800,source_id='codex',source_name='Codex',file='new-session.jsonl',status='transferred'),
        dict(id=4,at=1789305700,source_id='notes',source_name='Meine Notizen',file='new-note.md',status='transferred')]
    page.goto(native['url']+'/desktop/')
    page.get_by_role('tab',name='Protokoll',exact=True).click()
    rows=page.locator('#history-list tr')
    more=page.locator('#history-more')
    source=page.get_by_label('Quelle im Protokoll')
    expect(rows).to_have_count(2)
    expect(more).to_be_enabled()
    native['history_error']=True
    source.select_option('notes')
    expect(page.locator('#history-error')).to_be_visible()
    expect(source).to_have_value('notes')
    expect(rows).to_have_count(0)
    expect(more).to_be_disabled()
    native['history_error']=False
    page.get_by_role('button',name='Protokoll aktualisieren').click()
    expect(rows).to_have_count(2)
    expect(rows.first).to_contain_text('new-note.md')
    expect(rows.last).to_contain_text('<script>note.md')
    more.click()
    expect(rows).to_have_count(3)
    expect(rows.last).to_contain_text('old.md')
    assert all('Meine Notizen' in row for row in rows.all_text_contents())
    queries=[payload for action,payload in native['calls'] if action=='history' and payload.get('source_id')=='notes']
    assert queries==[{'limit':100,'source_id':'notes'}, {'limit':100,'source_id':'notes'},
                     {'limit':100,'source_id':'notes','before':2}]


def test_history_ignores_old_page_response_after_filter_change(page,native):
    page.goto(native['url']+'/desktop/')
    page.get_by_role('tab',name='Protokoll',exact=True).click()
    rows=page.locator('#history-list tr')
    expect(rows).to_have_count(2)
    page.evaluate("""() => {
      const invoke=window.__TAURI__.core.invoke;
      window.__TAURI__.core.invoke=async (command,options) => {
        const result=await invoke(command,options);
        if(options.action==='history'&&options.payload.before){
          return new Promise(resolve=>{window.releaseOldHistory=()=>resolve(result);});
        }
        return result;
      };
    }""")
    page.get_by_role('button',name='Ältere Einträge laden').click()
    page.wait_for_function('typeof window.releaseOldHistory==="function"')
    page.get_by_label('Quelle im Protokoll').select_option('notes')
    expect(rows).to_have_count(2)
    expect(rows.first).to_contain_text('<script>note.md')
    page.evaluate('async () => {window.releaseOldHistory(); await new Promise(resolve=>setTimeout(resolve,0));}')
    expect(rows).to_have_count(2)
    assert all('Meine Notizen' in row for row in rows.all_text_contents())
    expect(page.get_by_role('button',name='Ältere Einträge laden')).to_be_hidden()


def test_source_focus_survives_unchanged_status_poll_and_keyboard_opens_source(page,native):
    page.goto(native['url']+'/desktop/')
    edit=page.get_by_role('button',name='Meine Notizen bearbeiten',exact=True)
    edit.focus()
    original=edit.element_handle()
    status_calls=sum(action=='status' for action,_ in native['calls'])
    page.wait_for_timeout(2200)
    assert sum(action=='status' for action,_ in native['calls'])>status_calls
    expect(edit).to_be_focused()
    assert original.evaluate('element => element.isConnected')
    page.keyboard.press('Enter')
    expect(page.get_by_role('dialog',name='Meine Notizen',exact=True)).to_be_visible()


@pytest.mark.parametrize('change', ['updated','disabled','removed','empty'])
def test_source_focus_follows_control_after_status_change(page,native,change):
    native['state']['sources'].append(dict(native['state']['sources'][0],source_id='other',name='Andere Notizen'))
    page.goto(native['url']+'/desktop/')
    sync=page.get_by_role('button',name='Meine Notizen jetzt synchronisieren',exact=True)
    sync.focus()
    if change=='updated':
        native['state']['sources'][0]['last_synced']=1789305900
        target=sync
    elif change=='disabled':
        native['state']['sources'][0]['sync_requested']=True
        target=page.get_by_role('button',name='Meine Notizen bearbeiten',exact=True)
    elif change=='removed':
        native['state']['sources'].pop(0)
        target=page.get_by_role('button',name='Andere Notizen bearbeiten',exact=True)
    else:
        native['state']['sources']=[]
        target=page.get_by_role('button',name='Quelle hinzufügen',exact=True)
    status_calls=sum(action=='status' for action,_ in native['calls'])
    page.wait_for_timeout(2200)
    assert sum(action=='status' for action,_ in native['calls'])>status_calls
    expect(target).to_be_focused()
    page.keyboard.press('Enter')
    if change=='updated':
        expect(page.get_by_text('Abgleich vorgemerkt',exact=True)).to_be_visible()
    else:
        expect(page.get_by_role('dialog')).to_be_visible()


def test_disconnect_requires_settings_and_confirmation(page,native):
    page.goto(native['url']+'/desktop/')
    expect(page.get_by_role('button',name='Server trennen',exact=True)).to_be_hidden()
    page.get_by_role('button',name='Einstellungen',exact=True).click()
    page.get_by_role('button',name='Server trennen',exact=True).click()
    expect(page.get_by_role('dialog',name='Verbindung trennen?')).to_be_visible()
    assert not any(c[0]=='disconnect' for c in native['calls'])
    page.get_by_role('button',name='Verbindung behalten',exact=True).click()
    assert not any(c[0]=='disconnect' for c in native['calls'])
    page.get_by_role('button',name='Server trennen',exact=True).click()
    page.get_by_role('dialog').get_by_role('button',name='Server trennen',exact=True).click()
    expect(page.get_by_role('heading',name='Verbinde deinen Server.')).to_be_visible()
    assert any(c[0]=='disconnect' for c in native['calls'])
def test_sync_progress_and_incomplete_result_are_visible(page, native):
    source = native['state']['sources'][0]
    source.update(status='scanning', last_synced=None)
    page.goto(native['url']+'/desktop/')
    expect(page.get_by_text('Dateien werden geprüft.', exact=True)).to_be_visible()
    source['status'] = 'transferring'
    page.evaluate('refresh()')
    expect(page.get_by_text('Dateien werden übertragen.', exact=True)).to_be_visible()
    source.update(status='incomplete', error='Scan incomplete')
    page.evaluate('refresh()')
    expect(page.get_by_text('Abgleich unvollständig. Prüfe die Dateiauswahl und den Zugriff auf die Dateien.', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Meine Notizen jetzt synchronisieren', exact=True)).to_be_enabled()
    source.update(status='unchanged', error=None, last_synced=1789305500)
    page.evaluate('refresh()')
    expect(page.get_by_text('Keine neuen oder geänderten Dateien.', exact=True)).to_be_visible()
def test_received_files_do_not_hide_failed_knowledge_processing(page, native):
    native['state']['sources'][0].update(status='processing_failed', error='Source processing failed')
    page.goto(native['url']+'/desktop/')
    expect(page.get_by_text('Übertragen. Die Wissensaufbereitung am Server ist fehlgeschlagen.', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Meine Notizen jetzt synchronisieren', exact=True)).to_be_enabled()
