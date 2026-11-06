import json
import time

import pytest
from mycelos.app import App
from mycelos.collector.crypto import Identity, Channel, initiate, decode, fingerprint


@pytest.fixture
def server(tmp_path):
    from mycelos.collector.server import CollectorServer
    app = App(tmp_path / 'server')
    app.storage.initialize()
    return CollectorServer(app)


def connect(server, identity=None, invitation=None):
    identity = identity or Identity.generate()
    invitation = invitation or server.invitation('default', 'https://example.test')
    conn, first = initiate(identity, decode(invitation['server_key']),
        {'version': 1, 'name': 'Laptop', 'invitation': invitation['secret']})
    sid, reply = server.accept_handshake(first)
    conn.read_message(reply)
    return identity, sid, Channel(conn)


def exchange(server, sid, channel, type='poll', payload=None, id='job-1'):
    return channel.open(server.exchange(sid, channel.seal(dict(version=1,type=type,id=id,payload=payload or {}))))


def confirm(server):
    device = server.devices('default')[0]
    server.confirm('default', device['id'], device['fingerprint'], device['attempt'])
    return device


def test_invitation_requires_exact_confirmation(server):
    identity, sid, channel = connect(server)
    result = exchange(server, sid, channel)
    assert result['status'] == 'pending'
    device = server.devices('default')[0]
    assert device['fingerprint'] == fingerprint(identity.public)
    with pytest.raises(ValueError):
        server.confirm('default', device['id'], 'wrong', device['attempt'])
    assert exchange(server, sid, channel)['status'] == 'pending'
    confirm(server)
    assert exchange(server, sid, channel)['status'] == 'active'


def test_no_data_before_confirmation_and_revocation_closes_active_channel(server):
    _, sid, channel = connect(server)
    with pytest.raises(ValueError):
        exchange(server, sid, channel, 'scan', {'source_id':'private'})
    device = confirm(server)
    server.set_status('default', device['id'], 'revoked')
    with pytest.raises(ValueError):
        exchange(server, sid, channel)


def test_invitation_expiry_replay_and_cross_user_confirmation(server):
    invite = server.invitation('default', 'https://example.test')
    connect(server, invitation=invite)
    with pytest.raises(ValueError):
        connect(server, invitation=invite)
    device = server.devices('default')[0]
    with pytest.raises(ValueError):
        server.confirm('someone-else', device['id'], device['fingerprint'], device['attempt'])
    invite = server.invitation('default', 'https://example.test')
    server.db.execute('UPDATE collector_invitations SET expires=0')
    with pytest.raises(ValueError):
        connect(server, invitation=invite)


def test_device_identity_and_confirmation_survive_restart(server):
    from mycelos.collector.server import CollectorServer
    identity, _, _ = connect(server)
    device = confirm(server)
    again = CollectorServer(server.app)
    conn, first = initiate(identity, again.identity.public, {'version':1,'name':'Laptop'})
    sid, reply = again.accept_handshake(first)
    conn.read_message(reply)
    assert exchange(again, sid, Channel(conn))['status'] == 'active'
    assert again.devices('default')[0]['id'] == device['id']


def test_http_auth_keeps_management_private_and_allows_noise_transport(server):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mycelos.gateway.auth import install_auth
    from mycelos.gateway.routers.collectors import router
    api = FastAPI()
    api.state.mycelos = server.app
    api.state.collector_server = server
    api.include_router(router)
    install_auth(api, 'password')
    client = TestClient(api)
    assert client.get('/api/collectors').status_code == 401
    invitation = client.post('/api/collectors/invitations', auth=('user','password'),
        json={'url':'https://example.test'}).json()
    conn, first = initiate(Identity.generate(), decode(invitation['server_key']),
        {'version':1,'name':'Laptop','invitation':invitation['secret']})
    from mycelos.collector.crypto import encode
    result = client.post('/api/collector-transport/handshake',json={'message':encode(first)})
    assert result.status_code == 200, result.text
    conn.read_message(decode(result.json()['message']))
    channel = Channel(conn)
    result = client.post('/api/collector-transport/exchange',json={
        'session_id':result.json()['session_id'], 'frames':channel.seal(dict(version=1,type='poll',id='1',payload={}))})
    assert result.status_code == 200, result.text
    assert channel.open(result.json()['frames'])['status'] == 'pending'


def test_lost_pairing_reply_can_resume_same_device(server):
    invite=server.invitation('default','https://example.test')
    identity,_,_=connect(server,invitation=invite)
    attempt=server.devices('default')[0]['attempt']
    _,sid,channel=connect(server,identity,invite)
    assert exchange(server,sid,channel)['status']=='pending'
    assert len(server.devices('default'))==1
    assert server.devices('default')[0]['attempt']==attempt


def test_replayed_hello_cannot_close_existing_session(server):
    identity,_,_=connect(server)
    confirm(server)
    conn,first=initiate(identity,server.identity.public,{'version':1,'name':'Laptop'})
    sid,reply=server.accept_handshake(first);conn.read_message(reply);channel=Channel(conn)
    assert exchange(server,sid,channel)['status']=='active'
    with pytest.raises(ValueError):
        server.accept_handshake(first)
    assert exchange(server,sid,channel)['status']=='active'


def test_bad_anonymous_hellos_do_not_block_valid_device(server):
    identity,_,_=connect(server);confirm(server)
    for _ in range(65):
        with pytest.raises(ValueError):
            server.accept_handshake(b'')
    conn,first=initiate(identity,server.identity.public,{'version':1,'name':'Laptop'})
    sid,reply=server.accept_handshake(first);conn.read_message(reply)
    assert exchange(server,sid,Channel(conn))['status']=='active'


def test_websocket_transports_bounded_frames(server):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mycelos.gateway.routers.collectors import router
    api=FastAPI();api.state.mycelos=server.app;api.state.collector_server=server;api.include_router(router)
    invite=server.invitation('default','https://example.test')
    conn,first=initiate(Identity.generate(),server.identity.public,{'version':1,'name':'Laptop','invitation':invite['secret']})
    with TestClient(api).websocket_connect('/api/collector-transport/ws') as ws:
        ws.send_bytes(first);conn.read_message(ws.receive_bytes());channel=Channel(conn)
        frames=channel.seal(dict(version=1,type='status',id='1',payload={'padding':'x'*100000}))
        ws.send_json({'count':len(frames)})
        for frame in frames:
            ws.send_text(frame)
        header=ws.receive_json()
        assert isinstance(header,dict)
        assert channel.open([ws.receive_text() for _ in range(header['count'])])['status']=='pending'
