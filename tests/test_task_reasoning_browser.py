"""Shared settings form with deterministic API responses."""
import json
from pathlib import Path
from playwright.sync_api import expect


def test_task_settings_can_be_saved_on_mobile(page):
    tasks = {key: {'reasoning':'default', 'fallbacks':[], 'max_output_tokens':None}
             for key in ('execution','source_analysis','source_relations','classification')}
    def route(req):
        url=req.request.url
        if '/task-settings/' in url:
            tasks[url.rsplit('/',1)[1]]=req.request.post_data_json
            data={}
        elif url.endswith('/task-settings'):
            data=tasks
        elif url.endswith('/settings'):
            data={'providers':[]}
        else:
            data={'models':[], 'agents':[], 'assignments':[]}
        req.fulfill(content_type='application/json',body=json.dumps(data))
    page.route('http://settings.test/api/**',route)
    page.route('http://settings.test/',lambda r:r.fulfill(content_type='text/html',body='<html lang="de"><meta name="viewport" content="width=device-width"><mycelos-models></mycelos-models></html>'))
    page.set_viewport_size({'width':390,'height':844})
    page.goto('http://settings.test/')
    page.add_script_tag(path=str(Path('src/mycelos/frontend/shared/model-settings.js').resolve()))
    page.get_by_role('button',name='Denkschritte für Verknüpfung ändern',exact=True).click()
    page.get_by_role('combobox',name='Denkschritte',exact=True).select_option('none')
    page.get_by_label('Ausgabelimit in Tokens · leer für Modellstandard').fill('8000')
    page.get_by_role('button',name='Speichern',exact=True).click()
    expect(page.get_by_text('Einstellungen für die Aufgabe gespeichert.',exact=True)).to_be_visible()
    assert tasks['source_relations']['reasoning']=='none'
    assert tasks['source_relations']['max_output_tokens']==8000
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path='/tmp/mycelos-reasoning-mobile.png',full_page=True)
