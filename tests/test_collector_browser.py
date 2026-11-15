"""Browser flows use a real gateway and artificial source data only."""
import json
from pathlib import Path
import socket
import threading
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from tests.browser_i18n import add_i18n_route
import uvicorn

from mycelos.app import App
from mycelos.collector.client import CollectorClient
from mycelos.collector.server import CollectorServer
from mycelos.collector.intake import process_pending
from mycelos.gateway.routers.collectors import router


@pytest.fixture
def collector_web(tmp_path):
    app=App(tmp_path/'server');app.storage.initialize()
    app.memory.set('default','system','knowledge_config',json.dumps({'embedding_provider':'none'}))
    api=FastAPI();api.state.mycelos=app
    server=CollectorServer(app);api.state.collector_server=server;api.include_router(router)
    project=Path(__file__).resolve().parents[1]
    add_i18n_route(api,'de')
    api.mount('/desktop',StaticFiles(directory=project/'collector-desktop/ui',html=True))
    api.mount('/',StaticFiles(directory=project/'src/mycelos/frontend',html=True))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    gateway=uvicorn.Server(uvicorn.Config(api,log_level='error'))
    thread=threading.Thread(target=gateway.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    for _ in range(100):
        if gateway.started:break
        time.sleep(.02)
    assert gateway.started
    yield f'http://127.0.0.1:{port}',server,project
    gateway.should_exit=True;thread.join(timeout=5);sock.close()


def test_browser_confirms_device_configures_source_and_opens_original(page,collector_web,tmp_path):
    from playwright.sync_api import expect
    url,server,project=collector_web
    page.goto(url+'/pages/collectors.html')
    page.get_by_role('button',name='Einladung erstellen').click()
    expect(page.locator('#invitation')).not_to_have_value('')
    client=CollectorClient(tmp_path/'client',use_keyring=False)
    client.pair(json.loads(page.locator('#invitation').input_value()))
    root=tmp_path/'notes';root.mkdir();(root/'note.md').write_text('A source document with <script>unsafe()</script>.')
    client.local.add_source('My notes','folder',str(root))
    with httpx.Client(timeout=5) as http:
        client.connect_http(http)
        page.get_by_role('button',name='Aktualisieren',exact=True).click()
        expect(page.get_by_text(client.status()['fingerprint'],exact=True)).to_be_visible()
        page.get_by_role('button',name='Der Fingerabdruck stimmt überein').click()
        expect(page.get_by_role('button',name='Pausieren',exact=True)).to_be_visible()
        client.cycle(http)
        page.get_by_role('button',name='Aktualisieren',exact=True).click()
        expect(page.get_by_role('heading',name='My notes')).to_be_visible()
        page.get_by_role('button',name='Textprobe für das Quelleninterview').click()
        expect(page.get_by_text('A source document with <script>unsafe()</script>.',exact=True)).to_be_visible()
        page.get_by_label('Verarbeitung',exact=True).select_option('direct')
        page.get_by_role('button',name='Verarbeitung freigeben').click()
        expect(page.get_by_text('folder · Verarbeitung freigegeben')).to_be_visible()
        process_pending(server)
        client.cycle(http)
        page.get_by_text('Quelleneinträge und Originale',exact=True).click()
        page.get_by_role('button',name='Einträge anzeigen').click()
        page.get_by_role('button',name='Original abrufen').click()
        client.cycle(http)
        expect(page.locator('#original-content')).to_contain_text('<script>unsafe()</script>',timeout=6000)
        assert page.locator('#original-content script').count()==0
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(project/'tmp/collector-server-mobile.png'),full_page=True)
        page.get_by_role('button',name='Inhalt schließen').click()
        expect(page.locator('#original-panel')).to_be_hidden()
        page.get_by_role('button',name='Verbindung widerrufen').click()
        expect(page.get_by_text('Verbindung widerrufen',exact=True)).to_be_visible()


def test_desktop_form_grants_only_after_explicit_choice(page,collector_web,tmp_path):
    from playwright.sync_api import expect
    url,server,project=collector_web
    client=CollectorClient(tmp_path/'client',use_keyring=False)
    root=tmp_path/'notes';root.mkdir();(root/'note.md').write_text('Local note')
    client.pair(server.invitation('default',url))
    with httpx.Client(timeout=5) as http:
        client.connect_http(http)
        device=server.devices('default')[0]
        server.confirm('default',device['id'],device['fingerprint'],device['attempt'])
        client.cycle(http)
    def control(command,options):
        if command=='choose_source':return str(root)
        action=options['action']
        if action=='status':return dict(client.status(),daemon_running=True)
        if action=='add':return client.local.add_source(**options['payload'])
        if action=='pause':client.local.pause(True);return client.status()
        if action=='rules':return client.local.update_rules(**options['payload'])
        if action=='remove':client.local.remove_source(options['payload']['source_id']);return {'status':'removed'}
        if action=='discover':return client.local.discover(home=tmp_path)
        raise AssertionError(action)
    page.expose_function('localControl',control)
    page.add_init_script("window.__TAURI__={core:{invoke:(command,options)=>window.localControl(command,options)}}")
    page.goto(url+'/desktop/')
    page.get_by_role('button',name='Quelle hinzufügen',exact=True).click()
    page.get_by_role('button',name='Auswählen',exact=True).click()
    expect(page.get_by_label('Ordner oder Datei auf diesem Gerät')).to_have_value(str(root))
    page.get_by_label('Name der Quelle').fill('Local notes')
    assert not client.local.list_sources()
    page.get_by_label('Ich gebe diese Quelle frei.',exact=False).check()
    page.get_by_role('button',name='Quelle freigeben',exact=True).click()
    expect(page.get_by_role('button',name='Local notes bearbeiten',exact=True)).to_be_visible()
    assert len(client.local.list_sources())==1
    page.get_by_role('button',name='Einstellungen',exact=True).click()
    page.get_by_role('button',name='Pausieren',exact=True).click()
    expect(page.get_by_text('Sammlung pausiert',exact=True)).to_be_visible()
    page.get_by_role('button',name='Quellen',exact=True).click()
    page.screenshot(path=str(project/'tmp/collector-desktop.png'),full_page=True)
    page.get_by_role('button',name='Local notes bearbeiten',exact=True).click()
    page.get_by_label('Dateimuster',exact=True).fill('**/*.txt')
    page.wait_for_timeout(5200)
    expect(page.get_by_label('Dateimuster',exact=True)).to_have_value('**/*.txt')
    page.get_by_role('button',name='Regeln auf diesem Gerät ändern').click()
    expect(page.get_by_role('button',name='Local notes bearbeiten',exact=True)).to_be_visible()
    assert client.local.list_sources()[0]['rule_version']==2
    page.get_by_role('button',name='Local notes bearbeiten',exact=True).click()
    page.get_by_role('button',name='Freigabe entziehen').click()
    expect(page.get_by_role('heading',name='Füge deine erste Quelle hinzu.')).to_be_visible()
    assert (root/'note.md').read_text()=='Local note'


@pytest.mark.parametrize('model_state', ['available', 'empty', 'error', 'missing_saved'])
def test_collector_model_selection_uses_configured_models(page,collector_web,tmp_path,model_state):
    from playwright.sync_api import expect
    url,server,project=collector_web
    client=CollectorClient(tmp_path/'client',use_keyring=False)
    client.pair(server.invitation('default',url))
    root=tmp_path/'notes';root.mkdir();(root/'note.md').write_text('A test note')
    source=client.local.add_source('Testquelle','folder',root)
    with httpx.Client(timeout=5) as http:
        client.connect_http(http)
        device=server.devices('default')[0]
        server.confirm('default',device['id'],device['fingerprint'],device['attempt'])
        client.cycle(http)
    if model_state=='missing_saved':
        server.db.execute("UPDATE local_sources SET model='custom/old'")
    models=[{'id':'custom/cheap','provider':'custom'}, {'id':'custom/fast','provider':'custom'},
            {'id':'custom/pro','provider':'custom'}, {'id':'custom/disabled','provider':'custom','enabled':False},
            {'id':'custom/image-model','provider':'custom'}, {'id':'other/model','provider':'other'}]
    page.route('**/api/models',lambda route:route.fulfill(status=503 if model_state=='error' else 200,
        json={'models':models if model_state!='empty' else []}))
    page.route('**/api/models/settings',lambda route:route.fulfill(json={'providers':[
        {'id':'custom','name':'Eigener Anbieter','configured':True},
        {'id':'other','name':'Anderer Anbieter','configured':False}]}))
    page.goto(url+'/pages/collectors.html')
    model=page.get_by_role('combobox',name='Modell',exact=True)
    expect(model).to_be_visible()
    save=page.get_by_role('button',name='Verarbeitung freigeben',exact=True)
    expect(save).to_be_disabled()
    page.get_by_label('Wofür dient die Quelle?').fill('Entscheidungen erhalten')
    if model_state in {'available','missing_saved'}:
        expect(model.locator('option:not([disabled])')).to_have_text([
            'Modell wählen','cheap · Eigener Anbieter','fast · Eigener Anbieter','pro · Eigener Anbieter'])
        if model_state=='missing_saved':
            expect(page.get_by_text('Das gespeicherte Modell ist nicht verfügbar. Wähle ein anderes Modell.')).to_be_visible()
        model.select_option('custom/cheap')
        if model_state=='missing_saved':
            expect(page.get_by_text('Das gespeicherte Modell ist nicht verfügbar. Wähle ein anderes Modell.')).to_be_hidden()
        expect(save).to_be_enabled()
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(project/'tmp/collector-model-selection.png'),full_page=True)
        save.click()
        expect(page.get_by_text('folder · Verarbeitung freigegeben')).to_be_visible()
        saved=server.db.fetchone('SELECT model,purpose FROM local_sources WHERE id=?',(source['source_id'],))
        assert saved['model']=='custom/cheap'
        assert saved['purpose']=='Entscheidungen erhalten'
    else:
        expect(model).to_be_disabled()
        if model_state=='empty':
            expect(page.get_by_text('Kein Textmodell verfügbar. Richte ein Modell ein oder speichere den Text direkt.')).to_be_visible()
            expect(page.get_by_role('link',name='Modelle einrichten')).to_have_attribute('href','/pages/settings.html#models')
        else:
            expect(page.get_by_text('Die Modellliste ist nicht erreichbar. Lade sie erneut oder speichere den Text direkt.')).to_be_visible()
            page.route('**/api/models',lambda route:route.fulfill(json={'models':models}))
            page.get_by_role('button',name='Modelle erneut laden').click()
            expect(model).to_be_enabled()
            expect(page.get_by_label('Wofür dient die Quelle?')).to_have_value('Entscheidungen erhalten')
        page.get_by_label('Verarbeitung',exact=True).select_option('direct')
        expect(model).to_be_hidden()
        expect(save).to_be_enabled()
        save.click()
        expect(page.get_by_text('folder · Verarbeitung freigegeben')).to_be_visible()
        assert server.db.fetchone('SELECT model FROM local_sources WHERE id=?',(source['source_id'],))['model'] is None
