"""Source checks stay separate from model and import actions in the browser."""
from tests.test_source_card_browser import card_server  # noqa: F401


def prepare(page, url, status=None, pending=None):
    source = {"id": "demo", "name": "Projekt", "kind": "folder", "root": "/demo", "status": "ready", "mode": "direct"}
    state = {"interval_minutes": 0, "status": "idle", "last_check": None, "next_check": None,
             "error": None, "failure_count": 0, "pending_count": 0, "removed_count": 0, "checked_units": 0,
             **(status or {})}
    requests = []

    def api(route):
        path = route.request.url.split('/api/local-sources')[-1]
        method = route.request.method
        requests.append((method, path, route.request.post_data_json if method in ['POST', 'PUT'] and route.request.post_data else None))
        if path == '':
            route.fulfill(json=[source])
        elif path == '/demo':
            route.fulfill(json=source)
        elif path == '/demo/check-status':
            route.fulfill(json=state)
        elif path == '/demo/pending':
            route.fulfill(json=pending or [])
        elif path == '/demo/check':
            state['status'] = 'queued'
            route.fulfill(json=state)
        elif path == '/demo/check-schedule':
            state['interval_minutes'] = route.request.post_data_json['interval_minutes']
            route.fulfill(json=state)
        else:
            route.fulfill(status=500, json={"detail": "Unexpected action"})

    page.route('**/api/local-sources**', api)
    page.goto(url)
    card = page.locator('#settings')
    card.get_by_role('button', name='Projekt', exact=False).click()
    return card, state, requests


def test_check_polls_ready_source_without_import_or_probe(page, card_server):
    from playwright.sync_api import expect
    card, state, requests = prepare(page, card_server[0])
    card.get_by_role('button', name='Jetzt prüfen', exact=True).click()
    expect(card.get_by_text('Prüfung vorgemerkt', exact=True)).to_be_visible()
    expect(card.get_by_role('button', name='Jetzt prüfen', exact=True)).to_be_disabled()
    expect(card.get_by_role('button', name='Import starten', exact=True)).to_be_disabled()
    state.update(status='idle', checked_units=3, last_check='2026-09-06T09:00:00Z')
    expect(card.get_by_text('Prüfung bereit', exact=True)).to_be_visible(timeout=8000)
    expect(card.get_by_text('0 Inhalte vorgemerkt · 0 entfernt · 3 geprüft', exact=True)).to_be_visible()
    assert len([r for r in requests if r[1] == '/demo/check-status']) >= 2
    assert [r[1] for r in requests if r[0] == 'POST'] == ['/demo/check']


def test_check_schedule_explicit_save_and_error_pending_mobile(page, card_server):
    from playwright.sync_api import expect
    page.set_viewport_size({'width': 390, 'height': 844})
    items = [{'external_id': 'readme', 'title': '<script>README</script>', 'change': 'changed', 'files': ['/demo/' + 'x' * 180], 'project': 'Projekt'},
             {'external_id': 'old', 'title': 'Alte Datei', 'change': 'removed', 'files': []}]
    card, state, requests = prepare(page, card_server[0], {'status': 'error', 'error': 'Pfad fehlt', 'failure_count': 3, 'pending_count': 250, 'removed_count': 1}, items)
    expect(card.get_by_text('Pfad fehlt · Fehler in Folge: 3.', exact=False)).to_be_visible()
    card.get_by_text('Zeitplan', exact=True).click()
    card.get_by_label('Prüfabstand').select_option('60')
    assert not [r for r in requests if r[0] == 'PUT']
    card.get_by_role('button', name='Zeitplan speichern', exact=True).click()
    expect(card.get_by_text('Zeitplan gespeichert.', exact=True)).to_be_visible()
    assert [r for r in requests if r[0] == 'PUT'] == [('PUT', '/demo/check-schedule', {'interval_minutes': 60})]
    expect(card.get_by_label('Prüfabstand')).to_have_value('60')
    card.get_by_text('Vorgemerkte Inhalte ansehen', exact=True).click()
    expect(card.get_by_text('<script>README</script>', exact=True)).to_be_visible()
    expect(card.get_by_text('Entfernt', exact=True)).to_be_visible()
    expect(card.get_by_text('Die Liste zeigt höchstens 200 Inhalte.', exact=False)).to_be_visible()
    assert card.locator('.pending script').count() == 0
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path='/tmp/source-check-mobile.png', full_page=True)
    assert not [r for r in requests if r[0] == 'POST']
