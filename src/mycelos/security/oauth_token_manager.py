"""OAuth 2.0 token lifecycle for HTTP-MCP connectors.

Pure functions. Knows nothing about FastAPI, HTTP servers, or
subprocess spawning — just about exchanging codes for tokens,
refreshing expired tokens, and reading/writing the encrypted
credential store.

Storage shape (in credential_proxy under recipe.oauth_token_credential_service):
    {"api_key": json.dumps({
        "access_token": "ya29.xxx",
        "refresh_token": "1//xxx",
        "expires_at": "2026-04-23T09:30:00+00:00",
        "scope": "https://.../gmail.readonly https://.../gmail.compose",
        "token_type": "Bearer"
    })}
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)


@dataclass
class TokenPayload:
    access_token: str
    refresh_token: str
    expires_at: str   # ISO-8601, UTC
    scope: str
    token_type: str   # "Bearer"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _read_client_credentials(recipe, credential_proxy, user_id: str) -> tuple[str, str]:
    """Load client_id + client_secret from the stored client_secret_*.json."""
    cred = credential_proxy.get_credential(
        recipe.oauth_client_credential_service, user_id=user_id,
    )
    if not cred or not cred.get("api_key"):
        raise RuntimeError(
            f"Missing OAuth client credential '{recipe.oauth_client_credential_service}' — "
            "upload client_secret_*.json first."
        )
    client_json = json.loads(cred["api_key"])
    installed = client_json.get("installed") or client_json.get("web") or {}
    client_id = installed.get("client_id", "")
    client_secret = installed.get("client_secret", "")
    if not client_id or not client_secret:
        raise RuntimeError("Malformed OAuth client credential — missing client_id or client_secret.")
    return client_id, client_secret


def _store_token(recipe, credential_proxy, user_id: str, payload: TokenPayload) -> None:
    credential_proxy.store_credential(
        recipe.oauth_token_credential_service,
        {"api_key": json.dumps({
            "access_token": payload.access_token,
            "refresh_token": payload.refresh_token,
            "expires_at": payload.expires_at,
            "scope": payload.scope,
            "token_type": payload.token_type,
        })},
        user_id=user_id,
        label="default",
        description=f"OAuth token for {recipe.id}",
    )


def exchange_code_for_token(
    recipe,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    credential_proxy,
    user_id: str,
) -> TokenPayload:
    """Trade an authorization code for an access+refresh token and
    store the token blob in the credential proxy."""
    client_id, client_secret = _read_client_credentials(recipe, credential_proxy, user_id)

    resp = httpx.post(
        recipe.oauth_token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Token exchange failed ({resp.status_code}): {resp.text}")

    body = resp.json()
    expires_in = int(body.get("expires_in", 3600))
    expires_at = (_now_utc() + timedelta(seconds=expires_in)).isoformat()
    payload = TokenPayload(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token", ""),
        expires_at=expires_at,
        scope=body.get("scope", ""),
        token_type=body.get("token_type", "Bearer"),
    )
    _store_token(recipe, credential_proxy, user_id, payload)
    return payload


def refresh_if_expired(
    recipe,
    credential_proxy,
    user_id: str,
    refresh_threshold_seconds: int = 60,
) -> str:
    """Return a valid access_token. Refreshes if the stored token
    expires within `refresh_threshold_seconds`. Raises if the refresh
    fails (revoked refresh_token, network error) — caller must surface
    a 'reconnect required' message to the user."""
    cred = credential_proxy.get_credential(
        recipe.oauth_token_credential_service, user_id=user_id,
    )
    if not cred or not cred.get("api_key"):
        raise RuntimeError(
            f"No token stored under '{recipe.oauth_token_credential_service}' — "
            "connect the service first."
        )
    blob = json.loads(cred["api_key"])
    access_token = blob.get("access_token", "")
    refresh_token = blob.get("refresh_token", "")
    expires_at = blob.get("expires_at", "")

    if not expires_at:
        needs_refresh = True
    else:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            needs_refresh = (exp_dt - _now_utc()) < timedelta(seconds=refresh_threshold_seconds)
        except ValueError:
            needs_refresh = True

    if not needs_refresh:
        return access_token

    if not refresh_token:
        raise RuntimeError("Token expired and no refresh_token available — reconnect required.")

    client_id, client_secret = _read_client_credentials(recipe, credential_proxy, user_id)
    resp = httpx.post(
        recipe.oauth_token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Token refresh failed ({resp.status_code}): {resp.text}"
        )
    body = resp.json()
    new_access = body["access_token"]
    expires_in = int(body.get("expires_in", 3600))
    new_expires = (_now_utc() + timedelta(seconds=expires_in)).isoformat()
    # Google typically reuses the refresh_token across refreshes.
    new_refresh = body.get("refresh_token") or refresh_token
    new_payload = TokenPayload(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_at=new_expires,
        scope=body.get("scope", blob.get("scope", "")),
        token_type=body.get("token_type", blob.get("token_type", "Bearer")),
    )
    _store_token(recipe, credential_proxy, user_id, new_payload)
    return new_access
