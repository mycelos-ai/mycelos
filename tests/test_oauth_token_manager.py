"""OAuth token manager — code exchange + lazy refresh.

Pure functions tested with mocked httpx + MagicMock credential_proxy.
No live network."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from mycelos.security.oauth_token_manager import (
    TokenPayload,
    exchange_code_for_token,
    refresh_if_expired,
)


def _fake_recipe(**overrides):
    from mycelos.connectors.mcp_recipes import MCPRecipe
    base = dict(
        id="gmail",
        name="Gmail",
        description="",
        command="",
        transport="http",
        oauth_token_url="https://oauth2.googleapis.com/token",
        oauth_client_credential_service="gmail-oauth-client",
        oauth_token_credential_service="gmail-oauth-token",
    )
    base.update(overrides)
    return MCPRecipe(**base)


def _client_blob():
    return {"api_key": json.dumps({
        "installed": {
            "client_id": "cid.apps.googleusercontent.com",
            "client_secret": "csec",
        }
    })}


def test_exchange_code_for_token_posts_correctly_and_stores():
    """Happy path: POST the right form body, parse response, store
    as JSON blob on oauth_token_credential_service."""
    recipe = _fake_recipe()
    cp = MagicMock()
    cp.get_credential.return_value = _client_blob()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "access_token": "ya29.abc",
        "refresh_token": "1//rtok",
        "expires_in": 3600,
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "token_type": "Bearer",
    }
    with patch("mycelos.security.oauth_token_manager.httpx.post", return_value=fake_response) as post:
        payload = exchange_code_for_token(
            recipe=recipe,
            code="auth-code-123",
            code_verifier="verifier-xyz",
            redirect_uri="http://localhost:9100/api/connectors/oauth/callback",
            credential_proxy=cp,
            user_id="default",
        )

    # POST body contains all required OAuth params
    call = post.call_args
    assert call.args[0] == "https://oauth2.googleapis.com/token"
    body = call.kwargs["data"]
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "auth-code-123"
    assert body["code_verifier"] == "verifier-xyz"
    assert body["redirect_uri"] == "http://localhost:9100/api/connectors/oauth/callback"
    assert body["client_id"] == "cid.apps.googleusercontent.com"
    assert body["client_secret"] == "csec"

    assert payload.access_token == "ya29.abc"
    assert payload.refresh_token == "1//rtok"
    assert payload.token_type == "Bearer"

    # Stored back as JSON blob
    cp.store_credential.assert_called_once()
    store_args = cp.store_credential.call_args
    stored_service = store_args.args[0] if store_args.args else store_args.kwargs["service"]
    stored_value = store_args.args[1] if len(store_args.args) > 1 else store_args.kwargs["credential"]
    assert stored_service == "gmail-oauth-token"
    blob = json.loads(stored_value["api_key"])
    assert blob["access_token"] == "ya29.abc"
    assert blob["refresh_token"] == "1//rtok"
    assert "expires_at" in blob  # ISO string


def test_exchange_code_raises_on_http_error():
    recipe = _fake_recipe()
    cp = MagicMock()
    cp.get_credential.return_value = _client_blob()

    fake_response = MagicMock()
    fake_response.status_code = 400
    fake_response.text = '{"error": "invalid_grant"}'
    with patch("mycelos.security.oauth_token_manager.httpx.post", return_value=fake_response):
        with pytest.raises(RuntimeError) as excinfo:
            exchange_code_for_token(
                recipe=recipe,
                code="bad",
                code_verifier="v",
                redirect_uri="http://x",
                credential_proxy=cp,
                user_id="default",
            )
    assert "invalid_grant" in str(excinfo.value)
    cp.store_credential.assert_not_called()


def test_exchange_code_raises_when_client_credential_missing():
    recipe = _fake_recipe()
    cp = MagicMock()
    cp.get_credential.return_value = None

    with pytest.raises(RuntimeError) as excinfo:
        exchange_code_for_token(
            recipe=recipe,
            code="c",
            code_verifier="v",
            redirect_uri="http://x",
            credential_proxy=cp,
            user_id="default",
        )
    assert "gmail-oauth-client" in str(excinfo.value)


def test_refresh_if_expired_returns_current_token_when_valid():
    """Token that expires >60s in the future is returned as-is,
    no HTTP call, no re-store."""
    recipe = _fake_recipe()
    cp = MagicMock()
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    cp.get_credential.return_value = {"api_key": json.dumps({
        "access_token": "still-valid",
        "refresh_token": "rtok",
        "expires_at": future,
        "scope": "x",
        "token_type": "Bearer",
    })}

    with patch("mycelos.security.oauth_token_manager.httpx.post") as post:
        tok = refresh_if_expired(
            recipe=recipe, credential_proxy=cp, user_id="default",
        )

    assert tok == "still-valid"
    post.assert_not_called()
    cp.store_credential.assert_not_called()


def test_refresh_if_expired_refreshes_when_stale():
    """Token expiring in <60s triggers a refresh POST and updates
    the stored credential."""
    recipe = _fake_recipe()
    cp = MagicMock()

    def fake_get(service, user_id="default"):
        if service == "gmail-oauth-token":
            return {"api_key": json.dumps({
                "access_token": "expired",
                "refresh_token": "rtok",
                "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
                "scope": "x",
                "token_type": "Bearer",
            })}
        if service == "gmail-oauth-client":
            return {"api_key": json.dumps({
                "installed": {"client_id": "cid", "client_secret": "csec"}
            })}
        return None
    cp.get_credential.side_effect = fake_get

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "access_token": "new-fresh",
        "expires_in": 3600,
        "scope": "x",
        "token_type": "Bearer",
        # note: no refresh_token in the refresh response — Google
        # reuses the old one
    }
    with patch("mycelos.security.oauth_token_manager.httpx.post", return_value=fake_response) as post:
        tok = refresh_if_expired(
            recipe=recipe, credential_proxy=cp, user_id="default",
        )

    assert tok == "new-fresh"
    body = post.call_args.kwargs["data"]
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "rtok"
    assert body["client_id"] == "cid"
    assert body["client_secret"] == "csec"

    # Stored updated token; refresh_token carried forward
    store_args = cp.store_credential.call_args
    stored_value = store_args.args[1] if len(store_args.args) > 1 else store_args.kwargs["credential"]
    blob = json.loads(stored_value["api_key"])
    assert blob["access_token"] == "new-fresh"
    assert blob["refresh_token"] == "rtok"


def test_refresh_if_expired_raises_on_revoked_refresh_token():
    """Google returns invalid_grant when refresh token is revoked."""
    recipe = _fake_recipe()
    cp = MagicMock()

    def fake_get(service, user_id="default"):
        if service == "gmail-oauth-token":
            return {"api_key": json.dumps({
                "access_token": "exp",
                "refresh_token": "revoked",
                "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
                "scope": "x",
                "token_type": "Bearer",
            })}
        if service == "gmail-oauth-client":
            return {"api_key": json.dumps({
                "installed": {"client_id": "cid", "client_secret": "csec"}
            })}
        return None
    cp.get_credential.side_effect = fake_get

    fake_response = MagicMock()
    fake_response.status_code = 400
    fake_response.text = '{"error": "invalid_grant"}'
    with patch("mycelos.security.oauth_token_manager.httpx.post", return_value=fake_response):
        with pytest.raises(RuntimeError) as excinfo:
            refresh_if_expired(
                recipe=recipe, credential_proxy=cp, user_id="default",
            )
    assert "invalid_grant" in str(excinfo.value).lower()


def test_refresh_if_expired_raises_when_no_token_stored():
    """No token row at all → clear error."""
    recipe = _fake_recipe()
    cp = MagicMock()
    cp.get_credential.return_value = None

    with pytest.raises(RuntimeError) as excinfo:
        refresh_if_expired(
            recipe=recipe, credential_proxy=cp, user_id="default",
        )
    assert "gmail-oauth-token" in str(excinfo.value)
