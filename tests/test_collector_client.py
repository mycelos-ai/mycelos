import json
import threading
import time

import httpx
import pytest

from mycelos.collector.crypto import decode
from mycelos.app import App


def _patch_client_sleep(monkeypatch, fake):
    """Replace time.sleep for this test's thread only.

    The collector client imports the time module, so patching its sleep
    patches it process-wide. Background threads left by other tests (for
    example the gateway restart watchdog) must keep sleeping for real and
    must not show up in the recorded delays.
    """
    real_sleep = time.sleep
    owner = threading.get_ident()

    def sleep(delay):
        if threading.get_ident() != owner:
            return real_sleep(delay)
        return fake(delay)

    monkeypatch.setattr('mycelos.collector.client.time.sleep', sleep)


def test_client_pins_invitation_and_uses_https_fallback(tmp_path):
    from mycelos.collector.client import CollectorClient
    from mycelos.collector.server import CollectorServer
    from mycelos.collector.crypto import encode
    app = App(tmp_path/'server'); app.storage.initialize()
    server = CollectorServer(app)
    def transport(request):
        body = json.loads(request.content)
        if request.url.path.endswith('/handshake'):
            sid, reply = server.accept_handshake(decode(body['message']))
            return httpx.Response(200,json={'session_id':sid,'message':encode(reply)})
        return httpx.Response(200,json={'frames':server.exchange(body['session_id'],body['frames'])})
    client = CollectorClient(tmp_path/'client', use_keyring=False)
    client.pair(server.invitation('default','https://example.test'))
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        client.connect_http(http)
        assert client.exchange('poll',{},http=http)['status'] == 'pending'
        dev = server.devices('default')[0]
        server.confirm('default',dev['id'],dev['fingerprint'],dev['attempt'])
        assert client.exchange('poll',{},http=http)['status'] == 'active'
    reloaded = CollectorClient(tmp_path/'client',use_keyring=False)
    assert reloaded.status()['server_fingerprint'] == client.status()['server_fingerprint']
    assert 'secret' not in (tmp_path/'client'/'connection.json').read_text()


def test_client_rejects_remote_http_and_key_change(tmp_path):
    from mycelos.collector.client import CollectorClient
    from mycelos.collector.crypto import Identity, encode
    client = CollectorClient(tmp_path,use_keyring=False)
    invitation = dict(version=1,url='http://example.test',server_key=encode(Identity.generate().public),secret='a'*43,expires=9999999999)
    with pytest.raises(ValueError):
        client.pair(invitation)
    invitation['url']='https://example.test'
    client.pair(invitation)
    invitation['server_key']=encode(Identity.generate().public)
    with pytest.raises(ValueError):
        client.pair(invitation)


def test_cli_grants_and_pause_without_server(tmp_path):
    from click.testing import CliRunner
    from mycelos.collector.cli import cli
    root=tmp_path/'docs';root.mkdir();(root/'hello.md').write_text('Hello')
    args=['--state-dir',str(tmp_path/'state'),'--file-key']
    runner=CliRunner()
    result=runner.invoke(cli,args+['add'],input=json.dumps({'name':'Docs','kind':'folder','root':str(root)}))
    assert result.exit_code==0,result.output
    assert json.loads(result.output)['name']=='Docs'
    assert runner.invoke(cli,args+['pause']).exit_code==0
    state=runner.invoke(cli,args+['status'])
    assert state.exit_code==0,state.output
    assert json.loads(state.output)['paused'] is True


@pytest.fixture
def connected(tmp_path):
    from mycelos.collector.client import CollectorClient
    from mycelos.collector.server import CollectorServer
    from mycelos.collector.crypto import encode
    app = App(tmp_path/'server'); app.storage.initialize()
    server = CollectorServer(app)
    def transport(request):
        body = json.loads(request.content)
        if request.url.path.endswith('/handshake'):
            sid, reply = server.accept_handshake(decode(body['message']))
            return httpx.Response(200, json={'session_id': sid, 'message': encode(reply)})
        return httpx.Response(200, json={'frames': server.exchange(body['session_id'], body['frames'])})
    client = CollectorClient(tmp_path/'client', use_keyring=False)
    client.pair(server.invitation('default', 'https://example.test'))
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        client.connect_http(http)
        device = server.devices('default')[0]
        server.confirm('default', device['id'], device['fingerprint'], device['attempt'])
        yield client, server, http, device
    client.close()


def add_documents(client, tmp_path, name):
    root = tmp_path/name
    root.mkdir()
    (root/'hello.md').write_text('A private test document.')
    return client.local.add_source(name, 'folder', root)


def test_missing_source_does_not_block_other_sources_or_original_jobs(connected, tmp_path):
    from mycelos.collector.intake import receive, process_pending, request_job, job_result
    client, server, http, device = connected
    bad = add_documents(client, tmp_path, 'bad')
    good = add_documents(client, tmp_path, 'good')
    original = client.local.scan(good['source_id'])
    receive(server, device, original['scan_id'], original)
    server.db.execute("UPDATE local_sources SET mode='direct',status='ready'")
    process_pending(server)
    client.exchange('poll', {}, http=http)
    job = request_job(server, 'default', device['id'], 'original', dict(
        source_id=good['source_id'], external_id='hello.md', fingerprint=original['units'][0]['fingerprint']))
    (tmp_path/'bad').rename(tmp_path/'disconnected')
    client.cycle(http)
    result = job_result(server, 'default', job['id'])
    assert result['status'] == 'complete'
    assert result['result']['content'] == 'A private test document.'
    assert client.channel is not None
    state = client.status()
    sources = {item['source_id']: item for item in state['sources']}
    assert sources[bad['source_id']]['status'] == 'unavailable'
    assert sources[good['source_id']]['status'] != 'unavailable'
    assert state['runtime']['status'] == 'active'
    assert str(tmp_path) not in json.dumps(state['runtime'])
    assert 'A private test document.' not in json.dumps(state['runtime'])
    # Restore the same granted root identity, as after a device reconnect.
    (tmp_path/'disconnected').rename(tmp_path/'bad')
    new_source = add_documents(client, tmp_path, 'another')
    client.cycle(http)
    assert server.db.fetchone('SELECT 1 FROM collector_sources WHERE source_id=?', (new_source['source_id'],))
    restored = {item['source_id']: item for item in client.status()['sources']}
    assert restored[bad['source_id']]['status'] != 'unavailable'
    assert restored[bad['source_id']]['error'] is None


def test_running_client_uses_same_key_replacement_invitation(connected, monkeypatch):
    from mycelos.collector.client import CollectorClient
    client, server, http, device = connected
    real_cycle = client.cycle
    states = []
    def cycle(_http):
        real_cycle(http)
        states.append(client.connection_status)
        if len(states) == 1:
            server.set_status('default', device['id'], 'revoked')
            control = CollectorClient(client.directory, use_keyring=False)
            control.pair(server.invitation('default', 'https://example.test'))
        else:
            (client.directory/'stop').touch()
    def no_websocket():
        raise OSError('WebSocket unavailable')
    real_connect = client.connect_http
    monkeypatch.setattr(client, 'cycle', cycle)
    monkeypatch.setattr(client, 'connect_websocket', no_websocket)
    monkeypatch.setattr(client, 'connect_http', lambda _http: real_connect(http))
    sleeps = []
    def sleep(_seconds):
        sleeps.append(1)
        if len(sleeps) > 30:
            (client.directory/'stop').touch()
    _patch_client_sleep(monkeypatch, sleep)
    client.run()
    assert states == ['active', 'pending']
    assert server._device(device['id'])['status'] == 'pending'
    assert 'invitation' not in json.loads(client.config_path.read_text())


@pytest.mark.parametrize('operation', ['pause', 'stop'])
def test_local_stop_after_scan_prevents_transmission(connected, tmp_path, monkeypatch, operation):
    client, server, http, _ = connected
    add_documents(client, tmp_path, 'docs')
    real_scan = client.local.scan
    def scan(source_id):
        body = real_scan(source_id)
        if operation == 'pause':
            client.local.pause(True)
        else:
            (client.directory/'stop').touch()
        return body
    monkeypatch.setattr(client.local, 'scan', scan)
    client.cycle(http)
    assert not server.db.fetchall('SELECT * FROM collector_receipts')
    assert client.local.status()['pending_scans'] == 1


def test_cancel_during_original_read_prevents_result_upload(connected, tmp_path, monkeypatch):
    from mycelos.collector.intake import receive, process_pending, request_job, spool_path
    client, server, http, device = connected
    source = add_documents(client, tmp_path, 'docs')
    body = client.local.scan(source['source_id'])
    receive(server, device, body['scan_id'], body)
    server.db.execute("UPDATE local_sources SET mode='direct',status='ready'")
    process_pending(server)
    client.exchange('poll', {}, http=http)
    job = request_job(server, 'default', device['id'], 'original', dict(
        source_id=source['source_id'], external_id='hello.md', fingerprint=body['units'][0]['fingerprint']))
    real_original = client.local.original
    def original(*args):
        result = real_original(*args)
        server.db.execute("UPDATE collector_jobs SET status='cancelled' WHERE id=?", (job['id'],))
        return result
    monkeypatch.setattr(client.local, 'original', original)
    client.cycle(http)
    assert client.channel is not None
    assert not spool_path(server, 'original:'+job['id']).exists()


def test_scan_job_ack_recovers_receipt_outside_poll_limit(connected, tmp_path, monkeypatch):
    import time
    from mycelos.collector.intake import receive, process_pending, request_job
    client, server, http, device = connected
    source = add_documents(client, tmp_path, 'docs')
    body = client.local.scan(source['source_id'])
    receive(server, device, body['scan_id'], body)
    server.db.execute("UPDATE local_sources SET mode='direct',status='ready'")
    process_pending(server)
    # More recent receipts hide the target from the bounded poll response.
    for number in range(101):
        server.db.execute('INSERT INTO collector_receipts VALUES(?,?,?,?,?,?,NULL)',
            (f'later{number}', device['id'], 'digest', 'processed', time.time()+number, time.time()+900))
    response = client.exchange('poll', {}, http=http)
    assert body['scan_id'] not in {receipt['id'] for receipt in response['receipts']}
    request_job(server, 'default', device['id'], 'scan', {'source_id': source['source_id']})
    # Pause after the job, before a routine check can hide missing job ack logic.
    real_exchange = client.exchange
    def exchange(type, *args, **kwargs):
        response = real_exchange(type, *args, **kwargs)
        if type == 'job_result':
            client.local.pause(True)
        return response
    monkeypatch.setattr(client, 'exchange', exchange)
    client.cycle(http)
    assert client.local.status()['pending_scans'] == 0


def test_replacement_invitation_during_handshake_is_not_overwritten(connected, monkeypatch):
    from mycelos.collector.client import CollectorClient
    client, server, http, device = connected
    server.set_status('default', device['id'], 'revoked')
    client.pair(server.invitation('default', 'https://example.test'))
    real_connected = client._connected
    invitations = []
    def connected_with_new_invitation(conn, response):
        server.set_status('default', device['id'], 'revoked')
        invitation = server.invitation('default', 'https://example.test')
        invitations.append(invitation)
        control = CollectorClient(client.directory, use_keyring=False)
        control.pair(invitation)
        real_connected(conn, response)
    monkeypatch.setattr(client, '_connected', connected_with_new_invitation)
    client.connect_http(http)
    assert json.loads(client.config_path.read_text())['invitation'] == invitations[0]['secret']
    monkeypatch.setattr(client, '_connected', real_connected)
    client.connect_http(http)
    assert client.exchange('poll', {}, http=http)['status'] == 'pending'


def test_running_profile_rejects_replacement_server_key(connected):
    from mycelos.collector.client import save_json
    from mycelos.collector.crypto import Identity, encode
    client, _, http, _ = connected
    approved_key = client.config['server_key']
    save_json(client.config_path, dict(client.config, server_key=encode(Identity.generate().public)))
    with pytest.raises(ValueError, match='identity changed'):
        client.connect_http(http)
    assert client.config['server_key'] == approved_key


def test_concurrent_pair_cannot_lose_invitation_at_handshake_commit(connected, monkeypatch):
    import threading
    import mycelos.collector.client as module
    client, server, _, device = connected
    server.set_status('default', device['id'], 'revoked')
    client.pair(server.invitation('default', 'https://example.test'))
    conn, first = client._hello()
    _, reply = server.accept_handshake(first)
    server.set_status('default', device['id'], 'revoked')
    invitation = server.invitation('default', 'https://example.test')
    control = module.CollectorClient(client.directory, use_keyring=False)
    at_commit = threading.Event()
    release = threading.Event()
    pair_started = threading.Event()
    pair_done = threading.Event()
    errors = []
    real_save = module.save_json
    def save(path, value):
        if threading.current_thread().name == 'handshake' and path == client.config_path:
            at_commit.set()
            assert release.wait(5)
        real_save(path, value)
    def finish_handshake():
        try:
            client._connected(conn, reply)
        except Exception as error:
            errors.append(error)
    def pair():
        pair_started.set()
        try:
            control.pair(invitation)
        except Exception as error:
            errors.append(error)
        finally:
            pair_done.set()
    monkeypatch.setattr(module, 'save_json', save)
    handshake = threading.Thread(target=finish_handshake, name='handshake')
    replacement = threading.Thread(target=pair)
    handshake.start()
    try:
        assert at_commit.wait(5)
        replacement.start()
        assert pair_started.wait(5)
        # An unlocked pair completes before the old handshake writes its state.
        pair_done.wait(0.2)
    finally:
        release.set()
        handshake.join(5)
        if replacement.ident is not None:
            replacement.join(5)
    assert not errors
    assert not handshake.is_alive() and not replacement.is_alive()
    assert json.loads(client.config_path.read_text())['invitation'] == invitation['secret']


def test_client_websocket_poll_reaches_active_device(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mycelos.collector.client import CollectorClient
    from mycelos.collector.server import CollectorServer
    from mycelos.gateway.routers.collectors import router

    app = App(tmp_path/'server'); app.storage.initialize()
    server = CollectorServer(app)
    api = FastAPI(); api.state.mycelos = app; api.state.collector_server = server
    api.include_router(router)
    client = CollectorClient(tmp_path/'client', use_keyring=False)
    client.pair(server.invitation('default', 'http://localhost:9100'))
    with TestClient(api).websocket_connect('/api/collector-transport/ws') as socket:
        class Transport:
            def send(self, message):
                if isinstance(message, bytes): socket.send_bytes(message)
                else: socket.send_text(message)
            def recv(self, timeout=None):
                message = socket.receive()
                return message.get('bytes', message.get('text'))
            def close(self):
                pass
        monkeypatch.setattr('websockets.sync.client.connect', lambda *a, **kw: Transport())
        client.connect_websocket()
        assert client.exchange('poll', {})['status'] == 'pending'
        device = server.devices('default')[0]
        server.confirm('default', device['id'], device['fingerprint'], device['attempt'])
        assert client.exchange('poll', {})['status'] == 'active'
        assert client.exchange('poll', {})['status'] == 'active'
