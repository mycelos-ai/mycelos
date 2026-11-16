"""LITELLM_SECRET from the environment must never leave the process.

It is read at call time to fill a blank custom-provider token. It must not
be copied into the credential store or returned by any settings endpoint.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mycelos.app import App
from mycelos.gateway.routers.models import router

SECRET = 'sk-litellm-env-secret-value-1234567890'


def test_env_secret_is_never_returned_or_stored(tmp_path, monkeypatch):
    monkeypatch.setenv('MYCELOS_MASTER_KEY', 'env-secret-invariant')
    monkeypatch.setenv('LITELLM_HOST', 'http://proxy:4000')
    monkeypatch.setenv('LITELLM_SECRET', SECRET)
    app = App(tmp_path)
    app.storage.initialize()
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(router)
    client = TestClient(api)

    saved = client.put('/api/models/providers/custom', json={'url': 'http://proxy:4000/v1', 'api_key': ''})
    assert saved.status_code == 200
    for path in ('/api/models/settings', '/api/models/stt', '/api/models'):
        response = client.get(path)
        assert SECRET not in response.text, path
    assert SECRET not in str(app.credentials.get_credential('custom'))
    rows = app.storage.fetchall('SELECT details FROM audit_events')
    assert all(SECRET not in str(row['details']) for row in rows)
