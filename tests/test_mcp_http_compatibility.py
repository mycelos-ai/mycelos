"""Exercise the installed MCP SDK over an in-memory HTTP transport."""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import mcp.shared._httpx_utils as sdk_http
httpx_client = getattr(sdk_http, "httpx2", None) or sdk_http.httpx
import pytest

from mycelos.connectors.mcp_client import MycelosMCPClient


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("fail_initialize", [False, True])
async def test_http_client_initializes_and_discovers_with_auth_without_network(monkeypatch, legacy, fail_initialize):
    requests = []
    clients = []

    def respond(request):
        requests.append(request)
        message = json.loads(request.content)
        if message['method'] == 'initialize':
            result = {'protocolVersion': 'invalid' if fail_initialize else '2025-11-25', 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'local-test', 'version': '1'}}
        elif message['method'] == 'tools/list':
            result = {'tools': [{'name':'search', 'description':'Search local test data',
                                'inputSchema':{'type':'object','properties':{'query':{'type':'string'}}}}]}
        elif message['method'] == 'notifications/initialized':
            return httpx_client.Response(202)
        else:
            pytest.fail('Unexpected MCP call: ' + message['method'])
        return httpx_client.Response(200, json={'jsonrpc':'2.0','id':message['id'],'result':result})

    def factory(headers=None, **kwargs):
        client = httpx_client.AsyncClient(headers=headers, transport=httpx_client.MockTransport(respond))
        clients.append(client)
        return client

    if legacy:
        from mcp.client import streamable_http
        modern = streamable_http.streamable_http_client

        @asynccontextmanager
        async def legacy_client(url, headers):
            async with factory(headers=headers) as http_client:
                async with modern(url, http_client=http_client) as streams:
                    yield streams[0], streams[1], lambda: None

        monkeypatch.setattr(streamable_http, 'streamablehttp_client', legacy_client, raising=False)
        monkeypatch.delattr(streamable_http, 'streamable_http_client')

    import mcp.shared._httpx_utils
    monkeypatch.setattr(mcp.shared._httpx_utils, 'create_mcp_http_client', factory)
    monkeypatch.setattr('mycelos.connectors.mcp_recipes.get_recipe', lambda _: SimpleNamespace(http_endpoint='https://local.test/mcp'))
    client = MycelosMCPClient('test-docs', '', transport='http')
    monkeypatch.setattr(client, '_resolve_token', lambda recipe: 'test-token')
    try:
        if fail_initialize:
            with pytest.raises(RuntimeError, match='Unsupported protocol'):
                await client.connect()
            assert not client.is_connected
            assert clients[0].is_closed
            return
        await client.connect()
        tools = await client.discover_tools()
        assert [tool['name'] for tool in tools] == ['test-docs.search']
        assert tools[0]['input_schema']['properties']['query']['type'] == 'string'
        assert client.is_connected
    finally:
        await client.disconnect()
    assert requests
    assert all(request.headers['Authorization'] == 'Bearer test-token' for request in requests)
    assert clients[0].is_closed
    assert not client.is_connected
