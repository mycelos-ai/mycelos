# OAuth-HTTP-MCP for Official Google Connectors — Design Spec

**Status:** Design — awaiting user review.
**Origin:** Discovery that Google ships official remote MCP servers
(`https://gmailmcp.googleapis.com/mcp/v1`, `calendarmcp.googleapis.com`,
`drivemcp.googleapis.com`) in Developer Preview, with standard OAuth 2.0
Authorization Code Flow.
**Supersedes:** The `oauth_browser` / file-materialization path built in the
earlier plan `2026-04-22-file-credential-materialization.md`. That code is
archived on a branch, not removed entirely in case some future non-Google
MCP needs the file-hardcoded pattern again.

---

## Goal

Ship Gmail as a native HTTP-MCP connector authenticated via OAuth 2.0
Authorization Code Flow against `gmailmcp.googleapis.com/mcp/v1`. Pave
the way for Calendar and Drive by generalizing the OAuth machinery —
recipes just declare endpoint + scopes, the flow is reused.

## Non-goals

- Calendar + Drive integration in this spec. Once Gmail works the other
  two are recipe additions; they don't need their own design.
- Keeping the `oauth_browser` / gongrzhe / file-materialization path
  alive in main. It moves to an archive branch.
- Programmatic Redirect-URI registration via Google's Admin API. The
  user enters it in Cloud Console by hand (one-time).

## Architecture

Three MCP servers — Gmail first, Calendar and Drive later — speak with
Google's remote MCP endpoints via HTTP-streamable transport
(`mcp.client.streamable_http`, which is already in our `mcp_client.py`
for GitHub). Auth: OAuth 2.0 Authorization Code Flow with PKCE. The
proxy container holds access-token + refresh-token in the encrypted
credential store; before every MCP call the proxy lazily refreshes the
access-token if it has <60s of validity left.

New `setup_flow="oauth_http"` on `MCPRecipe` discriminates these from
the old `setup_flow="secret"` (plain API-key) recipes. The old
`setup_flow="oauth_browser"` path (file-materialization) is removed
from main and preserved on an archive branch.

User flow, end-to-end:

1. **Setup in Cloud Console** (manual, documented in the in-dialog
   wizard): create project, enable Gmail API + Gmail MCP API,
   configure consent screen, create OAuth 2.0 Desktop-app Client ID,
   add `<mycelos-origin>/api/connectors/oauth/callback` as an
   Authorized redirect URI.
2. **Upload client secret** into the Mycelos dialog (paste the
   `client_secret_*.json` blob; same shape validator we use today).
3. **Click "Start OAuth consent"** → gateway builds Google consent URL
   with PKCE challenge + random state, stores the state in-memory,
   returns the URL.
4. **User consents** in a new browser tab. Google redirects the
   browser back to `<origin>/api/connectors/oauth/callback?code=...&state=...`.
5. Gateway callback handler validates state, forwards code + PKCE
   verifier to proxy, proxy exchanges code for token at
   `https://oauth2.googleapis.com/token`, stores the token blob in
   the credential store.
6. Gateway redirects browser to `/connectors.html?connected=gmail`.
   The connectors page detects the query param and shows the
   "Connected" state.

## Components

### 1. Recipe fields

`src/mycelos/connectors/mcp_recipes.py` — `MCPRecipe` gains:

```python
http_endpoint: str = ""
# e.g. "https://gmailmcp.googleapis.com/mcp/v1"
# Only meaningful when setup_flow == "oauth_http".

oauth_authorize_url: str = ""
# e.g. "https://accounts.google.com/o/oauth2/v2/auth"

oauth_token_url: str = ""
# e.g. "https://oauth2.googleapis.com/token"

oauth_scopes: list[str] = field(default_factory=list)
# e.g. [".../gmail.readonly", ".../gmail.compose"]

oauth_client_credential_service: str = ""
# DB row holding {"api_key": json.dumps(client_secret_json)}.
# Conventionally "<recipe-id>-oauth-client".

oauth_token_credential_service: str = ""
# DB row holding {"api_key": json.dumps({access_token, refresh_token,
# expires_at, scope, token_type})}.
# Conventionally "<recipe-id>-oauth-token".
```

The allowed values of `setup_flow` become `"secret" | "oauth_http"`.
`"oauth_browser"` is removed.

### 2. OAuth token manager

New module `src/mycelos/security/oauth_token_manager.py`. Pure
functions, no state. Works against the credential_proxy interface.

```python
@dataclass
class TokenPayload:
    access_token: str
    refresh_token: str
    expires_at: str   # ISO-8601, UTC
    scope: str
    token_type: str   # usually "Bearer"


def exchange_code_for_token(
    recipe: MCPRecipe,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    credential_proxy,
    user_id: str,
) -> TokenPayload:
    """POST to recipe.oauth_token_url with code+verifier+client
    credentials, returns the parsed token, and stores it as the
    recipe's oauth_token_credential_service row."""


def refresh_if_expired(
    recipe: MCPRecipe,
    credential_proxy,
    user_id: str,
    refresh_threshold_seconds: int = 60,
) -> str:
    """Return a valid access_token for the recipe. Refreshes via
    refresh_token if the stored access_token expires within
    refresh_threshold_seconds. Raises if refresh fails — caller
    should surface 'reconnect required' to the user.
    """
```

Client credentials are read from
`recipe.oauth_client_credential_service`; the stored blob is the
`client_secret_*.json` the user pasted — we extract
`installed.client_id` + `installed.client_secret` on demand.

### 3. Proxy endpoint

`POST /oauth/callback` (not related to the browser callback — that one
is gateway-side). This endpoint is proxy-internal: the gateway calls
it after it has received the browser's callback.

Body: `{recipe_id, code, code_verifier, redirect_uri}`.

Behavior:
1. Look up recipe (`get_recipe(recipe_id)`), reject if not
   `setup_flow="oauth_http"`.
2. Call `exchange_code_for_token` with the credential_proxy.
3. Audit `credential.token_persisted` + `credential.stored`.
4. Return `{status: "connected", expires_at: ...}`.

### 4. Gateway endpoints

Replaces the old `oauth_browser` passthrough:

**`POST /api/connectors/oauth/start`** — body `{recipe_id, origin}`.
- Generates `state` (32 bytes of `secrets.token_urlsafe`), `code_verifier`
  (64 bytes), `code_challenge` = SHA256(verifier) base64url without
  padding.
- Looks up recipe, reads
  `recipe.oauth_client_credential_service` via proxy, extracts
  `client_id` (we need it in the auth URL — the URL itself isn't
  sensitive).
- Builds auth URL: `recipe.oauth_authorize_url?client_id=...&redirect_uri=<origin>/api/connectors/oauth/callback&response_type=code&scope=<space-separated>&state=<state>&code_challenge=<challenge>&code_challenge_method=S256&access_type=offline&prompt=consent`.
- Stores `state → {recipe_id, code_verifier, user_id, origin, expires_at=now+10min}` in an in-memory `dict` attached to `app.state.oauth_pending_states`. No persistence across gateway restarts — if the user is mid-consent when the gateway restarts, they'll see an `invalid_state` error and have to start over. Acceptable trade-off: consent is seconds-to-minutes, restarts are rare.
- Returns `{auth_url}`.

**`GET /api/connectors/oauth/callback?code=<c>&state=<s>&error=<e>`** —
browser lands here.
- Looks up state in the dict. If missing or expired → redirect to
  `/connectors.html?oauth_error=invalid_state`.
- If `error` param present → redirect with `oauth_error=<error>`.
- Builds `redirect_uri = stored_origin + "/api/connectors/oauth/callback"`.
- Calls `proxy_client.oauth_callback(recipe_id, code, code_verifier, redirect_uri)`.
- On success → redirect to `/connectors.html?connected=<recipe_id>`.
- On proxy error → redirect with `oauth_error=<message>`.
- Pops the state entry (single-use).

**No separate `/stop` endpoint.** There's nothing long-lived to stop.

### 5. MCP-Client integration

`src/mycelos/connectors/mcp_client.py` already has
`_connect_http` + `_HTTP_ENDPOINTS`. Extend:
- `_HTTP_ENDPOINTS` populated from
  `RECIPES[connector_id].http_endpoint` when `setup_flow="oauth_http"`.
- `_resolve_token` replaced by a call to
  `oauth_token_manager.refresh_if_expired(recipe, credential_proxy, user_id)`
  when the recipe is `oauth_http`. Non-OAuth HTTP recipes (GitHub)
  keep the old path.

Proxy's `/mcp/start`: for `oauth_http` recipes, no file materialization
(the materializer is gone anyway). Just call `connect` with the
right `connector_id` — the http-client resolves the token itself.

### 6. Frontend

`src/mycelos/frontend/pages/connectors.html`:
- The existing OAuth dialog has three stages (paste keys / consent /
  done). The new `oauth_http` flow uses the same three stages but
  simpler actions:
  - Stage 1: paste JSON (same validator — `/api/credentials/oauth-keys/validate` still accepts desktop-app shape).
  - Stage 2: dialog calls `POST /api/connectors/oauth/start` → receives `{auth_url}` → shows the URL + "Open in browser" button. Also shows the exact `redirect_uri` the user must set in Cloud Console (derived from `window.location.origin`), with a copy button.
  - Stage 3: triggered when the page is loaded with `?connected=gmail` in the URL. Alpine detects it on init, re-opens the dialog in success state.
- Failure: `?oauth_error=<msg>` shows the error panel.
- The `/oauth/stream/{sid}` WebSocket path + subprocess log panel are removed (nothing to stream anymore).

`src/mycelos/frontend/shared/oauth_setup.js` simplifies drastically —
no more WS client, no more `findOAuthUrl` regex. Keeps the helper for
reading query params on page load.

### 7. Setup guide

`src/mycelos/connectors/oauth_setup_guides.py` — `GOOGLE_CLOUD_GUIDE`
updated with two new steps:
- Step: "Enable Gmail MCP API" — mentions
  `gcloud services enable gmailmcp.googleapis.com` or the Console
  equivalent.
- Step: "Register Redirect URI" — tells the user to add
  `<origin>/api/connectors/oauth/callback` as an Authorized redirect
  URI in the OAuth client they just created. The wizard shows the
  exact string computed from `window.location.origin`.

## Data Flow

### Token exchange

```
Browser                Gateway                              Proxy                       Google
   |                      |                                    |                          |
   | click "Start OAuth"  |                                    |                          |
   |--------------------->|                                    |                          |
   |                      | /oauth/start                       |                          |
   |                      |   - generate state+verifier        |                          |
   |                      |   - read client_id from DB         |--- get_credential ------>|
   |                      |   - build auth_url                 |<-- client_id ------------|
   |                      | store state in memory              |                          |
   |  {auth_url}          |                                    |                          |
   |<---------------------|                                    |                          |
   |                                                                                      |
   | navigate to auth_url                                                                 |
   |----------------------------------------------------------------------------------> G |
   |                                                                                      |
   | user consents                                                                        |
   |                                                                                      |
   | redirect to <origin>/callback?code=...&state=...                                     |
   |<-----------------------------------------------------------------------------------|
   |                      |                                    |                          |
   | GET /callback        |                                    |                          |
   |--------------------->|                                    |                          |
   |                      | validate state                     |                          |
   |                      | POST /oauth/callback ------------->|                          |
   |                      |                                    | POST token_url --------->|
   |                      |                                    |<-- {access_token,        |
   |                      |                                    |     refresh_token, ...}--|
   |                      |                                    | store_credential         |
   |                      |<---- {status: connected} ----------|                          |
   |                      | 302 Location: /connectors.html?connected=gmail                |
   |<---------------------|                                    |                          |
```

### MCP tool call

```
Agent              MCP Manager           Proxy                     Google MCP
  |                    |                    |                           |
  | gmail.search_mails |                    |                           |
  |------------------->| /mcp/call          |                           |
  |                    |------------------->| refresh_if_expired        |
  |                    |                    |   (expires in 30s,        |
  |                    |                    |    refresh needed)        |
  |                    |                    | POST token_url ---------->|
  |                    |                    |<--- {new access_token} ---|
  |                    |                    | update credential         |
  |                    |                    | POST /mcp/v1 (Bearer new)-|
  |                    |                    |<--- tool result ----------|
  |                    |<--- result --------|                           |
  |<-------------------|                    |                           |
```

## Error Handling

| Condition | Response |
|---|---|
| State missing / expired | `/connectors.html?oauth_error=invalid_state` |
| Google returns `error=access_denied` (user cancelled) | `/connectors.html?oauth_error=access_denied` |
| Token exchange HTTP 4xx/5xx | `/connectors.html?oauth_error=<google_error_message>` |
| `refresh_if_expired` fails (refresh token revoked) | `/mcp/start` returns 502 `"Token expired — please reconnect Gmail"`; frontend surfaces this as a banner on next tool call |
| Recipe not `oauth_http` on `/oauth/start` | 400 from gateway |
| `oauth_client_credential_service` missing | 400 `"upload client secret first"` |
| In-memory state dict grows unbounded | 10-min TTL sweeper on each `/oauth/start` call removes expired entries |

## Testing

| Test file | What it covers |
|---|---|
| `tests/test_mcp_recipe_setup_flow.py` | `setup_flow="oauth_http"` enum value accepted, `gmail` recipe has correct `http_endpoint` + `oauth_scopes` + `oauth_token_url` |
| `tests/test_oauth_token_manager.py` | `exchange_code_for_token` posts the right body, stores the parsed token. `refresh_if_expired` no-ops when valid, refreshes + updates when expired, raises on refresh-token revocation |
| `tests/test_proxy_oauth_callback.py` | `/oauth/callback` endpoint: happy path, missing recipe, non-oauth_http recipe, token-exchange failure |
| `tests/test_gateway_oauth_http_flow.py` | `/oauth/start` generates sane URL with PKCE + stored state, `/oauth/callback` validates state + forwards to proxy + redirects to success page. Expired state → error redirect |
| `tests/integration/test_gmail_mcp_http_live.py` | Live smoke test, skipped if `.env.test` lacks `GMAIL_OAUTH_CLIENT_JSON` + `GMAIL_OAUTH_TOKEN_JSON`. Seeds credentials, calls `/mcp/start`, verifies tools list includes documented tools (`search_threads`, `list_labels`, `create_draft`), smoke-calls `list_labels` and asserts `INBOX` present |

No live consent-flow automation — consent is manual and done once by
the tester to populate `.env.test`. The tester consents through the UI,
then extracts the token from the DB with `sqlite3 ~/.mycelos/mycelos.db`
(or a small helper script we ship alongside the tests if manual
extraction is too tedious — decision deferred to the implementation
plan).

## Archive strategy

Before deleting the old `oauth_browser` / file-materialization code:

```bash
git checkout main
git push origin main:archive/oauth-browser-file-materialization
# Now the state is frozen at the branch. Delete code on main freely.
```

Commit message in main explicitly points at the archive branch:

```
refactor: remove oauth_browser + file-materialization path

Google's official remote MCP servers replace the gongrzhe / cocal /
piotr-agier npm packages for the three Google integrations. No
remaining recipe uses the file-materialization flow, so the helper
module, the WebSocket subprocess streaming, and the associated UI
branch are deleted here.

The code lives frozen on `archive/oauth-browser-file-materialization`
in case a future file-hardcoded MCP needs it back.
```

Files removed on main:
- `src/mycelos/security/credential_materializer.py`
- `tests/test_credential_materializer.py`
- `tests/test_proxy_oauth_endpoints.py` (most tests — keep ones that are still about the renamed endpoints)
- `tests/test_proxy_oauth_websocket.py` (entire file — WS is gone)
- `src/mycelos/frontend/shared/oauth_setup.js` simplifies
- Parts of `src/mycelos/security/proxy_server.py`: old `/oauth/start`, `/oauth/stop`, `/oauth/stream/{sid}`, `OAUTH_TMP_ROOT`, `_OAUTH_ALLOWED_HEADS`, `_MCP_ALLOWED_HEADS`, materializer imports, ExitStack lifecycle in `/mcp/start`
- Parts of `src/mycelos/gateway/routes.py`: old `/api/connectors/oauth/start`, `/api/connectors/oauth/stop`, WebSocket passthrough
- Parts of `src/mycelos/connectors/mcp_recipes.py`: `oauth_cmd`, `oauth_keys_credential_service`, `oauth_keys_home_dir`, `oauth_keys_filename`, `oauth_token_filename` fields, and their uses in the three Google recipes
- `docker-compose.yml`: tmpfs mount for `/tmp/mycelos-oauth` (no longer needed)
- `CHANGELOG.md`: add a new entry documenting the swap

Files preserved on main (reused for new flow):
- `oauth_setup_guides.py` — guide content updated
- `/api/connectors/recipes/{id}` endpoint — still needed to fetch recipe metadata
- `/api/credentials/oauth-keys/validate` — still used to validate the client_secret JSON shape
- `setup_flow` field on recipe — values change but field stays

## Security notes

- **PKCE** is used even though this is an offline desktop-ish app — cheap to implement, standard practice, defends against code interception on the redirect.
- **State** param is 32 bytes of `secrets.token_urlsafe` with 10-minute TTL in an in-memory dict. Single-use. Rotated on each `/oauth/start`.
- **Client secret** is stored encrypted (same credential store as everything else), never logged.
- **Refresh tokens** carry offline access and are the real long-lived key — same protection as access tokens.
- **Redirect URI** must be registered by the user manually in Cloud Console. If a user mistypes, Google rejects the auth request with `redirect_uri_mismatch` and the setup fails visibly.
- **No Proxy needed for the Google token endpoint**. The proxy has internet (two-container Phase 1b lockdown) and already egresses to Anthropic, OpenAI, etc. Adding `oauth2.googleapis.com` to its outbound allow-list is a no-op (the proxy doesn't enforce an egress allow-list today).

## Rollout

Single PR, single feature branch, single merge. Because archive branch
is pushed first, rollback is `git revert <merge-sha>` on main —
nothing needs to come back from the archive to restore behavior (old
flow is gone, not hidden behind a feature flag).

## Open questions

None blocking. One observation: if the Developer Preview API goes away
before GA, we re-archive this flow and restore the old one from the
archive branch. The cost of that is one cherry-pick chain, which we
can live with.
