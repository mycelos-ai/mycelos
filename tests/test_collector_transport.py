import hashlib
import json

import pytest


def channel_pair():
    from mycelos.collector.crypto import Identity, initiate, respond, Channel
    client, server = Identity.generate(), Identity.generate()
    conn, first = initiate(client, server.public, {'version': 1, 'invitation': 'secret', 'name': 'Laptop'})
    response, peer, payload, reply = respond(server, first, {'status': 'pending'})
    assert peer == client.public
    assert payload['invitation'] == 'secret'
    assert json.loads(conn.read_message(reply))['status'] == 'pending'
    return Channel(conn), Channel(response)


def test_noise_binds_keys_and_encrypts_both_directions():
    client, server = channel_pair()
    payload = {'version': 1, 'type': 'scan', 'id': 'scan-1', 'payload': {'content': 'private' * 20000}}
    frames = client.seal(payload)
    assert len(frames) > 1
    assert all('private' not in frame for frame in frames)
    assert server.open(frames) == payload
    assert client.open(server.seal({'ok': True})) == {'ok': True}


def test_substitute_server_key_fails():
    from mycelos.collector.crypto import Identity, initiate, respond
    client, server, attacker = Identity.generate(), Identity.generate(), Identity.generate()
    _, first = initiate(client, server.public, {})
    with pytest.raises(ValueError):
        respond(attacker, first, {})


@pytest.mark.parametrize('attack', ['replay', 'tamper', 'truncate', 'reorder'])
def test_invalid_ciphertext_closes_channel(attack):
    client, server = channel_pair()
    frames = client.seal({'text': 'x' * 100000})
    if attack == 'replay':
        server.open(frames)
    elif attack == 'tamper':
        frames[0] = 'AAAA' + frames[0][4:]
    elif attack == 'truncate':
        frames.pop()
    else:
        frames.reverse()
    with pytest.raises(ValueError):
        server.open(frames)
    with pytest.raises(ValueError):
        server.open(client.seal({'text': 'later'}))


def test_identity_persists_with_private_permissions(tmp_path):
    from mycelos.collector.crypto import Identity, fingerprint
    path = tmp_path / 'identity'
    first = Identity.load(path)
    assert Identity.load(path).public == first.public
    assert path.stat().st_mode & 0o777 == 0o600
    assert fingerprint(first.public).replace(':', '').lower() == hashlib.sha256(first.public).hexdigest()
    assert 'private' not in repr(first)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        Identity.load(path)
