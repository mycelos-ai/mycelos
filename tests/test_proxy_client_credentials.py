"""SecurityProxyClient credential_* methods — gateway side of the RPC."""
from __future__ import annotations

from unittest.mock import MagicMock

from mycelos.security.proxy_client import SecurityProxyClient


def _mock_resp(status: int, payload: dict | list):
    r = MagicMock()
    r.status_code = status
    r.json = lambda: payload
    return r


def test_credential_store_posts_body(monkeypatch):
    c = SecurityProxyClient(url="http://proxy:9110", token="t")
    captured = {}
    def fake_request(method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = kwargs.get("json")
        return _mock_resp(200, {"status": "stored", "service": "x", "label": "default"})
    monkeypatch.setattr(c, "_request", fake_request)
    result = c.credential_store("x", {"api_key": "abc"}, label="default", description="unit")
    assert captured["method"] == "POST"
    assert captured["path"] == "/credential/store"
    assert captured["json"] == {
        "service": "x",
        "label": "default",
        "payload": {"api_key": "abc"},
        "description": "unit",
    }
    assert result["status"] == "stored"


def test_credential_store_default_description_is_none(monkeypatch):
    c = SecurityProxyClient(url="http://proxy:9110", token="t")
    captured = {}
    def fake_request(method, path, **kwargs):
        captured["json"] = kwargs.get("json")
        return _mock_resp(200, {"status": "stored"})
    monkeypatch.setattr(c, "_request", fake_request)
    c.credential_store("x", {"k": "v"})
    assert captured["json"]["description"] is None
    assert captured["json"]["label"] == "default"


def test_credential_delete_uses_url_params(monkeypatch):
    c = SecurityProxyClient(url="http://proxy:9110", token="t")
    captured = {}
    def fake_request(method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        return _mock_resp(200, {"status": "deleted"})
    monkeypatch.setattr(c, "_request", fake_request)
    result = c.credential_delete("anthropic", "default")
    assert captured["method"] == "DELETE"
    assert captured["path"] == "/credential/anthropic/default"
    assert result["status"] == "deleted"


def test_credential_list_returns_items(monkeypatch):
    c = SecurityProxyClient(url="http://proxy:9110", token="t")
    def fake_request(method, path, **kwargs):
        return _mock_resp(200, {"credentials": [
            {"service": "anthropic", "label": "default", "description": None,
             "created_at": "2026-04-20T10:00:00Z"},
        ]})
    monkeypatch.setattr(c, "_request", fake_request)
    items = c.credential_list()
    assert len(items) == 1
    assert items[0]["service"] == "anthropic"


def test_credential_list_empty_when_malformed(monkeypatch):
    c = SecurityProxyClient(url="http://proxy:9110", token="t")
    monkeypatch.setattr(c, "_request", lambda *a, **k: _mock_resp(200, "not a dict"))
    assert c.credential_list() == []


def test_credential_rotate_posts_json(monkeypatch):
    c = SecurityProxyClient(url="http://proxy:9110", token="t")
    captured = {}
    def fake_request(method, path, **kwargs):
        captured["json"] = kwargs.get("json")
        return _mock_resp(200, {"status": "rotated"})
    monkeypatch.setattr(c, "_request", fake_request)
    c.credential_rotate("slack", "default")
    assert captured["json"] == {"service": "slack", "label": "default"}
