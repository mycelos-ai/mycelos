"""Tests for ModelUpdaterHandler (deterministic — no LLM)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def handler(tmp_path: Path, monkeypatch):
    from mycelos.agents.handlers.model_updater_handler import ModelUpdaterHandler
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "test-key-model-updater")
    app = MagicMock()
    # Default: user has anthropic credentials only
    app.credentials.list_credentials.return_value = [
        {"service": "anthropic", "label": "default"},
    ]
    # memory.get must accept keyword args (user_id=, scope=, key=)
    app.memory.get.return_value = None  # no ollama, no opt-out, no cache
    app.model_registry.sync_from_litellm.return_value = {
        "added": [], "updated": [], "total": 0,
    }
    return ModelUpdaterHandler(app), app


def test_handler_reports_no_additions(handler) -> None:
    h, app = handler
    result = h.run("default")
    assert result["added"] == []
    assert result["total"] == 0
    # No audit event when nothing new
    assert not any(
        call.args and call.args[0] == "models.discovered"
        for call in app.audit.log.call_args_list
    )


def test_handler_emits_audit_on_new_models(handler) -> None:
    h, app = handler
    app.model_registry.sync_from_litellm.return_value = {
        "added": ["anthropic/claude-opus-4-7", "anthropic/claude-sonnet-4-7"],
        "updated": [],
        "total": 2,
    }
    result = h.run("default")
    assert len(result["added"]) == 2
    # Audit event logged
    assert any(
        call.args and call.args[0] == "models.discovered"
        for call in app.audit.log.call_args_list
    )


def test_handler_uses_prefer_remote(handler) -> None:
    """The periodic handler must request the remote cost map so users
    get fresh-off-the-press models without a pip upgrade."""
    h, app = handler
    h.run("default")
    call = app.model_registry.sync_from_litellm.call_args
    assert call.kwargs["prefer_remote"] is True


def test_handler_restricts_to_configured_providers(handler) -> None:
    """Only providers the user has credentials for should be synced.
    Avoids flooding the registry with 200 Gemini models when only
    Anthropic is configured."""
    h, app = handler
    app.credentials.list_credentials.return_value = [
        {"service": "anthropic", "label": "default"},
        {"service": "openai", "label": "default"},
    ]
    h.run("default")
    call = app.model_registry.sync_from_litellm.call_args
    assert sorted(call.kwargs["providers"]) == ["anthropic", "openai"]


def test_handler_skips_sync_when_no_credentials(handler) -> None:
    """No credentials → no sync. Nothing to discover that's actually usable."""
    h, app = handler
    app.credentials.list_credentials.return_value = []
    app.memory.get.return_value = None
    result = h.run("default")
    assert result["total"] == 0
    app.model_registry.sync_from_litellm.assert_not_called()


def test_handler_includes_ollama_when_configured(handler) -> None:
    """Ollama is credential-less. When the endpoint is stored, include it."""
    h, app = handler
    app.credentials.list_credentials.return_value = []
    app.memory.get.return_value = "http://localhost:11434"
    h.run("default")
    call = app.model_registry.sync_from_litellm.call_args
    assert "ollama" in call.kwargs["providers"]


def test_handler_survives_sync_failure(handler) -> None:
    """If the refresh itself raises, the handler must not crash — it's
    a periodic job; a bad network day cannot take the scheduler down."""
    h, app = handler
    app.model_registry.sync_from_litellm.side_effect = RuntimeError("boom")
    result = h.run("default")
    assert result.get("error")
    assert any(
        call.args and call.args[0] == "models.refresh_failed"
        for call in app.audit.log.call_args_list
    )


def test_handler_is_pure_python_no_llm(handler) -> None:
    """Proof point: the handler never touches app.llm. This is a
    deterministic workflow — the whole point of this feature."""
    h, app = handler
    h.run("default")
    app.llm.complete.assert_not_called()
    app.llm.assert_not_called()


# ── App update check ───────────────────────────────────────────

@pytest.fixture
def update_handler(handler, monkeypatch):
    """Handler with a stubbed httpx so we can inject release responses."""
    from mycelos.agents.handlers import model_updater_handler as mod
    h, app = handler
    # No prior cached update state
    app.memory.get.side_effect = lambda key, scope=None: None
    return h, app, mod


def _fake_response(status: int, payload: dict | None = None):
    class R:
        status_code = status
        def raise_for_status(self):
            if status >= 400:
                import httpx
                raise httpx.HTTPStatusError("boom", request=None, response=None)
        def json(self):
            return payload or {}
    return R()


def test_update_check_reports_newer_version(update_handler, monkeypatch):
    h, app, mod = update_handler
    from mycelos.agents.handlers.model_updater_handler import ModelUpdaterHandler
    monkeypatch.setattr(ModelUpdaterHandler, "_current_version", staticmethod(lambda: "0.1.0"))
    import httpx
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kw: _fake_response(
            200,
            {"tag_name": "v0.2.0", "html_url": "https://github.com/x/y/releases/tag/v0.2.0", "published_at": "2026-04-20T10:00:00Z"},
        ),
    )
    result = h.run("default")
    assert result["update_available"] is True
    assert result["latest_version"] == "0.2.0"
    # Audit fired once
    audited = [c for c in app.audit.log.call_args_list if c.args and c.args[0] == "mycelos.update_available"]
    assert len(audited) == 1


def test_update_check_same_version_no_alert(update_handler, monkeypatch):
    h, app, mod = update_handler
    from mycelos.agents.handlers.model_updater_handler import ModelUpdaterHandler
    monkeypatch.setattr(ModelUpdaterHandler, "_current_version", staticmethod(lambda: "0.2.0"))
    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _fake_response(200, {"tag_name": "v0.2.0"}))
    result = h.run("default")
    assert result["update_available"] is False
    audited = [c for c in app.audit.log.call_args_list if c.args and c.args[0] == "mycelos.update_available"]
    assert audited == []


def test_update_check_opt_out(update_handler, monkeypatch):
    h, app, mod = update_handler
    # User has opted out
    def fake_get(*a, **kw):
        key = kw.get("key") or (a[2] if len(a) >= 3 else None)
        return "false" if key == "system.check_for_updates" else None
    app.memory.get.side_effect = fake_get
    import httpx
    called = []
    monkeypatch.setattr(httpx, "get", lambda url, **kw: called.append(url) or _fake_response(200, {}))
    h.run("default")
    # httpx.get never called
    assert called == []


def test_update_check_survives_network_error(update_handler, monkeypatch):
    h, app, mod = update_handler
    from mycelos.agents.handlers.model_updater_handler import ModelUpdaterHandler
    monkeypatch.setattr(ModelUpdaterHandler, "_current_version", staticmethod(lambda: "0.1.0"))
    import httpx
    def boom(*a, **kw):
        raise httpx.RequestError("offline")
    monkeypatch.setattr(httpx, "get", boom)
    # Must not raise, run must still complete
    result = h.run("default")
    assert "update_available" in result


def test_update_check_audits_only_once_per_new_tag(update_handler, monkeypatch):
    """Running the handler twice on the same latest-tag should only log
    one audit event, not spam it daily."""
    h, app, mod = update_handler
    from mycelos.agents.handlers.model_updater_handler import ModelUpdaterHandler
    monkeypatch.setattr(ModelUpdaterHandler, "_current_version", staticmethod(lambda: "0.1.0"))
    import httpx, json
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _fake_response(200, {"tag_name": "v0.2.0"}))

    # First call: nothing cached
    memory_store: dict = {}
    def _get(*a, **kw):
        key = kw.get("key") or (a[2] if len(a) >= 3 else None)
        return memory_store.get(key)
    def _set(*a, **kw):
        key = kw.get("key") or (a[2] if len(a) >= 3 else None)
        value = kw.get("value")
        memory_store[key] = value
    app.memory.get.side_effect = _get
    app.memory.set.side_effect = _set

    h.run("default")
    h.run("default")

    audited = [c for c in app.audit.log.call_args_list if c.args and c.args[0] == "mycelos.update_available"]
    assert len(audited) == 1, "update audit must fire only once per new tag"
