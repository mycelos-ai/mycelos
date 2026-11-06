"""Telegram settings use local state and never send an implicit message."""
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mycelos.gateway.routers.setup import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('MYCELOS_MASTER_KEY', 'telegram-settings-test')
    from mycelos.app import App
    app = App(tmp_path)
    app.initialize()
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(router)
    return TestClient(api)


def test_config_save_retains_token_and_restricts_private_user(client):
    with patch('mycelos.gateway.server.start_telegram_channel'):
        response = client.put('/api/telegram/config', json={
            'token': '123456:SECRET', 'chat_id': '42', 'enabled': True,
        })
        assert response.status_code == 200
        response = client.put('/api/telegram/config', json={'chat_id': '43', 'enabled': True})
    assert response.status_code == 200
    state = client.get('/api/telegram/config').json()
    assert state['token_configured'] is True
    assert state['chat_id'] == '43'
    assert state['enabled'] is True
    assert 'SECRET' not in json.dumps(state)
    app = client.app.state.mycelos
    assert app.credentials.get_credential('telegram')['api_key'] == '123456:SECRET'
    row = app.storage.fetchone("SELECT allowed_users FROM channels WHERE id='telegram'")
    assert json.loads(row['allowed_users']) == [43]


@pytest.mark.parametrize('chat_id', ['', '-123', 'oops', None, {}, True])
def test_invalid_chat_does_not_store_token(client, chat_id):
    response = client.put('/api/telegram/config', json={
        'token': '123456:SECRET', 'chat_id': chat_id, 'enabled': True,
    })
    assert response.status_code == 400
    assert not client.app.state.mycelos.credentials.get_credential('telegram')


def test_check_lists_private_chats_and_reports_polling_conflict(client):
    replies = [
        {'ok': True, 'result': {'first_name': 'Test', 'username': 'test_bot'}},
        {'ok': True, 'result': [
            {'message': {'chat': {'id': 42, 'type': 'private', 'first_name': 'A'}}},
            {'message': {'chat': {'id': -1, 'type': 'group', 'title': 'Group'}}},
            {'message': {'chat': {'id': 43, 'type': 'private', 'first_name': 'B'}}},
        ]},
    ]
    with patch('mycelos.channels.telegram.call_telegram_api_with_token', side_effect=replies):
        result = client.post('/api/telegram/check', json={'token': '123456:SECRET'}).json()
    assert {chat['id'] for chat in result['chats']} == {'42', '43'}
    assert result['chat_id'] is None
    with patch('mycelos.channels.telegram.call_telegram_api_with_token', side_effect=[
        replies[0], {'ok': False, 'description': 'Conflict: 123456:SECRET'},
    ]):
        result = client.post('/api/telegram/check', json={'token': '123456:SECRET'}).json()
    assert result['valid'] is True
    assert result['discovery_error']
    assert 'SECRET' not in json.dumps(result)


def test_disable_stops_local_runtime_without_token(client):
    with patch('mycelos.channels.telegram.stop_polling') as stop:
        result = client.put('/api/telegram/config', json={'enabled': False}).json()
    assert result['enabled'] is False
    stop.assert_called_once()


def test_inline_get_preserves_parameters_and_scrubs_transport_error(monkeypatch):
    from mycelos.channels.telegram import call_telegram_api_with_token
    from mycelos.connectors import http_tools
    monkeypatch.setattr(http_tools, '_proxy_client', None)
    with patch('urllib.request.urlopen', side_effect=RuntimeError('123456:SECRET')) as send:
        result = call_telegram_api_with_token(None, '123456:SECRET', 'getUpdates',
            payload={'limit': 100, 'timeout': 1}, http_method='GET')
    assert 'limit=100' in send.call_args.args[0].full_url
    assert 'SECRET' not in result['description']


def test_polling_can_stop_and_start_again_without_network(monkeypatch):
    import asyncio
    import threading
    import mycelos.channels.telegram as tg
    ready = threading.Event()
    async def receive(*args, **kwargs):
        ready.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(tg, '_bot', object())
    monkeypatch.setattr(tg, '_polling_thread', None)
    monkeypatch.setattr(tg, '_heartbeat_stop', threading.Event())
    monkeypatch.setattr(tg, '_start_heartbeat', lambda: None)
    monkeypatch.setattr(tg.dp, 'start_polling', receive)
    for _ in range(2):
        ready.clear()
        thread = tg.start_polling()
        assert ready.wait(1)
        tg.stop_polling()
        thread.join(timeout=1)
        assert not thread.is_alive()


def test_real_dispatcher_restarts_without_orphan_tasks(monkeypatch):
    import asyncio
    import threading
    from unittest.mock import AsyncMock, MagicMock
    import mycelos.channels.telegram as tg
    ready = threading.Event()
    closed = threading.Event()
    async def receive(**kwargs):
        ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
    bot = MagicMock()
    bot.session.close = AsyncMock()
    monkeypatch.setattr(tg, '_bot', bot)
    monkeypatch.setattr(tg, '_polling_thread', None)
    monkeypatch.setattr(tg, '_heartbeat_stop', threading.Event())
    monkeypatch.setattr(tg, '_start_heartbeat', lambda: None)
    monkeypatch.setattr(tg.dp, '_polling', receive)
    for _ in range(2):
        ready.clear(); closed.clear()
        thread = tg.start_polling()
        assert ready.wait(1)
        tg.stop_polling()
        assert closed.is_set()
        assert not thread.is_alive()
