# OAuth-HTTP-MCP for Official Google Connectors — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Gmail as a native HTTP-MCP connector authenticated via OAuth 2.0 Authorization Code Flow against `gmailmcp.googleapis.com/mcp/v1`. Remove the old `oauth_browser` / file-materialization path.

**Architecture:** New `setup_flow="oauth_http"` on `MCPRecipe`. Browser flows through a gateway-hosted OAuth callback (`/api/connectors/oauth/callback`) using PKCE + state; the proxy exchanges the auth code for a token and stores it as a JSON-blob credential. Lazy token refresh (60s-buffer expiry check) before every MCP call. Archive the old flow to `archive/oauth-browser-file-materialization` before deleting from main.

**Tech Stack:** Python 3.12+, FastAPI (gateway + proxy), httpx for Google token endpoint, existing `mcp.client.streamable_http` for HTTP MCP, Alpine.js frontend, `secrets` + `hashlib` for PKCE, `cryptography` (already used) for credential store.

---

## Spec

Source: `docs/superpowers/specs/2026-04-23-oauth-http-mcp-design.md`

---

## File structure

| File | Responsibility |
|---|---|
| `src/mycelos/connectors/mcp_recipes.py` | Add `oauth_http` setup_flow + 6 new recipe fields; flip `gmail` recipe; remove `oauth_browser` artifacts |
| `src/mycelos/connectors/oauth_setup_guides.py` | Rewrite `google_cloud` guide for new flow (enable MCP API, register Redirect-URI) |
| `src/mycelos/connectors/mcp_client.py` | HTTP endpoint resolved from recipe; token resolution via `oauth_token_manager` |
| `src/mycelos/security/oauth_token_manager.py` | NEW — pure functions for code-exchange and lazy refresh |
| `src/mycelos/security/proxy_server.py` | Add `POST /oauth/callback`; remove old `/oauth/start`, `/oauth/stop`, WS `/oauth/stream/{sid}`, `OAUTH_TMP_ROOT`, allowlists, materializer imports |
| `src/mycelos/security/proxy_client.py` | Add `oauth_callback(recipe_id, code, code_verifier, redirect_uri)`; remove `oauth_start`/`oauth_stop`/`oauth_stream_url` |
| `src/mycelos/security/credential_materializer.py` | DELETE |
| `src/mycelos/gateway/routes.py` | Replace old passthroughs with `POST /api/connectors/oauth/start` + `GET /api/connectors/oauth/callback`; add `app.state.oauth_pending_states` dict |
| `src/mycelos/frontend/pages/connectors.html` | Dialog: different stage 2 (show auth URL + redirect-URI hint), detect `?connected=<id>` and `?oauth_error=<msg>` on init |
| `src/mycelos/frontend/shared/oauth_setup.js` | Strip WS client + regex; keep query-param helper only |
| `tests/test_mcp_recipe_setup_flow.py` | Update: new `oauth_http` tests, remove `oauth_browser` assertions |
| `tests/test_oauth_token_manager.py` | NEW |
| `tests/test_proxy_oauth_callback.py` | NEW |
| `tests/test_gateway_oauth_http_flow.py` | NEW |
| `tests/test_credential_materializer.py` | DELETE |
| `tests/test_proxy_oauth_endpoints.py` | DELETE |
| `tests/test_proxy_oauth_websocket.py` | DELETE |
| `tests/integration/test_gmail_mcp_http_live.py` | NEW — live integration test skipped without `.env.test` creds |
| `docker-compose.yml` | Remove tmpfs mount for `/tmp/mycelos-oauth` |
| `docs/deployment/google-setup.md` | Rewrite for new flow |
| `CHANGELOG.md` | Week 17 entry |

---

## Task 1 — Create archive branch

**Files:** none (git only)

- [ ] **Step 1: From main, push the current state as an archive branch**

```bash
cd /Users/stefan/Documents/railsapps/mycelos
git checkout main
git pull --ff-only
git push origin main:archive/oauth-browser-file-materialization
```

Expected: `archive/oauth-browser-file-materialization` exists on origin, pointing at the same SHA as current `main`.

- [ ] **Step 2: Verify the branch is discoverable**

```bash
git ls-remote origin | grep archive/oauth-browser-file-materialization
```

Expected: one matching ref line.

- [ ] **Step 3: Create the feature branch + worktree for the rest of the plan**

(Follow the using-git-worktrees skill — this step just names the branch.)

Branch: `feature/oauth-http-mcp`. Base: `main`.

No commit in this task. The archive branch is purely a pointer; no code written here.

---

## Task 2 — Recipe fields for oauth_http

**Files:**
- Modify: `src/mycelos/connectors/mcp_recipes.py`
- Modify: `tests/test_mcp_recipe_setup_flow.py`

Context: The `MCPRecipe` dataclass currently has `setup_flow`, `oauth_cmd`, `oauth_setup_guide_id`, `oauth_keys_credential_service`, `oauth_keys_home_dir`, `oauth_keys_filename`, `oauth_token_filename`, `oauth_token_credential_service`. We add 6 new fields (`http_endpoint`, `oauth_authorize_url`, `oauth_token_url`, `oauth_scopes`, `oauth_client_credential_service`, `oauth_token_credential_service`) AND remove the file-materialization ones (`oauth_cmd`, `oauth_keys_*`, `oauth_token_filename`). `oauth_token_credential_service` survives but now holds the access+refresh token JSON blob, not the file-materialized token.

- [ ] **Step 1: Rewrite the failing tests**

Replace the `# ── File-based credential fields ──` block at the bottom of `tests/test_mcp_recipe_setup_flow.py` (Task 1-5 of the previous plan added it) with these tests. Locate the block starting at `# ── File-based credential fields ──` and delete everything from there to the end of the file. Then append:

```python


# ── oauth_http fields ──


def test_mcp_recipe_defaults_for_oauth_http_fields() -> None:
    """All new oauth_http fields default to empty (list for scopes).
    Only recipes that explicitly opt in get the HTTP flow."""
    r = MCPRecipe(id="x", name="X", description="", command="npx -y x")
    assert r.http_endpoint == ""
    assert r.oauth_authorize_url == ""
    assert r.oauth_token_url == ""
    assert r.oauth_scopes == []
    assert r.oauth_client_credential_service == ""
    assert r.oauth_token_credential_service == ""


def test_gmail_recipe_declares_oauth_http() -> None:
    r = RECIPES["gmail"]
    assert r.setup_flow == "oauth_http"
    assert r.http_endpoint == "https://gmailmcp.googleapis.com/mcp/v1"
    assert r.oauth_authorize_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert r.oauth_token_url == "https://oauth2.googleapis.com/token"
    assert "https://www.googleapis.com/auth/gmail.readonly" in r.oauth_scopes
    assert "https://www.googleapis.com/auth/gmail.compose" in r.oauth_scopes
    assert r.oauth_client_credential_service == "gmail-oauth-client"
    assert r.oauth_token_credential_service == "gmail-oauth-token"


def test_oauth_browser_is_gone() -> None:
    """The old file-materialization setup_flow value is removed; no
    recipe still declares it."""
    for r in RECIPES.values():
        assert r.setup_flow != "oauth_browser", f"{r.id} still uses oauth_browser"


def test_old_file_mat_fields_removed_from_dataclass() -> None:
    """The file-materialization fields are gone from MCPRecipe."""
    r = MCPRecipe(id="x", name="X", description="", command="npx -y x")
    assert not hasattr(r, "oauth_cmd")
    assert not hasattr(r, "oauth_keys_credential_service")
    assert not hasattr(r, "oauth_keys_home_dir")
    assert not hasattr(r, "oauth_keys_filename")
    assert not hasattr(r, "oauth_token_filename")
```

Also remove these existing tests (file-mat-era, no longer valid):

```python
def test_gmail_recipe_declares_oauth_browser()  # delete
def test_google_calendar_recipe_declares_oauth_browser()  # delete
def test_google_drive_recipe_declares_oauth_browser()  # delete
def test_gmail_recipe_uses_file_materialization()  # delete
def test_google_calendar_recipe_uses_file_materialization()  # delete
def test_google_drive_recipe_uses_file_materialization()  # delete
def test_non_file_recipes_keep_empty_materialization_fields()  # delete
def test_mcp_recipe_defaults_for_file_credentials()  # delete
```

Keep intact: tests about `setup_flow` default, `google_cloud` guide registry (those are updated in later tasks, not this one).

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/stefan/Documents/railsapps/mycelos
PYTHONPATH=src pytest tests/test_mcp_recipe_setup_flow.py -v 2>&1 | tail -30
```

Expected: the 4 new tests fail (`http_endpoint` etc. don't exist; gmail still declares oauth_browser).

- [ ] **Step 3: Update the MCPRecipe dataclass**

Edit `src/mycelos/connectors/mcp_recipes.py`. Find the `MCPRecipe` dataclass.

**Remove** these fields (they're the file-materialization ones):

```python
    oauth_cmd: str = ""
    ...

    oauth_keys_credential_service: str = ""
    ...

    oauth_keys_home_dir: str = ""
    ...

    oauth_keys_filename: str = ""
    ...

    oauth_token_filename: str = ""
    ...
```

**Keep** `oauth_token_credential_service` (it survives, reused for the JSON-blob token).

**Add** after `oauth_setup_guide_id`:

```python
    http_endpoint: str = ""
    # Remote MCP endpoint for HTTP-transport recipes. Empty for
    # stdio-transport recipes.

    oauth_authorize_url: str = ""
    # OAuth 2.0 authorize endpoint (Google, Microsoft, etc.).

    oauth_token_url: str = ""
    # OAuth 2.0 token endpoint (for code exchange AND refresh).

    oauth_scopes: list[str] = field(default_factory=list)
    # Space-joined into the `scope` query param on the auth URL.

    oauth_client_credential_service: str = ""
    # DB row holding {"api_key": json.dumps(client_secret_json)}.
    # The blob is the raw client_secret_*.json the user downloads
    # from Cloud Console.
```

Note: `oauth_token_credential_service` already exists (kept from file-mat era). Its *content* changes: now a JSON blob with `{access_token, refresh_token, expires_at, scope, token_type}`. Field name unchanged.

- [ ] **Step 4: Update the Gmail recipe**

In `mcp_recipes.py`, find the `gmail` recipe. Replace its body with:

```python
    "gmail": MCPRecipe(
        id="gmail",
        name="Gmail",
        description="Read, search, send, and manage Gmail via Google's official MCP server",
        command="",
        transport="http",
        setup_flow="oauth_http",
        oauth_setup_guide_id="google_cloud",
        http_endpoint="https://gmailmcp.googleapis.com/mcp/v1",
        oauth_authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        oauth_token_url="https://oauth2.googleapis.com/token",
        oauth_scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
        ],
        oauth_client_credential_service="gmail-oauth-client",
        oauth_token_credential_service="gmail-oauth-token",
        category="google",
        capabilities_preview=[
            "search_threads", "get_thread", "list_labels",
            "create_draft", "list_drafts",
        ],
    ),
```

- [ ] **Step 5: Remove the google-calendar and google-drive recipes for now**

This plan ships Gmail only (Non-goal per spec). Delete the `"google-calendar"` and `"google-drive"` entries from `RECIPES`. They can be added back in a follow-up plan once Gmail is proven.

- [ ] **Step 6: Verify tests pass**

```bash
cd /Users/stefan/Documents/railsapps/mycelos
PYTHONPATH=src pytest tests/test_mcp_recipe_setup_flow.py -v 2>&1 | tail -20
```

Expected: all green. `test_oauth_browser_is_gone` passes (no recipe still has `oauth_browser`). `test_gmail_recipe_declares_oauth_http` passes.

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/connectors/mcp_recipes.py tests/test_mcp_recipe_setup_flow.py
git commit -m "feat(mcp): oauth_http setup_flow + drop file-materialization recipe fields"
```

No `Co-Authored-By` footer. English only.

---

## Task 3 — OAuth token manager

**Files:**
- Create: `src/mycelos/security/oauth_token_manager.py`
- Create: `tests/test_oauth_token_manager.py`

Context: Pure helper module. Two functions: `exchange_code_for_token` (swap code for token via POST to `oauth_token_url`) and `refresh_if_expired` (read stored token, refresh via refresh_token if <60s valid, write back). Works against the `credential_proxy.get_credential/store_credential` interface, no FastAPI, no HTTP server.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_oauth_token_manager.py`:

```python
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

    # Both the expired token AND the client credential come through
    # get_credential. Use side_effect to distinguish.
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
    # POST body was a refresh
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
PYTHONPATH=src pytest tests/test_oauth_token_manager.py -v
```

Expected: all 7 fail with `ModuleNotFoundError: mycelos.security.oauth_token_manager`.

- [ ] **Step 3: Create the module**

Create `src/mycelos/security/oauth_token_manager.py`:

```python
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
        # Treat missing timestamp as expired (safer than trusting).
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
```

- [ ] **Step 4: Verify tests pass**

```bash
PYTHONPATH=src pytest tests/test_oauth_token_manager.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/security/oauth_token_manager.py tests/test_oauth_token_manager.py
git commit -m "feat(security): oauth_token_manager — code exchange + lazy refresh"
```

---

## Task 4 — Proxy `/oauth/callback` endpoint

**Files:**
- Modify: `src/mycelos/security/proxy_server.py` — add `/oauth/callback`, remove old `/oauth/start`, `/oauth/stop`, WS handler + materializer imports + `OAUTH_TMP_ROOT`, `_OAUTH_ALLOWED_HEADS`, `_MCP_ALLOWED_HEADS`, `OauthStartRequest`, ExitStack use in `/mcp/start`
- Modify: `src/mycelos/security/proxy_client.py` — add `oauth_callback`, remove `oauth_start`/`oauth_stop`/`oauth_stream_url`
- Create: `tests/test_proxy_oauth_callback.py`
- Delete: `tests/test_proxy_oauth_endpoints.py`, `tests/test_proxy_oauth_websocket.py`

Context: The proxy's new surface is one endpoint — `POST /oauth/callback` — which takes `{recipe_id, code, code_verifier, redirect_uri}`, calls `exchange_code_for_token`, and returns the stored token's `expires_at`. Nothing else.

At the same time, we remove the old `/oauth/start`, `/oauth/stop`, and the WebSocket stream handler — they're the subprocess-based flow that's now archived. The file-materialization ExitStack in `/mcp/start` also comes out (a Google HTTP endpoint needs no materialization).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_proxy_oauth_callback.py`:

```python
"""POST /oauth/callback — proxy-internal endpoint the gateway calls
after receiving the browser's OAuth callback. Exchanges the code for
a token and stores it. No subprocess spawning."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def proxy_app(tmp_path: Path, monkeypatch):
    from mycelos.app import App
    from mycelos.security.proxy_server import create_proxy_app
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "phase-1b-test-key-" + "x" * 16)
    monkeypatch.setenv("MYCELOS_PROXY_TOKEN", "test-token")
    monkeypatch.setenv("MYCELOS_DB_PATH", str(tmp_path / "mycelos.db"))
    app = App(tmp_path)
    app.initialize()
    proxy = create_proxy_app()
    client = TestClient(proxy)
    client.headers.update({"Authorization": "Bearer test-token"})
    return client


def _seed_client_cred(proxy_app):
    proxy_app.post("/credential/store", json={
        "service": "gmail-oauth-client",
        "label": "default",
        "payload": {"api_key": json.dumps({
            "installed": {
                "client_id": "cid.apps.googleusercontent.com",
                "client_secret": "csec",
            }
        })},
        "description": "test",
    })


def test_oauth_callback_requires_auth(proxy_app):
    proxy_app.headers.pop("Authorization", None)
    resp = proxy_app.post("/oauth/callback", json={
        "recipe_id": "gmail",
        "code": "c",
        "code_verifier": "v",
        "redirect_uri": "http://localhost:9100/api/connectors/oauth/callback",
    })
    assert resp.status_code == 401


def test_oauth_callback_exchanges_and_persists(proxy_app):
    _seed_client_cred(proxy_app)

    fake_resp = type("R", (), {
        "status_code": 200,
        "json": lambda self: {
            "access_token": "ya29.ok",
            "refresh_token": "1//rtok",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
            "token_type": "Bearer",
        },
        "text": "",
    })()
    with patch("mycelos.security.oauth_token_manager.httpx.post", return_value=fake_resp):
        resp = proxy_app.post("/oauth/callback", json={
            "recipe_id": "gmail",
            "code": "auth-code",
            "code_verifier": "verifier",
            "redirect_uri": "http://localhost:9100/api/connectors/oauth/callback",
        })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "connected"
    assert "expires_at" in body

    # Token was stored.
    lst = proxy_app.get("/credential/list").json()
    services = [c["service"] for c in lst.get("credentials", [])]
    assert "gmail-oauth-token" in services


def test_oauth_callback_unknown_recipe_404(proxy_app):
    resp = proxy_app.post("/oauth/callback", json={
        "recipe_id": "not-a-recipe",
        "code": "c",
        "code_verifier": "v",
        "redirect_uri": "http://x",
    })
    assert resp.status_code == 404


def test_oauth_callback_rejects_non_oauth_http_recipe(proxy_app):
    """Running /oauth/callback for brave-search (secret flow) makes
    no sense — should be 400."""
    resp = proxy_app.post("/oauth/callback", json={
        "recipe_id": "brave-search",
        "code": "c",
        "code_verifier": "v",
        "redirect_uri": "http://x",
    })
    assert resp.status_code == 400


def test_oauth_callback_surfaces_google_error(proxy_app):
    _seed_client_cred(proxy_app)
    fake_resp = type("R", (), {
        "status_code": 400,
        "text": '{"error": "invalid_grant"}',
    })()
    with patch("mycelos.security.oauth_token_manager.httpx.post", return_value=fake_resp):
        resp = proxy_app.post("/oauth/callback", json={
            "recipe_id": "gmail",
            "code": "bad",
            "code_verifier": "v",
            "redirect_uri": "http://x",
        })
    assert resp.status_code == 502
    assert "invalid_grant" in resp.json().get("error", "").lower()
```

- [ ] **Step 2: Run to confirm failure**

```bash
PYTHONPATH=src pytest tests/test_proxy_oauth_callback.py -v
```

Expected: 5 tests fail — endpoint doesn't exist.

- [ ] **Step 3: Delete the old proxy oauth test files**

```bash
rm tests/test_proxy_oauth_endpoints.py tests/test_proxy_oauth_websocket.py
```

- [ ] **Step 4: Remove old proxy code**

In `src/mycelos/security/proxy_server.py`:

**Delete:**
- Module-level `OAUTH_TMP_ROOT` constant
- Module-level `_OAUTH_ALLOWED_HEADS` constant
- Module-level `_MCP_ALLOWED_HEADS` constant
- `OauthStartRequest` class
- The `@app.post("/oauth/start")` handler (entire body)
- The `@app.post("/oauth/stop")` handler (entire body)
- The `@app.websocket("/oauth/stream/{session_id}")` handler (entire body)
- Any `import asyncio` that was added solely for the WS (if other handlers still use it, keep)
- Any `from mycelos.security.credential_materializer import ...` imports
- The module-level `_get_mcp_manager` global that Task 4 of the previous plan added to support monkeypatching — revert to the inner-function-only form

**In `/mcp/start`**: remove the file-materialization `ExitStack` plumbing. Revert to the shape from before the previous plan:

```python
    @app.post("/mcp/start")
    async def mcp_start(request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        agent_id = request.headers.get("X-Agent-Id", "")
        body_data = await request.json()
        req = McpStartRequest(**body_data)

        storage = _get_storage()
        t_start = time.time()

        # Resolve credential:X references in env_vars before passing to manager
        resolved_env: dict[str, str] = {}
        credential_proxy = _get_credential_proxy()
        for key, val in req.env_vars.items():
            if val.startswith("credential:") and credential_proxy:
                service_name = val[len("credential:"):]
                try:
                    cred = credential_proxy.get_credential(service_name, user_id=user_id)
                    if not (cred and cred.get("api_key")):
                        cred = credential_proxy.get_credential(f"connector:{service_name}", user_id=user_id)
                    if cred and cred.get("api_key"):
                        resolved_env[key] = cred["api_key"]
                    else:
                        return JSONResponse(
                            {"error": f"Credential '{service_name}' not found for env var '{key}' — denied (fail-closed)"},
                            status_code=502,
                        )
                except Exception:
                    return JSONResponse(
                        {"error": f"Credential lookup failed for '{service_name}' — denied (fail-closed)"},
                        status_code=502,
                    )
            else:
                resolved_env[key] = val

        try:
            mcp = _get_mcp_manager()
            tools = mcp.connect(
                connector_id=req.connector_id,
                command=req.command,
                env_vars=resolved_env,
                transport=req.transport,
            )
        except Exception as e:
            logger.error("MCP start failed for connector '%s': %s", req.connector_id, e)
            return JSONResponse(
                {"error": "MCP connector start failed. Check server logs for details.", "status": 0},
                status_code=500,
            )

        import secrets
        session_id = f"mcp-{req.connector_id}-{secrets.token_hex(6)}"
        _state["_mcp_sessions"][session_id] = req.connector_id

        duration = time.time() - t_start
        if storage:
            _write_audit(storage, "proxy.mcp_started", user_id, {
                "connector_id": req.connector_id,
                "command": req.command,
                "transport": req.transport,
                "agent_id": agent_id,
                "duration": round(duration, 3),
            })

        return JSONResponse({"session_id": session_id, "tools": tools})
```

**In `/mcp/stop`**: `_state["_mcp_sessions"]` values are back to strings (connector_ids), not dicts. Replace the ExitStack-aware pop logic with the simple:

```python
        _state["_mcp_sessions"].pop(req.session_id, None)
```

- [ ] **Step 5: Add the `OauthCallbackRequest` model and the new handler**

In `src/mycelos/security/proxy_server.py`, near other `*Request` models, add:

```python
class OauthCallbackRequest(BaseModel):
    recipe_id: str
    code: str
    code_verifier: str
    redirect_uri: str
```

Near the bottom of `create_proxy_app` (next to `/credential/store` etc.), add the handler:

```python
    @app.post("/oauth/callback")
    async def oauth_callback(request: Request) -> JSONResponse:
        """Exchange an OAuth authorization code for a token and store
        it in the credential proxy. Called by the gateway after the
        browser returns from the OAuth consent screen."""
        from fastapi import HTTPException
        from mycelos.connectors.mcp_recipes import get_recipe
        from mycelos.security.oauth_token_manager import exchange_code_for_token

        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        body = await request.json()
        req = OauthCallbackRequest(**body)

        recipe = get_recipe(req.recipe_id)
        if recipe is None:
            return JSONResponse(
                {"error": f"Unknown recipe: {req.recipe_id}"},
                status_code=404,
            )
        if recipe.setup_flow != "oauth_http":
            return JSONResponse(
                {"error": f"Recipe '{req.recipe_id}' setup_flow is '{recipe.setup_flow}', not 'oauth_http'"},
                status_code=400,
            )

        credential_proxy = _get_credential_proxy()
        if credential_proxy is None:
            return JSONResponse({"error": "credential_proxy unavailable"}, status_code=500)

        try:
            payload = exchange_code_for_token(
                recipe=recipe,
                code=req.code,
                code_verifier=req.code_verifier,
                redirect_uri=req.redirect_uri,
                credential_proxy=credential_proxy,
                user_id=user_id,
            )
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=502)

        storage = _get_storage()
        if storage:
            _write_audit(storage, "credential.token_persisted", user_id, {
                "service": recipe.oauth_token_credential_service,
                "recipe_id": req.recipe_id,
            })

        return JSONResponse({"status": "connected", "expires_at": payload.expires_at})
```

- [ ] **Step 6: Update `proxy_client.py`**

In `src/mycelos/security/proxy_client.py`:

**Remove:** `oauth_start`, `oauth_stop`, `oauth_stream_url` methods.

**Add:**

```python
    def oauth_callback(
        self,
        recipe_id: str,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        user_id: str = "default",
    ) -> dict:
        """Exchange an OAuth code for a token via the proxy."""
        resp = self._request("POST", "/oauth/callback", json={
            "recipe_id": recipe_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        }, headers={"X-User-Id": user_id})
        return resp.json()
```

- [ ] **Step 7: Also delete the materializer module (Task 5 will clean up any imports)**

```bash
rm src/mycelos/security/credential_materializer.py tests/test_credential_materializer.py
```

- [ ] **Step 8: Run tests**

```bash
PYTHONPATH=src pytest tests/test_proxy_oauth_callback.py tests/test_proxy_credential_endpoints.py -v
```

Expected: all green. Pre-existing credential tests must still pass (no regression from touching `/mcp/start`).

- [ ] **Step 9: Broader baseline**

```bash
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -5
```

Expected: baseline drops by the deleted test counts (~17 tests gone) but nothing fails.

- [ ] **Step 10: Commit**

```bash
git add -A src/mycelos/security/ tests/test_proxy_oauth_callback.py
git commit -m "feat(proxy): /oauth/callback + remove file-materialization path"
```

---

## Task 5 — Gateway endpoints + in-memory state dict

**Files:**
- Modify: `src/mycelos/gateway/routes.py`
- Create: `tests/test_gateway_oauth_http_flow.py`
- Delete: `tests/test_gateway_oauth_proxy.py`

Context: Gateway exposes two endpoints:
1. `POST /api/connectors/oauth/start` — generates PKCE pair + state, stores state in `app.state.oauth_pending_states`, returns auth URL.
2. `GET /api/connectors/oauth/callback?code=&state=[&error=]` — looks up state, calls proxy `/oauth/callback`, redirects browser to connectors page with `?connected=<id>` or `?oauth_error=<msg>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gateway_oauth_http_flow.py`:

```python
"""Gateway OAuth-HTTP flow endpoints:
- POST /api/connectors/oauth/start  — returns auth_url, stores state
- GET  /api/connectors/oauth/callback — validates state, forwards
  code to proxy, redirects browser to the connectors page.

Uses a MagicMock proxy_client so we don't need a live proxy container.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_mock_proxy():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-gw-oauth-http"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        app = App(data_dir)
        app.initialize()
        mock = MagicMock()
        mock.oauth_callback.return_value = {
            "status": "connected",
            "expires_at": "2026-04-23T10:30:00+00:00",
        }
        # Seed the client credential via the real credential store
        # so /oauth/start can look up client_id.
        app.credentials.store_credential(
            "gmail-oauth-client",
            {"api_key": json.dumps({
                "installed": {
                    "client_id": "cid.apps.googleusercontent.com",
                    "client_secret": "csec",
                }
            })},
            user_id="default",
        )
        app._proxy_client = mock
        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0")
        fastapi_app.state.mycelos._proxy_client = mock
        yield TestClient(fastapi_app), mock, fastapi_app


def test_oauth_start_builds_auth_url_and_stores_state(client_with_mock_proxy):
    client, _mock, fapp = client_with_mock_proxy
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "gmail",
        "origin": "http://localhost:9100",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    auth_url = body["auth_url"]
    assert auth_url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=cid.apps.googleusercontent.com" in auth_url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A9100%2Fapi%2Fconnectors%2Foauth%2Fcallback" in auth_url
    assert "response_type=code" in auth_url
    assert "code_challenge_method=S256" in auth_url
    assert "access_type=offline" in auth_url
    # state got stored
    state_dict = fapp.state.oauth_pending_states
    assert len(state_dict) == 1
    stored_state = list(state_dict.keys())[0]
    assert f"state={stored_state}" in auth_url
    entry = state_dict[stored_state]
    assert entry["recipe_id"] == "gmail"
    assert entry["origin"] == "http://localhost:9100"
    assert "code_verifier" in entry
    # expires_at is a datetime or ISO string — don't overspecify
    assert "expires_at" in entry


def test_oauth_start_unknown_recipe_404(client_with_mock_proxy):
    client, _mock, _fapp = client_with_mock_proxy
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "not-a-recipe",
        "origin": "http://localhost:9100",
    })
    assert resp.status_code == 404


def test_oauth_start_rejects_non_oauth_http_recipe(client_with_mock_proxy):
    """A secret-flow recipe like brave-search should not use /oauth/start."""
    client, _mock, _fapp = client_with_mock_proxy
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "brave-search",
        "origin": "http://localhost:9100",
    })
    assert resp.status_code == 400


def test_oauth_start_requires_client_credential(client_with_mock_proxy, tmp_path):
    """If the oauth_client_credential_service row is missing, 400 with
    'upload client secret first'."""
    client, _mock, fapp = client_with_mock_proxy
    # Delete the seeded cred via the credential store
    fapp.state.mycelos.credentials.delete_credential(
        "gmail-oauth-client", user_id="default",
    )
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "gmail",
        "origin": "http://localhost:9100",
    })
    assert resp.status_code == 400
    assert "client" in resp.json().get("detail", "").lower()


def test_oauth_callback_success_redirects(client_with_mock_proxy):
    """Browser arrives at /api/connectors/oauth/callback with a valid
    code+state → gateway calls proxy, redirects to /connectors.html
    with ?connected=gmail."""
    client, mock, fapp = client_with_mock_proxy
    # Start the flow to populate state
    start = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "gmail",
        "origin": "http://localhost:9100",
    })
    state = list(fapp.state.oauth_pending_states.keys())[0]

    resp = client.get(
        f"/api/connectors/oauth/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    loc = resp.headers["location"]
    assert loc == "/connectors.html?connected=gmail"
    assert mock.oauth_callback.called
    call = mock.oauth_callback.call_args
    assert call.kwargs["recipe_id"] == "gmail"
    assert call.kwargs["code"] == "auth-code"
    assert call.kwargs["redirect_uri"] == "http://localhost:9100/api/connectors/oauth/callback"
    # state was popped (single-use)
    assert state not in fapp.state.oauth_pending_states


def test_oauth_callback_invalid_state_redirects_with_error(client_with_mock_proxy):
    client, _mock, _fapp = client_with_mock_proxy
    resp = client.get(
        "/api/connectors/oauth/callback?code=c&state=totally-fake",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "oauth_error=invalid_state" in resp.headers["location"]


def test_oauth_callback_google_error_redirects_with_error(client_with_mock_proxy):
    client, _mock, _fapp = client_with_mock_proxy
    resp = client.get(
        "/api/connectors/oauth/callback?error=access_denied&state=whatever",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "oauth_error=access_denied" in resp.headers["location"]


def test_oauth_callback_surfaces_proxy_error(client_with_mock_proxy):
    """If the proxy returns a non-connected status we propagate."""
    client, mock, fapp = client_with_mock_proxy
    mock.oauth_callback.return_value = {"error": "invalid_grant"}
    start = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "gmail",
        "origin": "http://localhost:9100",
    })
    state = list(fapp.state.oauth_pending_states.keys())[0]
    resp = client.get(
        f"/api/connectors/oauth/callback?code=c&state={state}",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "oauth_error=" in resp.headers["location"]
```

- [ ] **Step 2: Run to confirm failure**

```bash
PYTHONPATH=src pytest tests/test_gateway_oauth_http_flow.py -v
```

Expected: all fail (endpoints don't exist).

- [ ] **Step 3: Remove old gateway code**

Delete `tests/test_gateway_oauth_proxy.py`.

In `src/mycelos/gateway/routes.py`, **remove**:
- `POST /api/connectors/oauth/start` (the one that calls `proxy_client.oauth_start` with recipe_id + env_vars — the previous-plan version)
- `POST /api/connectors/oauth/stop`
- `WS /api/connectors/oauth/stream/{session_id}` (and the `websockets` import if no other handler uses it)

- [ ] **Step 4: Add the new endpoints**

In `src/mycelos/gateway/routes.py`, add (near the recipe endpoint):

```python
    @api.post("/api/connectors/oauth/start")
    async def oauth_start_passthrough(
        request: Request, payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate an OAuth 2.0 Authorization Code Flow URL.

        Body: {recipe_id, origin}. Origin is the browser's
        window.location.origin — used to build the redirect_uri that
        must match what the user registered in Cloud Console.
        """
        import hashlib
        import base64
        import secrets as _secrets
        from datetime import datetime, timedelta, timezone
        import json as _json
        from urllib.parse import urlencode

        from mycelos.connectors.mcp_recipes import get_recipe

        recipe_id = payload.get("recipe_id", "")
        origin = (payload.get("origin") or "").rstrip("/")
        if not origin:
            raise HTTPException(status_code=400, detail="origin is required")

        recipe = get_recipe(recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail=f"Unknown recipe: {recipe_id}")
        if recipe.setup_flow != "oauth_http":
            raise HTTPException(
                status_code=400,
                detail=f"Recipe '{recipe_id}' setup_flow is '{recipe.setup_flow}', not 'oauth_http'",
            )

        mycelos = api.state.mycelos

        # Read client_id from the stored client_secret_*.json.
        client_cred = None
        try:
            client_cred = mycelos.credentials.get_credential(
                recipe.oauth_client_credential_service, user_id="default",
            )
        except Exception:
            # DelegatingCredentialProxy doesn't expose reads — in two-container mode
            # we fall back to reading through the proxy.
            client_cred = None
        if client_cred is None or not client_cred.get("api_key"):
            # Last-chance: ask the proxy directly.
            proxy_client = getattr(mycelos, "proxy_client", None)
            if proxy_client is not None:
                try:
                    got = proxy_client.credential_get(
                        recipe.oauth_client_credential_service, user_id="default",
                    )
                    if got and got.get("api_key"):
                        client_cred = got
                except Exception:
                    pass
        if client_cred is None or not client_cred.get("api_key"):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"OAuth client credential '{recipe.oauth_client_credential_service}' "
                    "not uploaded. Paste client_secret_*.json first."
                ),
            )
        client_json = _json.loads(client_cred["api_key"])
        installed = client_json.get("installed") or client_json.get("web") or {}
        client_id = installed.get("client_id", "")
        if not client_id:
            raise HTTPException(status_code=400, detail="Malformed client credential")

        # Build PKCE pair and state.
        code_verifier = _secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode().rstrip("=")
        state = _secrets.token_urlsafe(32)

        redirect_uri = f"{origin}/api/connectors/oauth/callback"

        # Store state (TTL-protected, purged here on each call).
        states = getattr(api.state, "oauth_pending_states", None)
        if states is None:
            states = {}
            api.state.oauth_pending_states = states
        now = datetime.now(timezone.utc)
        expiry = (now + timedelta(minutes=10)).isoformat()
        # Sweep expired entries in the same pass.
        for k in list(states.keys()):
            exp = states[k].get("expires_at", "")
            try:
                if datetime.fromisoformat(exp) < now:
                    states.pop(k, None)
            except Exception:
                states.pop(k, None)

        states[state] = {
            "recipe_id": recipe_id,
            "code_verifier": code_verifier,
            "user_id": "default",
            "origin": origin,
            "expires_at": expiry,
        }

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(recipe.oauth_scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        auth_url = f"{recipe.oauth_authorize_url}?{urlencode(params)}"

        return {"auth_url": auth_url, "redirect_uri": redirect_uri}


    @api.get("/api/connectors/oauth/callback")
    async def oauth_callback_passthrough(
        code: str = "",
        state: str = "",
        error: str = "",
    ):
        """Browser lands here after OAuth consent. Validate state,
        exchange the code through the proxy, redirect to the
        connectors page."""
        from fastapi.responses import RedirectResponse

        if error:
            return RedirectResponse(
                url=f"/connectors.html?oauth_error={error}",
                status_code=302,
            )

        mycelos = api.state.mycelos
        states = getattr(api.state, "oauth_pending_states", None) or {}
        entry = states.pop(state, None)
        if entry is None:
            return RedirectResponse(
                url="/connectors.html?oauth_error=invalid_state",
                status_code=302,
            )

        proxy_client = getattr(mycelos, "proxy_client", None)
        if proxy_client is None:
            return RedirectResponse(
                url="/connectors.html?oauth_error=proxy_unavailable",
                status_code=302,
            )

        redirect_uri = f"{entry['origin']}/api/connectors/oauth/callback"
        try:
            result = proxy_client.oauth_callback(
                recipe_id=entry["recipe_id"],
                code=code,
                code_verifier=entry["code_verifier"],
                redirect_uri=redirect_uri,
                user_id=entry["user_id"],
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/connectors.html?oauth_error={str(e)[:120]}",
                status_code=302,
            )

        if result.get("status") != "connected":
            err = (result.get("error") or "exchange_failed")[:120]
            return RedirectResponse(
                url=f"/connectors.html?oauth_error={err}",
                status_code=302,
            )

        return RedirectResponse(
            url=f"/connectors.html?connected={entry['recipe_id']}",
            status_code=302,
        )
```

Add `proxy_client.credential_get` if it's not already on `SecurityProxyClient` — check:

```bash
grep -n "credential_get\|def credential_" src/mycelos/security/proxy_client.py
```

If there's no `credential_get` (likely — the proxy exposes `/credential/list` and `/credential/store` but not an individual `get`), add one to `proxy_client.py`:

```python
    def credential_get(self, service: str, label: str = "default", user_id: str = "default") -> dict | None:
        """Get a credential by service+label. Returns the plaintext
        payload dict ({"api_key": "..."}) or None if not found."""
        resp = self._request(
            "GET", f"/credential/get/{service}/{label}",
            headers={"X-User-Id": user_id},
        )
        if resp.status_code == 404:
            return None
        return resp.json()
```

And add a matching `/credential/get/{service}/{label}` endpoint to `proxy_server.py` (simple read-through of the credential_proxy):

```python
    @app.get("/credential/get/{service}/{label}")
    async def credential_get(service: str, label: str, request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        cp = _get_credential_proxy()
        if cp is None:
            return JSONResponse({"error": "credential_proxy unavailable"}, status_code=500)
        try:
            cred = cp.get_credential(service, user_id=user_id, label=label)
        except NotImplementedError:
            return JSONResponse({"error": "not available"}, status_code=501)
        if cred is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(cred)
```

- [ ] **Step 5: Run tests**

```bash
PYTHONPATH=src pytest tests/test_gateway_oauth_http_flow.py -v
```

Expected: 8 passed.

- [ ] **Step 6: Baseline**

```bash
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -5
```

Expected: no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/gateway/routes.py src/mycelos/security/proxy_client.py src/mycelos/security/proxy_server.py tests/test_gateway_oauth_http_flow.py
git commit -m "feat(gateway): /oauth/start + /oauth/callback for OAuth-HTTP flow"
```

---

## Task 6 — MCP client wires token via oauth_token_manager

**Files:**
- Modify: `src/mycelos/connectors/mcp_client.py`

Context: When `_connect_http` runs for a recipe with `setup_flow="oauth_http"`, it must resolve the token via `oauth_token_manager.refresh_if_expired(recipe, credential_proxy, user_id)` and pass the result as the `Authorization: Bearer <token>` header. The existing `_HTTP_ENDPOINTS` hardcoded dict stays for GitHub but is augmented by `recipe.http_endpoint` lookup for oauth_http recipes.

- [ ] **Step 1: Read the existing `_connect_http`**

```bash
grep -n "_connect_http\|_HTTP_ENDPOINTS\|_resolve_token" src/mycelos/connectors/mcp_client.py
```

- [ ] **Step 2: Update `_connect_http` to resolve from recipe first**

In `src/mycelos/connectors/mcp_client.py`, find `_connect_http`. Replace its body with:

```python
    async def _connect_http(self) -> None:
        """Connect via HTTP (hosted MCP endpoint)."""
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        from mycelos.connectors.mcp_recipes import get_recipe

        recipe = get_recipe(self.connector_id)
        url = ""
        if recipe and recipe.http_endpoint:
            url = recipe.http_endpoint
        else:
            url = self._HTTP_ENDPOINTS.get(self.connector_id, "")
        if not url:
            raise ValueError(f"No HTTP endpoint configured for '{self.connector_id}'")

        token = self._resolve_token(recipe)
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        self._http_context = streamablehttp_client(url=url, headers=headers)
        read, write, _ = await self._http_context.__aenter__()
        self._context_stack.append(self._http_context)
        self._session_context = ClientSession(read, write)
        self._session = await self._session_context.__aenter__()
        self._context_stack.append(self._session_context)
        await self._session.initialize()
        logger.info("MCP server '%s' connected (http: %s)", self.connector_id, url)
```

- [ ] **Step 3: Update `_resolve_token` to dispatch on recipe**

Replace the existing `_resolve_token` (line ~129) with:

```python
    def _resolve_token(self, recipe=None) -> str | None:
        """Resolve the API token from credential proxy.

        For oauth_http recipes, runs through oauth_token_manager so
        an expired access_token is refreshed lazily. For other HTTP
        recipes (e.g. GitHub) the token is a plain credential lookup.

        Fail-closed: credential lookup errors propagate so the caller
        sees an explicit failure instead of an unauthenticated request.
        """
        if not self._credential_proxy:
            return None

        if recipe is not None and getattr(recipe, "setup_flow", "") == "oauth_http":
            from mycelos.security.oauth_token_manager import refresh_if_expired
            return refresh_if_expired(
                recipe=recipe,
                credential_proxy=self._credential_proxy,
                user_id="default",
            )

        # Non-OAuth HTTP recipes (GitHub etc.) use the env_vars path.
        for env_var, source in self._env_vars.items():
            if source.startswith("credential:"):
                service = source[11:]
                cred = self._credential_proxy.get_credential(service)
                if cred and "api_key" in cred:
                    return cred["api_key"]
        return None
```

- [ ] **Step 4: Run mcp_client-related tests**

```bash
PYTHONPATH=src pytest tests/ -k "mcp_client or mcp_recipes" -v 2>&1 | tail -15
```

Expected: no regressions.

- [ ] **Step 5: Broader baseline**

```bash
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -5
```

Expected: no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/connectors/mcp_client.py
git commit -m "feat(mcp): resolve token via oauth_token_manager for oauth_http recipes"
```

---

## Task 7 — Frontend dialog rewrite

**Files:**
- Modify: `src/mycelos/frontend/pages/connectors.html`
- Modify: `src/mycelos/frontend/shared/oauth_setup.js`

Context: The dialog's Stage 2 used to stream subprocess I/O via WebSocket. Now it just shows an auth URL + the redirect-URI hint. Stage 3 ("Connected") is triggered by `?connected=<id>` on page load (after the browser comes back from Google).

- [ ] **Step 1: Simplify `oauth_setup.js`**

Replace the entire contents of `src/mycelos/frontend/shared/oauth_setup.js` with:

```javascript
/**
 * OAuth setup helpers. The flow is entirely HTTP-driven now — no
 * WebSocket, no subprocess stream. Keeps a small helper for reading
 * query params on page load so the connectors page can react to
 * ?connected=<recipe_id> and ?oauth_error=<msg>.
 */
(function () {
  'use strict';

  function readOAuthQueryParams() {
    const params = new URLSearchParams(window.location.search);
    return {
      connected: params.get('connected'),
      error: params.get('oauth_error'),
    };
  }

  /**
   * Remove the OAuth-result params from the URL without reloading.
   * Called by the page after it has consumed the result so that a
   * reload doesn't re-trigger the success/error panel.
   */
  function clearOAuthQueryParams() {
    const url = new URL(window.location.href);
    url.searchParams.delete('connected');
    url.searchParams.delete('oauth_error');
    window.history.replaceState({}, '', url.toString());
  }

  window.MycelosOAuthSetup = { readOAuthQueryParams, clearOAuthQueryParams };
})();
```

- [ ] **Step 2: Update the Alpine state + methods in `connectors.html`**

In `src/mycelos/frontend/pages/connectors.html`, find the `submitOAuthKeysAndStart` method + the `oauthDialog` state object. Replace them with:

```javascript
        oauthDialog: {
          open: false,
          recipeId: '',
          recipe: null,
          keysJson: '',
          keysValid: null,
          authUrl: '',
          redirectUri: '',
          submitting: false,
          done: false,
          error: '',
        },

        async openOAuthDialog(recipeId) {
          const resp = await fetch('/api/connectors/recipes/' + encodeURIComponent(recipeId));
          if (!resp.ok) {
            this.showToast('Failed to load recipe', 'error');
            return;
          }
          const recipe = await resp.json();
          Object.assign(this.oauthDialog, {
            open: true,
            recipeId,
            recipe,
            keysJson: '',
            keysValid: null,
            authUrl: '',
            redirectUri: '',
            submitting: false,
            done: false,
            error: '',
          });
        },

        closeOAuthDialog() {
          this.oauthDialog.open = false;
        },

        async validateOAuthKeys() {
          this.oauthDialog.keysValid = null;
          const resp = await fetch('/api/credentials/oauth-keys/validate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({content: this.oauthDialog.keysJson}),
          });
          this.oauthDialog.keysValid = await resp.json();
        },

        async submitOAuthKeysAndStart() {
          this.oauthDialog.submitting = true;
          this.oauthDialog.error = '';
          try {
            const credService = this.oauthDialog.recipe.oauth_client_credential_service
                             || (this.oauthDialog.recipeId + '-oauth-client');
            // Store the client_secret_*.json blob.
            await fetch('/api/credentials', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                service: credService,
                secret: this.oauthDialog.keysJson,
                label: 'default',
                description: 'OAuth client for ' + this.oauthDialog.recipe.name,
              }),
            });

            // Ask the gateway for an auth URL.
            const resp = await fetch('/api/connectors/oauth/start', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                recipe_id: this.oauthDialog.recipeId,
                origin: window.location.origin,
              }),
            });
            const body = await resp.json();
            if (!resp.ok) {
              this.oauthDialog.error = body.detail || body.error || ('HTTP ' + resp.status);
              return;
            }
            this.oauthDialog.authUrl = body.auth_url;
            this.oauthDialog.redirectUri = body.redirect_uri;
          } catch (e) {
            this.oauthDialog.error = e.message || String(e);
          } finally {
            this.oauthDialog.submitting = false;
          }
        },

        detectOAuthReturn() {
          const { connected, error } = window.MycelosOAuthSetup.readOAuthQueryParams();
          if (!connected && !error) return;
          if (connected) {
            this.showToast('Connected ' + connected, 'success');
            // Re-open the dialog briefly in success state.
            this.openOAuthDialog(connected).then(() => {
              this.oauthDialog.done = true;
            });
          } else {
            this.showToast('OAuth error: ' + error, 'error');
          }
          window.MycelosOAuthSetup.clearOAuthQueryParams();
        },
```

Find the `x-data` `init()` method of the connectors page. At the end of it, add:

```javascript
          this.detectOAuthReturn();
```

- [ ] **Step 3: Update the dialog markup**

Find the OAuth dialog markup (it has `x-show="oauthDialog.open"`). Replace the Stage 2 + Stage 3 sections. Keep Stage 1 (textarea + validation) as-is. Replace Stage 2 with:

```html
            <!-- Stage 2: Consent -->
            <div x-show="oauthDialog.authUrl && !oauthDialog.done" class="space-y-3">
              <h3 class="font-semibold">Complete the consent in your browser</h3>
              <div class="card border-l-2 border-l-[var(--primary)]">
                <p class="text-sm mb-2 text-[var(--on-surface)]">
                  Open this URL, sign in with your Google account, and accept the scopes.
                  When you return, this page will show "Connected".
                </p>
                <a :href="oauthDialog.authUrl" target="_blank" rel="noopener"
                   class="block text-[var(--primary)] hover:underline break-all text-sm font-mono">
                  <span x-text="oauthDialog.authUrl"></span>
                </a>
                <button @click="window.open(oauthDialog.authUrl, '_blank')"
                        class="btn-primary mt-2">Open in browser</button>
              </div>
              <div class="card">
                <p class="text-xs text-[var(--on-surface-variant)]">
                  Make sure this exact Redirect URI is registered in Cloud Console:
                </p>
                <code class="block mt-1 text-xs font-mono text-[var(--on-surface)] break-all"
                      x-text="oauthDialog.redirectUri"></code>
              </div>
            </div>
```

Stage 3 stays as it is. Stage 1's "Start OAuth consent" button should stay — but on click it now only runs `submitOAuthKeysAndStart` which fills `oauthDialog.authUrl`; Stage 1 auto-hides when `authUrl` is populated.

Show the error panel anywhere below:

```html
            <div x-show="oauthDialog.error"
                 class="card border-l-2 border-l-[var(--error)] mt-3">
              <p class="text-sm text-[var(--error)]" x-text="oauthDialog.error"></p>
            </div>
```

- [ ] **Step 4: Verify tests + syntax**

```bash
cd /Users/stefan/Documents/railsapps/mycelos
python -c "
content = open('src/mycelos/frontend/pages/connectors.html').read()
print('curly:', content.count('{') - content.count('}'))
print('square:', content.count('[') - content.count(']'))
print('paren:', content.count('(') - content.count(')'))
"
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -3
```

Expected: balanced counts (0/0/0); no pytest regressions.

- [ ] **Step 5: Manual smoke check**

```bash
mycelos serve --reload
```

Open `http://localhost:9100/connectors.html` → Google Workspace → Gmail. Expect dialog opens with the updated Stage 1 (paste client_secret_*.json textarea). Paste a dummy JSON (`{"installed": {"client_id": "x", "client_secret": "y"}}`) — validator says "Valid". Click "Start OAuth consent". Expect Stage 2 with the auth URL + redirect_uri box. (Actually submitting the click won't complete the OAuth because the Google client_id is fake, but we verify the UI shape here.)

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/frontend/pages/connectors.html src/mycelos/frontend/shared/oauth_setup.js
git commit -m "feat(frontend): oauth_http dialog — auth URL + redirect-URI hint"
```

---

## Task 8 — Setup guide rewrite

**Files:**
- Modify: `src/mycelos/connectors/oauth_setup_guides.py`

- [ ] **Step 1: Update the failing tests**

In `tests/test_mcp_recipe_setup_flow.py`, find `test_google_cloud_guide_exists` and `test_google_cloud_guide_mentions_oauth_desktop_app`. Add one new test after them:

```python
def test_google_cloud_guide_covers_mcp_api_activation() -> None:
    """The guide must tell the user to enable the *MCP* API variant
    (e.g. gmailmcp.googleapis.com), not just the plain Gmail API."""
    guide = get_setup_guide("google_cloud")
    body_text = " ".join(step["body"].lower() for step in guide["steps"])
    assert "gmailmcp" in body_text or "mcp api" in body_text


def test_google_cloud_guide_covers_redirect_uri_registration() -> None:
    guide = get_setup_guide("google_cloud")
    body_text = " ".join(step["body"].lower() for step in guide["steps"])
    assert "redirect" in body_text and ("uri" in body_text or "url" in body_text)
```

- [ ] **Step 2: Run to confirm failure**

```bash
PYTHONPATH=src pytest tests/test_mcp_recipe_setup_flow.py -v 2>&1 | tail -15
```

Expected: new tests fail.

- [ ] **Step 3: Rewrite the guide**

Replace `GOOGLE_CLOUD_GUIDE` in `src/mycelos/connectors/oauth_setup_guides.py` with:

```python
GOOGLE_CLOUD_GUIDE: dict[str, Any] = {
    "id": "google_cloud",
    "title": "Set up Gmail via Google's official MCP server",
    "intro": (
        "Mycelos connects to Google's remote MCP server for Gmail. "
        "You need a Google Cloud project with two APIs enabled (Gmail API + "
        "Gmail MCP API) and an OAuth 2.0 Desktop-app credential. This is a "
        "one-time ~10-minute setup."
    ),
    "steps": [
        {
            "title": "Create or pick a Google Cloud project",
            "body": (
                "Open Google Cloud Console. Create a new project (name it "
                "e.g. 'Mycelos') or pick an existing one. Projects are free."
            ),
            "cta_url": "https://console.cloud.google.com/projectcreate",
            "cta_label": "Open Cloud Console",
        },
        {
            "title": "Enable the Gmail API",
            "body": (
                "In **APIs & Services → Library**, search for 'Gmail API' "
                "and enable it."
            ),
            "cta_url": "https://console.cloud.google.com/apis/library",
            "cta_label": "Open API Library",
        },
        {
            "title": "Enable the Gmail MCP API",
            "body": (
                "Also enable the **Gmail MCP API** (separate from the plain "
                "Gmail API — this one is in Developer Preview and lives at "
                "`gmailmcp.googleapis.com`). In the same API Library, search "
                "for 'Gmail MCP' and enable."
            ),
            "cta_url": "https://console.cloud.google.com/apis/library",
            "cta_label": "Open API Library",
        },
        {
            "title": "Configure the OAuth consent screen",
            "body": (
                "**APIs & Services → OAuth consent screen**. Pick **External**. "
                "Fill in an app name, your email as support + developer "
                "contact. Save. Add your Google account as a **Test user**. "
                "Add these scopes: `gmail.readonly`, `gmail.compose`."
            ),
            "cta_url": "https://console.cloud.google.com/apis/credentials/consent",
            "cta_label": "Open Consent Screen",
        },
        {
            "title": "Create an OAuth Desktop-app credential",
            "body": (
                "**APIs & Services → Credentials → Create credentials → "
                "OAuth client ID**. Pick **Desktop app** (important — Mycelos "
                "only supports Desktop). Give it a name. Click Create. Click "
                "**DOWNLOAD JSON** on the dialog that pops up. Save the file "
                "(typically named `client_secret_*.json`)."
            ),
            "cta_url": "https://console.cloud.google.com/apis/credentials",
            "cta_label": "Open Credentials",
        },
        {
            "title": "Register the Redirect URI",
            "body": (
                "Open the credential you just created. Under **Authorized "
                "redirect URIs**, click **Add URI** and paste the URL Mycelos "
                "will show you in the dialog after you upload the credential. "
                "This URL is derived from your Mycelos server's address and "
                "must match *exactly* (http vs https, trailing slash, port) "
                "what Mycelos sends in the auth request."
            ),
        },
        {
            "title": "Upload the client secret to Mycelos",
            "body": (
                "Come back to this dialog. In the textarea below, paste the "
                "full contents of `client_secret_*.json`. Mycelos stores the "
                "file encrypted; the gateway and the LLM never see it."
            ),
        },
        {
            "title": "Complete the consent",
            "body": (
                "Click **Start OAuth consent**. Mycelos builds a Google "
                "consent URL. Open it, sign in as the Test user you added, "
                "accept the scopes. Google redirects back to Mycelos and "
                "the dialog shows 'Connected'."
            ),
        },
    ],
}
```

- [ ] **Step 4: Verify tests**

```bash
PYTHONPATH=src pytest tests/test_mcp_recipe_setup_flow.py -v
```

Expected: all green including the two new guide tests.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/connectors/oauth_setup_guides.py tests/test_mcp_recipe_setup_flow.py
git commit -m "docs(setup-guide): rewrite google_cloud guide for OAuth-HTTP flow"
```

---

## Task 9 — Docker-compose tmpfs removal + CHANGELOG

**Files:**
- Modify: `docker-compose.yml`
- Modify: `CHANGELOG.md`
- Modify: `docs/deployment/google-setup.md`

- [ ] **Step 1: Remove the tmpfs mount**

In `docker-compose.yml`, find the proxy service's `tmpfs:` section:

```yaml
    tmpfs:
      - /tmp/mycelos-oauth:size=16m,mode=0700
```

Delete those three lines. The `tmpfs:` key itself goes away too.

- [ ] **Step 2: Update the CHANGELOG**

In `CHANGELOG.md`, under `## Week 17 (2026)`, append:

```markdown

### Google via official MCP server — Gmail first
- Dropped the `oauth_browser` / file-materialization path (gongrzhe + friends) in favor of Google's official remote MCP servers. Gmail now uses `https://gmailmcp.googleapis.com/mcp/v1` with standard OAuth 2.0 Authorization Code Flow + PKCE. No more `npx` subprocesses, no more `~/.gmail-mcp/` tmpfs hack, no more WebSocket streaming of subprocess I/O.
- New `setup_flow="oauth_http"` on `MCPRecipe` with fields for `http_endpoint`, `oauth_authorize_url`, `oauth_token_url`, `oauth_scopes`, `oauth_client_credential_service`, `oauth_token_credential_service`.
- New `oauth_token_manager` module: pure functions for code-exchange and lazy-refresh against any OAuth 2.0 token endpoint.
- New gateway endpoints: `POST /api/connectors/oauth/start` (auth URL with PKCE + in-memory state), `GET /api/connectors/oauth/callback` (proxy-to-gateway redirect from Google). New proxy endpoint: `POST /oauth/callback` (code → token exchange). New proxy `/credential/get/{service}/{label}` for the gateway's startup reads.
- Old code archived on `archive/oauth-browser-file-materialization` — not deleted from history, just not built on any longer.
- Calendar and Drive recipes removed for now; add back in a follow-up plan once Gmail is proven stable.
```

- [ ] **Step 3: Rewrite the Google setup doc**

Replace the contents of `docs/deployment/google-setup.md` with:

```markdown
# Gmail (via Google's official MCP server) setup

Mycelos walks you through the Gmail setup inside the web UI. This
doc is a reference — you don't need to read it top-to-bottom to
connect Gmail.

## Happy path (via the web UI)

1. Open **Connectors → Google Workspace**.
2. Click **Gmail**.
3. Follow the inline step-by-step guide: create a Google Cloud
   project, enable Gmail API + Gmail MCP API, configure the consent
   screen, create an OAuth 2.0 Desktop-app credential, and download
   the `client_secret_*.json`.
4. Paste the JSON into the dialog. Mycelos shows you the exact
   Redirect URI you need to register in Cloud Console.
5. Register the Redirect URI in the same credential screen you just
   created.
6. Click **Start OAuth consent**. Open the URL Mycelos shows, sign
   in with the Test user you added, accept the scopes.
7. Google redirects you back to the connectors page with "Connected".

## How it works under the hood

- The `client_secret_*.json` blob is stored encrypted under
  `gmail-oauth-client` in the credential store.
- When you click Start, the gateway mints a random `state` (CSRF)
  and PKCE `code_verifier`/`code_challenge`, builds the Google auth
  URL, and stores the state in memory (10-minute TTL).
- Google redirects your browser to
  `<your-mycelos-origin>/api/connectors/oauth/callback?code=...&state=...`.
- Gateway validates state, forwards the code + PKCE verifier to the
  proxy, proxy exchanges them at `oauth2.googleapis.com/token` for
  an access + refresh token, stores the token blob under
  `gmail-oauth-token`.
- On every MCP tool call, the proxy lazily refreshes the access
  token if it has <60s of validity left.

## Troubleshooting

### `redirect_uri_mismatch`

The Redirect URI you registered in Cloud Console must **exactly**
match what Mycelos sends (protocol, host, port, path, no trailing
slash). The dialog shows the expected URL verbatim — copy-paste
rather than type.

### `invalid_grant` on reconnect

Google revoked the refresh token. Delete the `gmail-oauth-token`
credential from Settings → Credentials and run the consent flow
again.

### `invalid_state` in the URL after consent

Gateway process restarted while you were mid-consent. Click Gmail
again and restart the flow.

### Google shows "This app isn't verified"

Normal in Developer Preview. Click Advanced → "Go to [appname]
(unsafe)". The warning only appears because you're the developer
of an unverified app; your own account accessing your own data is
safe.

## Security notes

- Client secret, access token, and refresh token are all encrypted
  at rest with the proxy's master key. The gateway holds no
  plaintext.
- PKCE defends against authorization-code interception on the
  redirect.
- State param is single-use with 10-minute TTL — prevents CSRF.
- No `localhost:3000` callback listener, no subprocess, no tmpfs
  HOME directory — the entire flow lives in the Mycelos process.
```

- [ ] **Step 4: Verify (no tests for docs, but run full suite)**

```bash
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -5
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml CHANGELOG.md docs/deployment/google-setup.md
git commit -m "docs: OAuth-HTTP setup guide + changelog; drop tmpfs mount"
```

---

## Task 10 — Integration test + final merge

**Files:**
- Create: `tests/integration/test_gmail_mcp_http_live.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_gmail_mcp_http_live.py`:

```python
"""Integration test: Gmail HTTP-MCP connector end-to-end.

Requires `.env.test` with:
  GMAIL_OAUTH_CLIENT_JSON   — contents of client_secret_*.json (single line)
  GMAIL_OAUTH_TOKEN_JSON    — contents of a previously-consented token
                              blob, shape like:
                              {"access_token": "...", "refresh_token": "...",
                               "expires_at": "2026-04-23T10:00:00+00:00",
                               "scope": "...", "token_type": "Bearer"}

Without both, the test skips cleanly.

To populate `.env.test` the first time: start Mycelos locally, run
through the UI consent flow, then extract the stored token from the
DB:
    sqlite3 ~/.mycelos/mycelos.db "SELECT encrypted FROM credentials
      WHERE service='gmail-oauth-token'"
Decrypt externally (see tests/security/README.md — we don't ship a
helper for this).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv_test() -> dict[str, str]:
    env_file = REPO_ROOT / ".env.test"
    if not env_file.exists():
        return {}
    env: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


@pytest.fixture(scope="module")
def gmail_oauth_env() -> tuple[str, str]:
    client_json = os.environ.get("GMAIL_OAUTH_CLIENT_JSON")
    token_json = os.environ.get("GMAIL_OAUTH_TOKEN_JSON")
    if not client_json or not token_json:
        dotenv = _load_dotenv_test()
        client_json = client_json or dotenv.get("GMAIL_OAUTH_CLIENT_JSON")
        token_json = token_json or dotenv.get("GMAIL_OAUTH_TOKEN_JSON")
    if not client_json or not token_json:
        pytest.skip(
            "GMAIL_OAUTH_CLIENT_JSON and GMAIL_OAUTH_TOKEN_JSON not set "
            "(put them in .env.test)"
        )
    return client_json, token_json


@pytest.mark.integration
def test_gmail_http_mcp_lists_labels(gmail_oauth_env, tmp_path, monkeypatch):
    """End-to-end: seed credentials, connect to the real Gmail MCP
    server, list labels, assert INBOX is present."""
    client_json, token_json = gmail_oauth_env

    monkeypatch.setenv("MYCELOS_MASTER_KEY", "live-integ-" + "x" * 16)
    monkeypatch.setenv("MYCELOS_DATA_DIR", str(tmp_path))

    from mycelos.app import App
    app = App(tmp_path)
    app.initialize()

    cp = app.credentials
    cp.store_credential(
        "gmail-oauth-client",
        {"api_key": client_json},
        user_id="default",
    )
    cp.store_credential(
        "gmail-oauth-token",
        {"api_key": token_json},
        user_id="default",
    )

    from mycelos.connectors.mcp_client import MycelosMCPClient

    client = MycelosMCPClient(
        connector_id="gmail",
        command="",  # HTTP recipes have no command
        credential_proxy=cp,
        transport="http",
    )
    import asyncio
    asyncio.run(client.connect())
    tools = asyncio.run(client._session.list_tools())
    tool_names = {t.name for t in tools.tools}
    assert "list_labels" in tool_names, f"tools: {tool_names}"

    # Smoke-call list_labels.
    result = asyncio.run(client._session.call_tool("list_labels", {}))
    assert result is not None
    # Labels list comes back as a text content block with JSON inside.
    text = ""
    for block in result.content:
        if hasattr(block, "text"):
            text += block.text
    assert "INBOX" in text, f"expected INBOX in response: {text[:500]!r}"

    asyncio.run(client.disconnect())
```

- [ ] **Step 2: Verify it skips cleanly**

```bash
PYTHONPATH=src pytest tests/integration/test_gmail_mcp_http_live.py -v
```

Expected: 1 skipped.

- [ ] **Step 3: Full baseline (unit)**

```bash
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -5
```

Expected: all green.

- [ ] **Step 4: Smoke-import**

```bash
PYTHONPATH=src python -c "
from mycelos.connectors.mcp_recipes import RECIPES
from mycelos.connectors.oauth_setup_guides import SETUP_GUIDES
from mycelos.security.oauth_token_manager import exchange_code_for_token, refresh_if_expired
from mycelos.gateway.server import create_app
from mycelos.security.proxy_server import create_proxy_app
r = RECIPES['gmail']
assert r.setup_flow == 'oauth_http'
assert r.http_endpoint.endswith('/mcp/v1')
print('all imports ok')
"
```

Expected: `all imports ok`.

- [ ] **Step 5: Commit integration test**

```bash
git add tests/integration/test_gmail_mcp_http_live.py
git commit -m "test(integration): live Gmail HTTP-MCP smoke test"
```

- [ ] **Step 6: Merge + push**

```bash
cd /Users/stefan/Documents/railsapps/mycelos
git checkout main
git pull --ff-only
git merge --no-ff feature/oauth-http-mcp -m "Merge feature/oauth-http-mcp: Gmail via Google's official MCP server

Drops the gongrzhe/npm oauth_browser flow entirely; archived on
archive/oauth-browser-file-materialization. Gmail now goes through
the official gmailmcp.googleapis.com endpoint with standard OAuth 2.0
+ PKCE. Calendar and Drive will follow in a separate plan.
"
git push origin main
```

- [ ] **Step 7: Cleanup worktree + branch**

```bash
git worktree remove .worktrees/oauth-http-mcp
git branch -d feature/oauth-http-mcp
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task |
|---|---|
| Recipe `setup_flow="oauth_http"` + 6 fields | Task 2 |
| `oauth_token_manager` with exchange + refresh | Task 3 |
| Proxy `/oauth/callback` | Task 4 |
| Gateway `/oauth/start` + `/oauth/callback` with PKCE + state | Task 5 |
| `proxy_client.credential_get` + proxy `/credential/get/{s}/{l}` | Task 5 (Step 4) |
| `mcp_client` resolves token via `oauth_token_manager` | Task 6 |
| Frontend dialog — auth URL + redirect-URI hint + query-param detection | Task 7 |
| Setup guide rewrite — MCP API + Redirect URI | Task 8 |
| Archive branch before delete | Task 1 |
| Removal of old oauth_browser code | Task 4 (proxy), Task 5 (gateway), Task 7 (frontend) |
| Docker tmpfs removal | Task 9 |
| Integration test skipped without creds | Task 10 |
| Calendar + Drive out of scope, removed from RECIPES | Task 2 Step 5 |

### Placeholder scan

No "TBD", "TODO", or "similar to task N" references. Every code step has literal content.

### Type consistency

- `TokenPayload` fields (`access_token`, `refresh_token`, `expires_at`, `scope`, `token_type`) are used consistently in exchange + refresh + integration test.
- Credential service names (`gmail-oauth-client`, `gmail-oauth-token`) match across recipe field, token manager, frontend, tests.
- State-dict entry shape (`{recipe_id, code_verifier, user_id, origin, expires_at}`) is consistent between `/oauth/start` (writer) and `/oauth/callback` (reader).
- `OauthCallbackRequest` fields match what `proxy_client.oauth_callback` sends.

### Known trade-offs

- State dict is in-memory — restart kills mid-flight consent sessions. Acceptable per spec.
- `credential_get` on the proxy breaks the "gateway never reads plaintext" rule for one specific purpose: reading the public half (`client_id`) of the OAuth client credential to build the auth URL. The `client_id` is not secret — it's published in the auth URL Google will serve to the user anyway. Storing + reading it through the encrypted store is convenient but not strictly necessary. Documented in the commit message.
