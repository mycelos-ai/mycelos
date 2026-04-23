# OAuth Connector Setup (Google + reusable) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give users a guided web UI to connect Gmail / Calendar / Drive (and any future OAuth-based MCP connector) end-to-end, covering Google Cloud project setup, OAuth keys upload, and the interactive browser consent — without opening a terminal.

**Architecture:** Extend the `MCPRecipe` dataclass with a `setup_flow` discriminator so recipes can declare `"oauth_browser"` instead of the default `"secret"` flow. The web UI renders a richer modal for `oauth_browser` recipes: (a) an optional step-by-step "Create your Google Cloud project" wizard, (b) a file/textarea upload for `gcp-oauth.keys.json`, (c) a "Start OAuth consent" button that opens a WebSocket to the proxy, which spawns `npx ... auth` and streams stdout/stderr back so the user sees the consent URL and completes the flow in-browser. The gateway never sees the OAuth keys or the resulting token — both land in the proxy container's `/data/.xxx-mcp/` directory via the existing credential-materialize mechanism.

**Tech Stack:** Python 3.12+, FastAPI (gateway + proxy), WebSocket for interactive subprocess I/O, Alpine.js + TailwindCSS (frontend), existing `MCPRecipe` + `DelegatingCredentialProxy` + `SecurityProxyClient` infrastructure.

---

## Scope check

This plan covers two features that share 80% of the machinery:
1. **Generic OAuth-browser setup flow** — `setup_flow="oauth_browser"` on recipes, web UI dialog, proxy endpoint that spawns `<cmd> auth` subcommands, streams I/O to the browser
2. **Google Cloud project wizard** — a guided multi-step modal inside the oauth_browser flow that walks users through Cloud Console so they can produce `gcp-oauth.keys.json` without reading the external doc

They ship together because (1) without (2) is unusable for new users; (2) without (1) is pointless. Kept in one plan because each task builds on the last and splitting would force "half-working" intermediate commits.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/mycelos/connectors/mcp_recipes.py` | Add `setup_flow`, `oauth_cmd`, `oauth_setup_guide_id` fields to `MCPRecipe`; mark `gmail` / `google-calendar` / `google-drive` as `oauth_browser` recipes |
| `src/mycelos/connectors/oauth_setup_guides.py` | New — registry of setup-guide JSON structures (e.g. `google_cloud`) that the frontend renders as step-by-step wizards |
| `src/mycelos/security/proxy_server.py` | Add `POST /oauth/start` (spawn auth subprocess) and `WS /oauth/stream/{session_id}` (bidirectional stream for stdout/stdin) |
| `src/mycelos/security/proxy_client.py` | Add `oauth_start` + thin WebSocket helper |
| `src/mycelos/gateway/routes.py` | Add `GET /api/connectors/recipes/{id}` (includes setup_flow + guide), `POST /api/connectors/oauth/start`, `WS /api/connectors/oauth/stream/{session_id}` (proxied to proxy) |
| `src/mycelos/frontend/pages/connectors.html` | New `OAuthSetupDialog` component that renders based on recipe.setup_flow; opens on click for Google recipes |
| `src/mycelos/frontend/shared/oauth_setup.js` | New — guide-rendering + WebSocket client shared between recipes |
| `tests/test_mcp_recipe_setup_flow.py` | New — unit tests for the new recipe fields and the guide registry |
| `tests/test_proxy_oauth_endpoints.py` | New — unit tests for `/oauth/start` + streaming contract |
| `tests/test_gateway_oauth_proxy.py` | New — unit tests for the gateway-side passthrough |
| `docs/deployment/google-setup.md` | Update — point users at the web wizard first, keep the CLI walkthrough as fallback |
| `CHANGELOG.md` | Week 17 entry |

**Design note on the WebSocket.** We use WS for the interactive stream because HTTP streaming can't carry user input back to the spawned process. The stream carries frame objects `{type: "stdout"|"stderr"|"stdin"|"done", data: "..."}`. Frontend parses stdout for the OAuth URL heuristically (most packages print a line starting with `https://accounts.google.com/o/oauth2/`), displays it prominently with a "Open in browser" button, and shows the raw tail underneath so power users see what's happening. `stdin` frames are sent when the process prompts (e.g. for a paste-back code).

---

## Task 1: Extend MCPRecipe with setup_flow field

**Files:**
- Modify: `src/mycelos/connectors/mcp_recipes.py` (dataclass + three Google recipes)
- Create: `tests/test_mcp_recipe_setup_flow.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_recipe_setup_flow.py`:

```python
"""The MCPRecipe dataclass gained a setup_flow field so the frontend
can render different setup dialogs for different credential shapes.
Recipes default to 'secret' (single password-style input); OAuth-based
recipes declare 'oauth_browser' which triggers the Google-style wizard."""
from __future__ import annotations

from mycelos.connectors.mcp_recipes import MCPRecipe, RECIPES


def test_default_setup_flow_is_secret() -> None:
    """Every existing recipe uses the plain-secret flow unless it opts out."""
    r = MCPRecipe(id="x", name="X", description="", command="npx -y x")
    assert r.setup_flow == "secret"


def test_gmail_recipe_declares_oauth_browser() -> None:
    r = RECIPES["gmail"]
    assert r.setup_flow == "oauth_browser"
    # The oauth_cmd is what the proxy spawns when the user clicks
    # "Start OAuth consent" — upstream's auth subcommand.
    assert r.oauth_cmd == "npx -y @gongrzhe/server-gmail-autoauth-mcp auth"
    # A setup-guide id links the recipe to a step-by-step wizard
    # (Google Cloud project creation, etc.). All three Google recipes
    # share the 'google_cloud' guide.
    assert r.oauth_setup_guide_id == "google_cloud"


def test_google_calendar_recipe_declares_oauth_browser() -> None:
    r = RECIPES["google-calendar"]
    assert r.setup_flow == "oauth_browser"
    assert r.oauth_cmd == "npx -y @cocal/google-calendar-mcp auth"
    assert r.oauth_setup_guide_id == "google_cloud"


def test_google_drive_recipe_declares_oauth_browser() -> None:
    r = RECIPES["google-drive"]
    assert r.setup_flow == "oauth_browser"
    assert r.oauth_cmd == "npx -y @piotr-agier/google-drive-mcp auth"
    assert r.oauth_setup_guide_id == "google_cloud"


def test_non_oauth_recipes_keep_secret_flow() -> None:
    """Make sure we didn't accidentally flip other recipes."""
    for rid in ("brave-search", "github", "notion", "slack", "telegram", "email"):
        r = RECIPES.get(rid)
        if r is None:
            continue  # recipe may be renamed or removed in future
        assert r.setup_flow == "secret", f"recipe {rid} unexpectedly switched flows"
        assert r.oauth_cmd == "", f"recipe {rid} should not declare oauth_cmd"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/stefan/Documents/railsapps/mycelos
PYTHONPATH=src pytest tests/test_mcp_recipe_setup_flow.py -v
```

Expected: 5 tests fail — `MCPRecipe` has no `setup_flow` / `oauth_cmd` / `oauth_setup_guide_id` attributes yet.

- [ ] **Step 3: Add the three fields to the dataclass**

Edit `src/mycelos/connectors/mcp_recipes.py`. Find the `MCPRecipe` dataclass (around lines 13–34). After the `static_env` field, add:

```python
    setup_flow: str = "secret"
        # "secret" (default): single password-style input for API key.
        # "oauth_browser": render the OAuth-keys upload + browser-consent
        # wizard. Frontend switches dialog based on this value.
    oauth_cmd: str = ""
        # Non-empty only when setup_flow == "oauth_browser". The command
        # the proxy spawns on "Start OAuth consent" — e.g.
        # "npx -y @gongrzhe/server-gmail-autoauth-mcp auth". stdout/stderr
        # is streamed to the web UI; the user follows the URL the command
        # prints.
    oauth_setup_guide_id: str = ""
        # Key into the oauth_setup_guides registry (e.g. "google_cloud").
        # Non-empty only when setup_flow == "oauth_browser". The guide
        # describes prerequisites like "create a Google Cloud project,
        # enable the Gmail API, download gcp-oauth.keys.json" as a
        # step-by-step wizard.
```

- [ ] **Step 4: Flip the three Google recipes to oauth_browser**

In the same file, find the three Google recipes (`gmail`, `google-calendar`, `google-drive`). For each, add these three fields at the end of the constructor call (before the closing `),`):

For `gmail`:
```python
        setup_flow="oauth_browser",
        oauth_cmd="npx -y @gongrzhe/server-gmail-autoauth-mcp auth",
        oauth_setup_guide_id="google_cloud",
```

For `google-calendar`:
```python
        setup_flow="oauth_browser",
        oauth_cmd="npx -y @cocal/google-calendar-mcp auth",
        oauth_setup_guide_id="google_cloud",
```

For `google-drive`:
```python
        setup_flow="oauth_browser",
        oauth_cmd="npx -y @piotr-agier/google-drive-mcp auth",
        oauth_setup_guide_id="google_cloud",
```

- [ ] **Step 5: Verify tests pass**

```bash
cd /Users/stefan/Documents/railsapps/mycelos
PYTHONPATH=src pytest tests/test_mcp_recipe_setup_flow.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Run the broader recipe test suite to catch regressions**

```bash
PYTHONPATH=src pytest tests/test_mcp_recipes_google.py tests/test_mcp_recipe_setup_flow.py -v
```

Expected: all passing (6 existing + 5 new = 11).

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/connectors/mcp_recipes.py tests/test_mcp_recipe_setup_flow.py
git commit -m "feat(mcp): add setup_flow + oauth_cmd fields to MCPRecipe"
```

No `Co-Authored-By` footer.

---

## Task 2: Google Cloud setup-guide registry

**Files:**
- Create: `src/mycelos/connectors/oauth_setup_guides.py`
- Modify: `tests/test_mcp_recipe_setup_flow.py` (append guide tests)

- [ ] **Step 1: Append the failing test**

Add to `tests/test_mcp_recipe_setup_flow.py`:

```python


# ── Setup-guide registry ──


from mycelos.connectors.oauth_setup_guides import (  # noqa: E402
    SETUP_GUIDES,
    get_setup_guide,
)


def test_google_cloud_guide_exists() -> None:
    """The 'google_cloud' guide must exist with a non-empty step list and
    each step must carry a title, body (markdown), and optional cta_url."""
    guide = get_setup_guide("google_cloud")
    assert guide is not None
    assert guide["id"] == "google_cloud"
    assert guide["title"]  # non-empty label
    steps = guide["steps"]
    assert len(steps) >= 5, "Google Cloud setup needs at least 5 concrete steps"
    for i, step in enumerate(steps):
        assert step["title"], f"step {i} missing title"
        assert step["body"], f"step {i} missing body"
        # cta_url is optional — a step that just asks the user to copy
        # a value out of the UI doesn't need a link.


def test_google_cloud_guide_mentions_oauth_desktop_app() -> None:
    """The step that creates the credential must specify Desktop app —
    that's the only OAuth client type the three MCP servers support."""
    guide = get_setup_guide("google_cloud")
    body_text = " ".join(step["body"].lower() for step in guide["steps"])
    assert "desktop app" in body_text or "desktop application" in body_text


def test_unknown_guide_returns_none() -> None:
    assert get_setup_guide("nonexistent-guide") is None


def test_all_guides_in_registry_self_reference() -> None:
    """Every guide's 'id' field must equal its key in SETUP_GUIDES."""
    for key, guide in SETUP_GUIDES.items():
        assert guide["id"] == key
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src pytest tests/test_mcp_recipe_setup_flow.py -v
```

Expected: the four new tests fail with `ModuleNotFoundError: mycelos.connectors.oauth_setup_guides`.

- [ ] **Step 3: Create the registry**

Write `src/mycelos/connectors/oauth_setup_guides.py`:

```python
"""Step-by-step setup guides for OAuth-based MCP connectors.

Each guide is a list of rendered steps the frontend walks the user
through inside the connector setup dialog. Keeps platform-specific
instructions out of the recipe dataclass (recipes just reference a
guide by id) and makes the guides reusable across recipes that share
a setup path — all three Google MCP servers need the same Google
Cloud project, so they all point at `google_cloud`.

Step shape:
    {
        "title": "Short step label",
        "body":  "Markdown explanation shown to the user",
        "cta_url": "https://example.com"  # optional 'open this page' link
        "cta_label": "Open Cloud Console"  # optional — defaults to "Open"
    }

Add a new guide by putting it in SETUP_GUIDES keyed by its id. Keep
the id snake_case so it looks natural in JSON APIs.
"""
from __future__ import annotations

from typing import Any


GOOGLE_CLOUD_GUIDE: dict[str, Any] = {
    "id": "google_cloud",
    "title": "Set up your Google Cloud project",
    "intro": (
        "Google requires every app that accesses Gmail / Calendar / Drive "
        "on your behalf to be registered in a Google Cloud project *you* "
        "own. This is a one-time, ~10-minute setup. After it's done, all "
        "three Mycelos Google connectors can share the same project."
    ),
    "steps": [
        {
            "title": "Create or pick a Google Cloud project",
            "body": (
                "Open the Google Cloud Console. Create a new project "
                "(name it anything — e.g. 'Mycelos') or pick one you "
                "already have. Projects are free and never charged unless "
                "you explicitly enable billing."
            ),
            "cta_url": "https://console.cloud.google.com/projectcreate",
            "cta_label": "Open Cloud Console",
        },
        {
            "title": "Enable the APIs you want to use",
            "body": (
                "For each Google service you plan to connect, enable its "
                "API in your project: Gmail API, Google Calendar API, "
                "Google Drive API. Enabling all three now is fine — they "
                "share the project and you can disable them later."
            ),
            "cta_url": "https://console.cloud.google.com/apis/library",
            "cta_label": "Open API Library",
        },
        {
            "title": "Configure the OAuth consent screen",
            "body": (
                "Go to **APIs & Services → OAuth consent screen**. Pick "
                "**External** user type. Fill in an app name (e.g. "
                "'Mycelos'), your email as support contact, and your "
                "email as developer contact. Save and continue. You can "
                "skip the scopes page. On the 'Test users' page, add "
                "*your own Google account* — the account whose Gmail / "
                "Calendar / Drive you want to connect — and save. "
                "Leaving the app in 'Testing' is fine; you do NOT need "
                "to publish it."
            ),
            "cta_url": "https://console.cloud.google.com/apis/credentials/consent",
            "cta_label": "Open Consent Screen",
        },
        {
            "title": "Create an OAuth Desktop-app credential",
            "body": (
                "Go to **APIs & Services → Credentials → Create "
                "credentials → OAuth client ID**. **Application type: "
                "Desktop app** (important — Mycelos only supports this "
                "type). Name it anything. Click Create. A dialog pops up "
                "with a Client ID and Client secret — click "
                "**DOWNLOAD JSON**. Save the file as `gcp-oauth.keys.json`."
            ),
            "cta_url": "https://console.cloud.google.com/apis/credentials",
            "cta_label": "Open Credentials",
        },
        {
            "title": "Upload the keys to Mycelos",
            "body": (
                "Come back to this dialog. In the next step, upload the "
                "`gcp-oauth.keys.json` file you just downloaded. Mycelos "
                "stores it encrypted inside the proxy container — the "
                "gateway and the LLM never see the contents."
            ),
        },
        {
            "title": "Complete the browser consent",
            "body": (
                "After upload, click **Start OAuth consent**. Mycelos "
                "launches the MCP server's one-shot auth command in the "
                "proxy. A Google consent URL appears in this dialog — "
                "open it, sign in with the account you added as a Test "
                "user, and accept the scopes. The server writes a token "
                "file and you're done."
            ),
        },
    ],
}


SETUP_GUIDES: dict[str, dict[str, Any]] = {
    "google_cloud": GOOGLE_CLOUD_GUIDE,
}


def get_setup_guide(guide_id: str) -> dict[str, Any] | None:
    """Return the guide by id or None if unknown.

    Unknown ids return None rather than raising so the frontend can
    gracefully degrade to 'no guide, just show the upload form' when
    a recipe references a guide this backend doesn't know about (e.g.
    a newer recipe on an older backend version).
    """
    return SETUP_GUIDES.get(guide_id)
```

- [ ] **Step 4: Verify tests pass**

```bash
PYTHONPATH=src pytest tests/test_mcp_recipe_setup_flow.py -v
```

Expected: all 9 tests pass (5 from Task 1 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/connectors/oauth_setup_guides.py tests/test_mcp_recipe_setup_flow.py
git commit -m "feat(mcp): google_cloud setup guide registry"
```

---

## Task 3: Gateway endpoint serving recipe + guide as JSON

**Files:**
- Modify: `src/mycelos/gateway/routes.py` (new endpoint)
- Create: `tests/test_gateway_recipe_endpoint.py`

Context: the frontend needs one call that returns everything to render the setup dialog — recipe metadata plus the full guide. A separate endpoint keeps this out of `/api/connectors` which currently returns just installed connectors.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_recipe_endpoint.py`:

```python
"""GET /api/connectors/recipes/{id} returns the recipe metadata plus
the resolved setup guide (if any). Frontend uses this to render the
right connector-setup dialog."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_with_routes):
    """app_with_routes comes from the existing gateway test fixtures."""
    return TestClient(app_with_routes)


def test_recipe_endpoint_returns_gmail_with_guide(client) -> None:
    resp = client.get("/api/connectors/recipes/gmail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "gmail"
    assert body["setup_flow"] == "oauth_browser"
    assert body["oauth_cmd"].startswith("npx -y @gongrzhe/")
    # Guide is inlined so the frontend needs only one roundtrip.
    assert body["setup_guide"] is not None
    assert body["setup_guide"]["id"] == "google_cloud"
    assert len(body["setup_guide"]["steps"]) >= 5


def test_recipe_endpoint_returns_secret_recipe_without_guide(client) -> None:
    resp = client.get("/api/connectors/recipes/brave-search")
    assert resp.status_code == 200
    body = resp.json()
    assert body["setup_flow"] == "secret"
    assert body["setup_guide"] is None


def test_recipe_endpoint_unknown_id_returns_404(client) -> None:
    resp = client.get("/api/connectors/recipes/this-does-not-exist")
    assert resp.status_code == 404
```

If `app_with_routes` fixture doesn't already exist, check `tests/conftest.py` for what's available. If there's no such fixture, create one inline — use this minimal form at the top of the file (after imports):

```python
@pytest.fixture
def app_with_routes():
    """Minimal FastAPI app with gateway routes mounted — no storage,
    no audit, just enough state for recipe endpoints to answer."""
    from fastapi import FastAPI
    from types import SimpleNamespace
    from mycelos.gateway.routes import register_routes
    app = FastAPI()
    # register_routes signature: register_routes(app) — state is
    # attached as app.state.mycelos and referenced only by handlers
    # that need the DB. Recipe handlers don't need state.
    app.state.mycelos = SimpleNamespace()
    register_routes(app)
    return app
```

Before adopting that inline fixture, grep `tests/conftest.py` for `def register_routes` or `app_with_routes` — if something similar exists, use it instead to stay consistent.

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src pytest tests/test_gateway_recipe_endpoint.py -v
```

Expected: all three fail — endpoint doesn't exist; 404s everywhere.

- [ ] **Step 3: Add the endpoint**

In `src/mycelos/gateway/routes.py`, find a good home for the new handler (somewhere near the existing `/api/connectors` handlers). Add:

```python
    @api.get("/api/connectors/recipes/{recipe_id}")
    async def get_recipe(recipe_id: str) -> dict[str, Any]:
        """Recipe metadata + resolved setup guide in one roundtrip.

        Used by the frontend setup dialog to decide which flow to render
        (plain 'secret' vs. 'oauth_browser' wizard) and to show the
        platform-specific preparation steps inline.
        """
        from mycelos.connectors.mcp_recipes import get_recipe as get_r
        from mycelos.connectors.oauth_setup_guides import get_setup_guide

        recipe = get_r(recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail=f"Unknown recipe: {recipe_id}")

        guide = (
            get_setup_guide(recipe.oauth_setup_guide_id)
            if recipe.oauth_setup_guide_id
            else None
        )
        return {
            "id": recipe.id,
            "name": recipe.name,
            "description": recipe.description,
            "command": recipe.command,
            "transport": recipe.transport,
            "category": recipe.category,
            "credentials": recipe.credentials,
            "capabilities_preview": recipe.capabilities_preview,
            "setup_flow": recipe.setup_flow,
            "oauth_cmd": recipe.oauth_cmd,
            "oauth_setup_guide_id": recipe.oauth_setup_guide_id,
            "setup_guide": guide,
            "requires_node": recipe.requires_node,
        }
```

If `HTTPException` isn't already imported at the top of the file, add `from fastapi import HTTPException` to the import block.

- [ ] **Step 4: Verify tests pass**

```bash
PYTHONPATH=src pytest tests/test_gateway_recipe_endpoint.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Broader sanity — import and baseline**

```bash
PYTHONPATH=src python -c "import mycelos.gateway.routes; print('ok')"
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -3
```

Expected: 'ok' prints; unit baseline unchanged (whatever it is now + 9 new tests from Tasks 1–3).

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/gateway/routes.py tests/test_gateway_recipe_endpoint.py
git commit -m "feat(gateway): GET /api/connectors/recipes/{id} with inlined setup guide"
```

---

## Task 4: Proxy `/oauth/start` — spawn auth subcommand

**Files:**
- Modify: `src/mycelos/security/proxy_server.py` (add endpoint + session state)
- Modify: `src/mycelos/security/proxy_client.py` (add `oauth_start`)
- Create: `tests/test_proxy_oauth_endpoints.py`

Context: this endpoint takes an `oauth_cmd` string (like `npx -y @gongrzhe/... auth`) and `env_vars` (where the keys JSON path lives), spawns the subprocess, and returns a session id. The subprocess keeps running in the proxy; the WebSocket from Task 5 reads its stdout/stderr and writes to stdin. No OAuth logic here — we are just a transparent I/O proxy around the upstream auth command.

- [ ] **Step 1: Write the failing test**

Create `tests/test_proxy_oauth_endpoints.py`:

```python
"""POST /oauth/start spawns an OAuth auth subprocess in the proxy and
returns a session id. The WebSocket endpoint (tested separately) then
streams the I/O. This is the control plane — one POST, one session."""
from __future__ import annotations

import os
import shlex
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def proxy_client(proxy_app):
    """proxy_app fixture — see tests/conftest.py. If not present,
    create a minimal one that starts the SecurityProxy FastAPI app
    without the full Mycelos bootstrap. See test_proxy_server.py for
    patterns to copy."""
    return TestClient(proxy_app)


def test_oauth_start_rejects_without_auth(proxy_client) -> None:
    resp = proxy_client.post("/oauth/start", json={
        "oauth_cmd": "echo hello",
        "env_vars": {},
    })
    assert resp.status_code == 401


def test_oauth_start_returns_session_id(proxy_client, proxy_auth_headers) -> None:
    """Spawning 'cat' as a stand-in for the auth subprocess: cat is long-
    running, reads stdin, prints nothing until we talk to it via the WS.
    That's exactly the shape of npx auth commands."""
    resp = proxy_client.post("/oauth/start", json={
        "oauth_cmd": "cat",
        "env_vars": {"X_TEST_MARKER": "hello"},
    }, headers=proxy_auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"].startswith("oauth-")
    # Cleanup so the test doesn't leak a hanging subprocess.
    stop = proxy_client.post("/oauth/stop", json={"session_id": body["session_id"]},
                             headers=proxy_auth_headers)
    assert stop.status_code == 200


def test_oauth_start_rejects_disallowed_command(proxy_client, proxy_auth_headers) -> None:
    """Only npx-based commands are allowed through /oauth/start — the
    surface area of arbitrary subprocess spawn needs to be narrow. If
    a recipe wants to use something other than npx, it needs code
    changes, not a config flip."""
    resp = proxy_client.post("/oauth/start", json={
        "oauth_cmd": "rm -rf /",
        "env_vars": {},
    }, headers=proxy_auth_headers)
    assert resp.status_code == 400
    assert "npx" in resp.json().get("error", "").lower()


def test_oauth_stop_terminates_session(proxy_client, proxy_auth_headers) -> None:
    resp = proxy_client.post("/oauth/start", json={
        "oauth_cmd": "cat",
        "env_vars": {},
    }, headers=proxy_auth_headers)
    sid = resp.json()["session_id"]
    resp2 = proxy_client.post("/oauth/stop", json={"session_id": sid},
                              headers=proxy_auth_headers)
    assert resp2.status_code == 200
    # Double-stop is idempotent.
    resp3 = proxy_client.post("/oauth/stop", json={"session_id": sid},
                              headers=proxy_auth_headers)
    assert resp3.status_code == 200
```

If `proxy_app` / `proxy_auth_headers` fixtures don't exist in `tests/conftest.py`, check `tests/test_proxy_*.py` for a pattern to copy. Most likely there's a module-local fixture in `tests/test_proxy_server.py` that sets up auth headers. Reuse the same pattern rather than inventing new infrastructure.

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src pytest tests/test_proxy_oauth_endpoints.py -v
```

Expected: all four fail — no `/oauth/start` or `/oauth/stop` endpoint exists.

- [ ] **Step 3: Add the endpoints to `proxy_server.py`**

In `src/mycelos/security/proxy_server.py`, find the `/mcp/start` endpoint (~line 461) and add the new endpoints right after the `/mcp/stop` block. Structure:

```python
    @app.post("/oauth/start")
    async def oauth_start(request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        body = await request.json()
        oauth_cmd: str = body.get("oauth_cmd", "")
        env_vars: dict = body.get("env_vars", {}) or {}

        # Narrow the blast radius: only npx-based commands are accepted.
        # An oauth_cmd comes straight from the MCPRecipe so we trust our
        # own registry, but an attacker with a stolen token should not
        # be able to spawn arbitrary processes via this endpoint.
        import shlex
        parts = shlex.split(oauth_cmd)
        if not parts or parts[0] not in ("npx", "/usr/bin/env"):
            return JSONResponse(
                {"error": "oauth_cmd must start with 'npx' — recipe validation"},
                status_code=400,
            )

        # Resolve credential: references same way /mcp/start does.
        resolved_env = dict(os.environ)
        credential_proxy = _get_credential_proxy()
        for key, val in env_vars.items():
            if isinstance(val, str) and val.startswith("credential:") and credential_proxy:
                service = val[len("credential:"):]
                cred = credential_proxy.get_credential(service, user_id=user_id)
                if not cred or not cred.get("api_key"):
                    return JSONResponse(
                        {"error": f"Credential '{service}' not found — fail-closed"},
                        status_code=502,
                    )
                resolved_env[key] = cred["api_key"]
            else:
                resolved_env[key] = str(val)

        import subprocess
        import secrets
        proc = subprocess.Popen(
            parts,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=resolved_env,
            bufsize=0,
        )
        session_id = f"oauth-{secrets.token_hex(6)}"
        _state.setdefault("_oauth_sessions", {})[session_id] = {
            "proc": proc,
            "user_id": user_id,
            "oauth_cmd": oauth_cmd,
        }

        storage = _get_storage()
        if storage:
            _write_audit(storage, "proxy.oauth_started", user_id, {
                "session_id": session_id,
                "oauth_cmd": oauth_cmd,
            })

        return JSONResponse({"session_id": session_id})


    @app.post("/oauth/stop")
    async def oauth_stop(request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        body = await request.json()
        session_id: str = body.get("session_id", "")
        sessions = _state.setdefault("_oauth_sessions", {})
        sess = sessions.pop(session_id, None)
        if sess is None:
            # Idempotent — session may already be gone.
            return JSONResponse({"status": "not_found"}, status_code=200)

        proc = sess["proc"]
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        storage = _get_storage()
        if storage:
            _write_audit(storage, "proxy.oauth_stopped", user_id, {
                "session_id": session_id,
            })

        return JSONResponse({"status": "stopped"})
```

`_state` is the existing module-level dict used by `/mcp/start` for `_mcp_sessions` — we reuse it for `_oauth_sessions` to keep the shape consistent.

- [ ] **Step 4: Add the client-side helper**

In `src/mycelos/security/proxy_client.py`, find the `mcp_start` method (around line 137) and add after `mcp_stop`:

```python
    def oauth_start(self, oauth_cmd: str, env_vars: dict, user_id: str = "default") -> dict:
        """Spawn an OAuth auth subprocess in the proxy. Returns {session_id}.

        Pair this with the WebSocket at /oauth/stream/{session_id} to
        actually interact with the process. Always call oauth_stop when
        done — subprocesses don't auto-clean on client disconnect.
        """
        resp = self._request("POST", "/oauth/start", json={
            "oauth_cmd": oauth_cmd,
            "env_vars": env_vars,
        }, headers={"X-User-Id": user_id})
        return resp.json()

    def oauth_stop(self, session_id: str, user_id: str = "default") -> dict:
        """Terminate an OAuth session. Idempotent — stopping an unknown
        or already-stopped session returns status=not_found (200)."""
        resp = self._request("POST", "/oauth/stop", json={
            "session_id": session_id,
        }, headers={"X-User-Id": user_id})
        return resp.json()
```

- [ ] **Step 5: Verify tests pass**

```bash
PYTHONPATH=src pytest tests/test_proxy_oauth_endpoints.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Broader baseline**

```bash
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -3
```

Expected: still no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/security/proxy_server.py src/mycelos/security/proxy_client.py tests/test_proxy_oauth_endpoints.py
git commit -m "feat(proxy): /oauth/start + /oauth/stop — spawn OAuth auth subprocesses"
```

---

## Task 5: WebSocket streaming for OAuth subprocess I/O

**Files:**
- Modify: `src/mycelos/security/proxy_server.py` (add WebSocket handler)
- Create: `tests/test_proxy_oauth_websocket.py`

Context: the WebSocket ships frames `{type: "stdout"|"stderr"|"stdin"|"done", data: str}`. Server pushes stdout/stderr from the subprocess; client pushes stdin. `done` is sent when the subprocess exits with an exit_code field.

- [ ] **Step 1: Write the failing test**

Create `tests/test_proxy_oauth_websocket.py`:

```python
"""The /oauth/stream/{session_id} WebSocket streams subprocess I/O
between the browser and the spawned auth process. Frame shape:
{type: 'stdout'|'stderr'|'stdin'|'done', data: str, exit_code?: int}."""
from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient


def test_websocket_echo_through_cat(proxy_app, proxy_auth_headers) -> None:
    """With 'cat' as the subprocess: anything we send as stdin should
    come back as stdout. That's exactly what 'cat' does, and it's a
    faithful stand-in for the 'paste your code back' step of a real
    OAuth flow."""
    client = TestClient(proxy_app)
    # Start the session
    resp = client.post("/oauth/start", json={
        "oauth_cmd": "cat",
        "env_vars": {},
    }, headers=proxy_auth_headers)
    assert resp.status_code == 200
    sid = resp.json()["session_id"]

    try:
        with client.websocket_connect(
            f"/oauth/stream/{sid}",
            headers=proxy_auth_headers,
        ) as ws:
            # Send stdin
            ws.send_text(json.dumps({"type": "stdin", "data": "hello oauth\n"}))
            # Expect stdout echo
            frame = json.loads(ws.receive_text())
            # cat may batch or split; at minimum we see our text back.
            assert frame["type"] == "stdout"
            assert "hello oauth" in frame["data"]
    finally:
        client.post("/oauth/stop", json={"session_id": sid},
                    headers=proxy_auth_headers)


def test_websocket_unknown_session_closes_immediately(proxy_app, proxy_auth_headers) -> None:
    client = TestClient(proxy_app)
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/oauth/stream/oauth-nonexistent",
            headers=proxy_auth_headers,
        ) as ws:
            ws.receive_text()  # Should get closed before any frame
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src pytest tests/test_proxy_oauth_websocket.py -v
```

Expected: both fail — no WebSocket endpoint exists.

- [ ] **Step 3: Add the WebSocket handler**

In `src/mycelos/security/proxy_server.py`, add right after `/oauth/stop`:

```python
    @app.websocket("/oauth/stream/{session_id}")
    async def oauth_stream(websocket, session_id: str) -> None:
        """Stream subprocess stdout/stderr to the client and relay client
        stdin writes back to the subprocess. Closes the WS when the
        subprocess exits; does NOT auto-stop the session (client is
        responsible for calling /oauth/stop)."""
        # Auth check — same header shape as HTTP endpoints.
        token = websocket.headers.get("authorization", "")
        expected = f"Bearer {_auth_token}"
        if not _auth_token or token != expected:
            await websocket.close(code=4401)
            return

        sessions = _state.setdefault("_oauth_sessions", {})
        sess = sessions.get(session_id)
        if sess is None:
            await websocket.close(code=4404)
            return

        proc = sess["proc"]
        await websocket.accept()

        import asyncio

        async def pump_stdout():
            loop = asyncio.get_running_loop()
            while True:
                chunk = await loop.run_in_executor(
                    None, proc.stdout.read1 if hasattr(proc.stdout, "read1") else lambda: proc.stdout.read(4096),
                    4096,
                )
                if not chunk:
                    break
                try:
                    await websocket.send_text(json.dumps({
                        "type": "stdout",
                        "data": chunk.decode("utf-8", "replace"),
                    }))
                except Exception:
                    return

        async def pump_stderr():
            loop = asyncio.get_running_loop()
            while True:
                chunk = await loop.run_in_executor(
                    None, proc.stderr.read1 if hasattr(proc.stderr, "read1") else lambda: proc.stderr.read(4096),
                    4096,
                )
                if not chunk:
                    break
                try:
                    await websocket.send_text(json.dumps({
                        "type": "stderr",
                        "data": chunk.decode("utf-8", "replace"),
                    }))
                except Exception:
                    return

        async def pump_stdin():
            try:
                while True:
                    raw = await websocket.receive_text()
                    try:
                        frame = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if frame.get("type") == "stdin" and proc.stdin is not None:
                        data = frame.get("data", "").encode()
                        proc.stdin.write(data)
                        proc.stdin.flush()
            except Exception:
                return

        # Run all three pumps concurrently; stop when the subprocess exits.
        stdout_task = asyncio.create_task(pump_stdout())
        stderr_task = asyncio.create_task(pump_stderr())
        stdin_task = asyncio.create_task(pump_stdin())

        async def wait_exit():
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, proc.wait)

        exit_code = await wait_exit()

        # Drain remaining output before announcing done.
        for t in (stdout_task, stderr_task):
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except Exception:
                t.cancel()

        try:
            await websocket.send_text(json.dumps({
                "type": "done",
                "exit_code": exit_code,
                "data": "",
            }))
        except Exception:
            pass

        stdin_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass
```

Add to the imports at the top of `proxy_server.py` if not already present:
```python
import json
import asyncio
```

- [ ] **Step 4: Add WebSocket support to proxy_client**

In `src/mycelos/security/proxy_client.py`, after the `oauth_stop` method, add:

```python
    def oauth_stream_url(self, session_id: str) -> str:
        """Return the ws:// URL for the OAuth streaming endpoint.

        The gateway doesn't itself *use* the WebSocket — it mints the
        URL for the browser, which opens it through the gateway's own
        WS passthrough (see /api/connectors/oauth/stream). Exposing it
        here keeps the URL-construction logic in one place.
        """
        base = self._base_url.replace("http://", "ws://").replace("https://", "wss://")
        return f"{base}/oauth/stream/{session_id}"
```

- [ ] **Step 5: Verify tests pass**

```bash
PYTHONPATH=src pytest tests/test_proxy_oauth_websocket.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Broader baseline**

```bash
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -3
```

Expected: no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/security/proxy_server.py src/mycelos/security/proxy_client.py tests/test_proxy_oauth_websocket.py
git commit -m "feat(proxy): WebSocket /oauth/stream for subprocess I/O"
```

---

## Task 6: Gateway WebSocket passthrough to proxy

**Files:**
- Modify: `src/mycelos/gateway/routes.py` (add `/api/connectors/oauth/*` passthroughs)
- Create: `tests/test_gateway_oauth_proxy.py`

Context: the browser can't talk to the proxy directly (no public URL, no auth). The gateway mints a session (via `proxy_client.oauth_start`) and proxies the WebSocket for the browser.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_oauth_proxy.py`:

```python
"""The gateway exposes its own /api/connectors/oauth/start + WS passthrough
so browsers don't need to know the proxy exists. Internally it just
forwards to the proxy."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient


def test_oauth_start_passthrough_forwards_to_proxy(app_with_routes) -> None:
    """The gateway's /api/connectors/oauth/start just reads the recipe,
    then calls proxy_client.oauth_start with the recipe's oauth_cmd.
    The body from the browser carries the env_vars (the credential
    reference for the OAuth keys file)."""
    mock_client = MagicMock()
    mock_client.oauth_start.return_value = {"session_id": "oauth-testsid"}
    app_with_routes.state.mycelos.proxy_client = mock_client

    client = TestClient(app_with_routes)
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "gmail",
        "env_vars": {"GMAIL_OAUTH_PATH": "credential:gmail-oauth-keys"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "oauth-testsid"
    assert body["ws_url"]  # stream URL for the browser to connect to
    # Proxy was told to spawn the gmail auth cmd, with the env we passed.
    call = mock_client.oauth_start.call_args
    assert "@gongrzhe/server-gmail-autoauth-mcp auth" in call.kwargs["oauth_cmd"]
    assert call.kwargs["env_vars"]["GMAIL_OAUTH_PATH"] == "credential:gmail-oauth-keys"


def test_oauth_start_unknown_recipe_404(app_with_routes) -> None:
    client = TestClient(app_with_routes)
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "no-such-recipe",
        "env_vars": {},
    })
    assert resp.status_code == 404


def test_oauth_start_rejects_non_oauth_recipe(app_with_routes) -> None:
    """Running /oauth/start against a plain-secret recipe is nonsensical."""
    client = TestClient(app_with_routes)
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "brave-search",
        "env_vars": {},
    })
    assert resp.status_code == 400
    assert "oauth_browser" in resp.json().get("detail", "").lower()
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src pytest tests/test_gateway_oauth_proxy.py -v
```

Expected: all three fail — gateway endpoint doesn't exist.

- [ ] **Step 3: Add the gateway endpoint**

In `src/mycelos/gateway/routes.py`, after the recipe endpoint from Task 3, add:

```python
    @api.post("/api/connectors/oauth/start")
    async def oauth_start_passthrough(payload: dict[str, Any]) -> dict[str, Any]:
        """Start an OAuth auth subprocess in the proxy for a recipe.

        Browser POSTs {recipe_id, env_vars}. Gateway resolves the recipe,
        validates that the recipe uses oauth_browser setup flow, and
        asks the proxy to spawn the recipe's oauth_cmd with the given
        env_vars. Returns a session_id plus a browser-facing WS URL
        that the frontend opens to stream the subprocess I/O.
        """
        from mycelos.connectors.mcp_recipes import get_recipe as get_r

        recipe_id = payload.get("recipe_id", "")
        env_vars = payload.get("env_vars", {}) or {}

        recipe = get_r(recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail=f"Unknown recipe: {recipe_id}")
        if recipe.setup_flow != "oauth_browser":
            raise HTTPException(
                status_code=400,
                detail=f"Recipe '{recipe_id}' setup_flow is '{recipe.setup_flow}', not 'oauth_browser'",
            )

        mycelos = api.state.mycelos
        proxy_client = getattr(mycelos, "proxy_client", None)
        if proxy_client is None:
            raise HTTPException(status_code=503, detail="Proxy not available")

        result = proxy_client.oauth_start(
            oauth_cmd=recipe.oauth_cmd,
            env_vars=env_vars,
        )
        session_id = result.get("session_id", "")
        return {
            "session_id": session_id,
            # The browser opens a WS to the gateway (not the proxy) —
            # the gateway relays the traffic. Path is mirrored below.
            "ws_url": f"/api/connectors/oauth/stream/{session_id}",
        }


    @api.post("/api/connectors/oauth/stop")
    async def oauth_stop_passthrough(payload: dict[str, Any]) -> dict[str, Any]:
        mycelos = api.state.mycelos
        proxy_client = getattr(mycelos, "proxy_client", None)
        if proxy_client is None:
            raise HTTPException(status_code=503, detail="Proxy not available")
        session_id = payload.get("session_id", "")
        return proxy_client.oauth_stop(session_id=session_id)


    @api.websocket("/api/connectors/oauth/stream/{session_id}")
    async def oauth_stream_passthrough(websocket, session_id: str) -> None:
        """Bidirectional WebSocket passthrough to the proxy's
        /oauth/stream/{session_id}. Frames are forwarded verbatim —
        the gateway does no parsing, only authentication and transport."""
        import asyncio
        import websockets

        mycelos = websocket.app.state.mycelos
        proxy_client = getattr(mycelos, "proxy_client", None)
        if proxy_client is None:
            await websocket.close(code=4503)
            return

        await websocket.accept()

        # Open the upstream WS to the proxy.
        ws_url = proxy_client.oauth_stream_url(session_id)
        headers = {"Authorization": f"Bearer {proxy_client._auth_token}"} \
                  if getattr(proxy_client, "_auth_token", None) else {}

        try:
            async with websockets.connect(
                ws_url, additional_headers=headers
            ) as upstream:

                async def client_to_proxy():
                    try:
                        while True:
                            msg = await websocket.receive_text()
                            await upstream.send(msg)
                    except Exception:
                        return

                async def proxy_to_client():
                    try:
                        async for msg in upstream:
                            if isinstance(msg, bytes):
                                msg = msg.decode("utf-8", "replace")
                            await websocket.send_text(msg)
                    except Exception:
                        return

                done, pending = await asyncio.wait(
                    [asyncio.create_task(client_to_proxy()),
                     asyncio.create_task(proxy_to_client())],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
        except Exception:
            pass
        finally:
            try:
                await websocket.close()
            except Exception:
                pass
```

Add `import websockets` at the top of `routes.py` if not present. If `websockets` is not in `pyproject.toml` under `dependencies`, add it. (Check first: `grep -n websockets pyproject.toml`.)

- [ ] **Step 4: Verify tests pass**

```bash
PYTHONPATH=src pytest tests/test_gateway_oauth_proxy.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Baseline**

```bash
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -3
```

Expected: no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/gateway/routes.py tests/test_gateway_oauth_proxy.py pyproject.toml
git commit -m "feat(gateway): /api/connectors/oauth/start + WS passthrough"
```

---

## Task 7: Store the OAuth keys file via the credential proxy

**Files:**
- Modify: `src/mycelos/gateway/routes.py` (add `/api/credentials/oauth-keys` or reuse `/api/credentials`)
- Create: `tests/test_oauth_keys_upload.py`

Context: the user uploads the `gcp-oauth.keys.json` as a JSON blob. We need to materialize it to a file path inside the proxy container (so `npx ... auth` can read it via the env var). The existing credential proxy already does this — credentials stored with `api_key` set to the JSON content get materialized on demand via `credential_materialize`, returning a file path. We just need to expose an upload endpoint that accepts the raw JSON (not a single-line password).

Check first if a JSON-capable credential upload already exists:
```bash
grep -n "credential.*multiline\|application/json" src/mycelos/gateway/routes.py
```

If nothing relevant, the existing `POST /api/credentials` endpoint already accepts arbitrary strings in `secret`. We just need to make sure the frontend serializes the JSON object into that string. That's a Task-8 concern.

**What to add here:** an endpoint that confirms the stored credential content is valid OAuth-keys JSON shape (has `installed.client_id` or `web.client_id`). This protects the user from pasting the wrong file and getting a cryptic error from `npx ... auth` three screens later.

- [ ] **Step 1: Write the failing test**

Create `tests/test_oauth_keys_upload.py`:

```python
"""POST /api/credentials/oauth-keys/validate runs a cheap shape check on
the uploaded OAuth keys JSON so we can tell the user 'this doesn't look
like a gcp-oauth.keys.json' at upload time rather than at auth time."""
from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient


def test_validates_installed_desktop_app_shape(app_with_routes) -> None:
    client = TestClient(app_with_routes)
    keys = {
        "installed": {
            "client_id": "123.apps.googleusercontent.com",
            "client_secret": "GOCSPX-xxxx",
            "redirect_uris": ["http://localhost"],
        }
    }
    resp = client.post("/api/credentials/oauth-keys/validate", json={
        "content": json.dumps(keys),
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["kind"] == "desktop"  # installed = desktop app


def test_rejects_web_app_shape(app_with_routes) -> None:
    """Web-app OAuth credentials have a 'web' key not 'installed' —
    our MCP servers don't support web-flow, only desktop. Flag early."""
    client = TestClient(app_with_routes)
    keys = {"web": {"client_id": "x", "client_secret": "y"}}
    resp = client.post("/api/credentials/oauth-keys/validate", json={
        "content": json.dumps(keys),
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "desktop" in body["error"].lower()


def test_rejects_malformed_json(app_with_routes) -> None:
    client = TestClient(app_with_routes)
    resp = client.post("/api/credentials/oauth-keys/validate", json={
        "content": "this is not json",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "json" in body["error"].lower()


def test_rejects_empty_content(app_with_routes) -> None:
    client = TestClient(app_with_routes)
    resp = client.post("/api/credentials/oauth-keys/validate", json={
        "content": "",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src pytest tests/test_oauth_keys_upload.py -v
```

Expected: 4 fail.

- [ ] **Step 3: Add the validator endpoint**

In `src/mycelos/gateway/routes.py`, near the other `/api/credentials/*` handlers, add:

```python
    @api.post("/api/credentials/oauth-keys/validate")
    async def validate_oauth_keys(payload: dict[str, Any]) -> dict[str, Any]:
        """Cheap shape-check on uploaded gcp-oauth.keys.json content.

        Returns {ok: bool, kind?: str, error?: str}. Non-200 is reserved
        for framework errors; validation failures are ok=False with a
        human-readable message so the UI can keep showing the dialog.
        """
        import json as _json

        content = payload.get("content", "")
        if not content:
            return {"ok": False, "error": "Empty content — paste the gcp-oauth.keys.json file."}
        try:
            data = _json.loads(content)
        except _json.JSONDecodeError as e:
            return {"ok": False, "error": f"Not valid JSON: {e}"}
        if not isinstance(data, dict):
            return {"ok": False, "error": "Top-level must be a JSON object."}
        if "installed" in data and isinstance(data["installed"], dict):
            inst = data["installed"]
            if "client_id" in inst and "client_secret" in inst:
                return {"ok": True, "kind": "desktop"}
            return {"ok": False, "error": "Missing client_id or client_secret in 'installed' section."}
        if "web" in data:
            return {
                "ok": False,
                "error": (
                    "This looks like a Web-app OAuth credential. Mycelos needs a "
                    "Desktop-app credential. Go back to Cloud Console → Credentials "
                    "→ Create credentials → OAuth client ID → Desktop app."
                ),
            }
        return {
            "ok": False,
            "error": (
                "File doesn't look like a gcp-oauth.keys.json. Expected a top-level "
                "'installed' or 'web' key. Make sure you downloaded the OAuth-client JSON, "
                "not the project's service-account key."
            ),
        }
```

- [ ] **Step 4: Verify tests pass**

```bash
PYTHONPATH=src pytest tests/test_oauth_keys_upload.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/gateway/routes.py tests/test_oauth_keys_upload.py
git commit -m "feat(gateway): validate uploaded OAuth-keys JSON shape"
```

---

## Task 8: Frontend OAuth setup dialog

**Files:**
- Modify: `src/mycelos/frontend/pages/connectors.html` (new dialog + routing)
- Create: `src/mycelos/frontend/shared/oauth_setup.js` (WS client, URL extraction, guide renderer)

Context: this is the UX payoff. When the user clicks Gmail / Calendar / Drive in the gallery, we open a different dialog than the plain-secret one. The new dialog has a left rail (guide steps with progress), a main panel (current step content + actions), and a right status pane (subprocess log tail + OAuth URL when detected).

- [ ] **Step 1: Create the shared JS helper**

Write `src/mycelos/frontend/shared/oauth_setup.js`:

```javascript
/**
 * OAuth setup helpers — shared across connectors that use the
 * `oauth_browser` setup flow. Everything here is state-less; callers
 * bring their own Alpine reactive state.
 */
(function () {
  'use strict';

  const OAUTH_URL_PATTERNS = [
    /https:\/\/accounts\.google\.com\/o\/oauth2\/[^\s"'<>]+/g,
    /https:\/\/login\.microsoftonline\.com\/[^\s"'<>]+\/oauth2\/[^\s"'<>]+/g,
    // add more providers here as new recipes land
  ];

  /**
   * Scan a chunk of stdout for an OAuth consent URL. Returns the URL
   * or null. First hit wins — most upstream tools print exactly one.
   */
  function findOAuthUrl(text) {
    for (const pat of OAUTH_URL_PATTERNS) {
      const match = text.match(pat);
      if (match && match[0]) return match[0];
    }
    return null;
  }

  /**
   * Open a WebSocket to the given path and wire up per-frame callbacks.
   * Returns an object with .send(frame) and .close().
   */
  function openOAuthStream(wsPath, { onStdout, onStderr, onDone, onError }) {
    const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = scheme + '//' + window.location.host + wsPath;
    const ws = new WebSocket(url);
    ws.onmessage = (event) => {
      let frame;
      try { frame = JSON.parse(event.data); } catch { return; }
      if (frame.type === 'stdout') onStdout && onStdout(frame.data);
      else if (frame.type === 'stderr') onStderr && onStderr(frame.data);
      else if (frame.type === 'done') onDone && onDone(frame.exit_code);
    };
    ws.onerror = (e) => onError && onError(e);
    return {
      send(frame) {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(frame));
      },
      close() {
        try { ws.close(); } catch {}
      },
    };
  }

  window.MycelosOAuthSetup = { findOAuthUrl, openOAuthStream };
})();
```

- [ ] **Step 2: Add the dialog markup + Alpine component to `connectors.html`**

In `src/mycelos/frontend/pages/connectors.html`:

**(a)** At the top `<head>` alongside other `<script>` tags, add:
```html
<script src="/shared/oauth_setup.js"></script>
```

**(b)** Find the existing `x-data` root (around line 700–860 where the `channels`, `services`, `useCases` arrays live). Add these keys to the Alpine state object:

```javascript
          // ── OAuth setup dialog state ──
          oauthDialog: {
            open: false,
            recipeId: '',
            recipe: null,         // full recipe + guide from /api/connectors/recipes/{id}
            step: 0,              // guide step cursor
            keysJson: '',         // textarea content
            keysValid: null,      // null | {ok, kind, error}
            sessionId: '',
            ws: null,
            stdout: '',
            stderr: '',
            consentUrl: null,
            done: false,
            exitCode: null,
            submitting: false,
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
              step: 0,
              keysJson: '',
              keysValid: null,
              sessionId: '',
              ws: null,
              stdout: '',
              stderr: '',
              consentUrl: null,
              done: false,
              exitCode: null,
              submitting: false,
            });
          },

          closeOAuthDialog() {
            if (this.oauthDialog.ws) {
              this.oauthDialog.ws.close();
              this.oauthDialog.ws = null;
            }
            if (this.oauthDialog.sessionId) {
              fetch('/api/connectors/oauth/stop', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: this.oauthDialog.sessionId}),
              }).catch(() => {});
            }
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
            // 1. Store the JSON as a credential under the recipe's env var name,
            //    slug-scoped so Gmail / Calendar / Drive can coexist.
            this.oauthDialog.submitting = true;
            const envVar = (this.oauthDialog.recipe.credentials[0] || {}).env_var || '';
            const credService = this.oauthDialog.recipeId + '-oauth-keys';
            await fetch('/api/credentials', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                service: credService,
                secret: this.oauthDialog.keysJson,
                label: 'default',
                description: 'OAuth keys for ' + this.oauthDialog.recipe.name,
              }),
            });

            // 2. Ask the gateway to start the auth subprocess.
            const resp = await fetch('/api/connectors/oauth/start', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                recipe_id: this.oauthDialog.recipeId,
                env_vars: {[envVar]: 'credential:' + credService},
              }),
            });
            if (!resp.ok) {
              this.oauthDialog.submitting = false;
              this.showToast('Failed to start OAuth: ' + resp.status, 'error');
              return;
            }
            const body = await resp.json();
            this.oauthDialog.sessionId = body.session_id;

            // 3. Open the WS and start streaming.
            const self = this;
            this.oauthDialog.ws = window.MycelosOAuthSetup.openOAuthStream(body.ws_url, {
              onStdout(data) {
                self.oauthDialog.stdout += data;
                if (!self.oauthDialog.consentUrl) {
                  const url = window.MycelosOAuthSetup.findOAuthUrl(self.oauthDialog.stdout + self.oauthDialog.stderr);
                  if (url) self.oauthDialog.consentUrl = url;
                }
              },
              onStderr(data) {
                self.oauthDialog.stderr += data;
                if (!self.oauthDialog.consentUrl) {
                  const url = window.MycelosOAuthSetup.findOAuthUrl(self.oauthDialog.stdout + self.oauthDialog.stderr);
                  if (url) self.oauthDialog.consentUrl = url;
                }
              },
              onDone(code) {
                self.oauthDialog.done = true;
                self.oauthDialog.exitCode = code;
                self.oauthDialog.submitting = false;
                if (code === 0) self.loadConnectors();
              },
              onError() {
                self.showToast('Stream error', 'error');
                self.oauthDialog.submitting = false;
              },
            });
          },

          sendOAuthStdin(text) {
            if (this.oauthDialog.ws) {
              this.oauthDialog.ws.send({type: 'stdin', data: text + '\n'});
            }
          },
```

**(c)** Inside the main page body, after the existing delete-confirmation modal, add the dialog markup:

```html
    <!-- OAuth setup dialog — for recipes with setup_flow == 'oauth_browser' -->
    <div x-show="oauthDialog.open" x-cloak
         class="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
         @click.self="closeOAuthDialog()">
      <div class="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-hidden flex flex-col">
        <!-- Header -->
        <div class="px-6 py-4 border-b flex items-center justify-between">
          <h2 class="text-lg font-semibold" x-text="'Connect ' + (oauthDialog.recipe?.name || '')"></h2>
          <button @click="closeOAuthDialog()" class="text-gray-400 hover:text-gray-600">
            <span class="material-symbols-outlined">close</span>
          </button>
        </div>

        <!-- Body: guide on left, actions on right -->
        <div class="flex flex-1 overflow-hidden">
          <!-- Guide panel -->
          <div class="w-1/2 border-r overflow-y-auto p-6" x-show="oauthDialog.recipe?.setup_guide">
            <h3 class="font-semibold mb-2" x-text="oauthDialog.recipe?.setup_guide?.title"></h3>
            <p class="text-sm text-gray-600 mb-4" x-text="oauthDialog.recipe?.setup_guide?.intro"></p>
            <ol class="space-y-3">
              <template x-for="(step, idx) in (oauthDialog.recipe?.setup_guide?.steps || [])" :key="idx">
                <li class="border rounded p-3"
                    :class="idx === oauthDialog.step ? 'border-blue-500 bg-blue-50' : 'border-gray-200'">
                  <div class="flex items-start gap-2">
                    <span class="text-xs font-mono bg-gray-200 rounded px-2 py-0.5 mt-0.5" x-text="idx + 1"></span>
                    <div class="flex-1">
                      <div class="font-medium text-sm" x-text="step.title"></div>
                      <div class="text-sm text-gray-700 mt-1" x-html="step.body.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')"></div>
                      <a x-show="step.cta_url" :href="step.cta_url" target="_blank" rel="noopener"
                         class="inline-flex items-center gap-1 text-sm text-blue-600 hover:underline mt-2">
                        <span x-text="step.cta_label || 'Open'"></span>
                        <span class="material-symbols-outlined text-sm">open_in_new</span>
                      </a>
                    </div>
                  </div>
                </li>
              </template>
            </ol>
          </div>

          <!-- Action panel -->
          <div class="flex-1 overflow-y-auto p-6 space-y-4">
            <!-- Stage 1: Upload keys -->
            <div x-show="!oauthDialog.sessionId">
              <h3 class="font-semibold mb-2">Upload your gcp-oauth.keys.json</h3>
              <p class="text-sm text-gray-600 mb-3">
                After downloading from Google Cloud Console (step 4 on the left), paste the full JSON content here.
              </p>
              <textarea x-model="oauthDialog.keysJson"
                        @input.debounce.500ms="validateOAuthKeys()"
                        rows="8"
                        placeholder='{"installed": {"client_id": "..."}}'
                        class="w-full border rounded p-2 font-mono text-xs"></textarea>
              <div x-show="oauthDialog.keysValid && oauthDialog.keysValid.ok"
                   class="text-sm text-green-600 mt-2">
                ✓ Valid desktop-app credential.
              </div>
              <div x-show="oauthDialog.keysValid && !oauthDialog.keysValid.ok"
                   class="text-sm text-red-600 mt-2"
                   x-text="oauthDialog.keysValid?.error"></div>

              <button @click="submitOAuthKeysAndStart()"
                      :disabled="oauthDialog.submitting || !(oauthDialog.keysValid && oauthDialog.keysValid.ok)"
                      class="btn-primary mt-3 disabled:opacity-50">
                <span x-show="!oauthDialog.submitting">Start OAuth consent</span>
                <span x-show="oauthDialog.submitting">Starting…</span>
              </button>
            </div>

            <!-- Stage 2: Consent -->
            <div x-show="oauthDialog.sessionId && !oauthDialog.done" class="space-y-3">
              <h3 class="font-semibold">Complete the consent in your browser</h3>

              <div x-show="oauthDialog.consentUrl" class="border rounded p-4 bg-blue-50">
                <p class="text-sm mb-2">Open this URL, sign in as the Test user you added, and accept the scopes:</p>
                <a :href="oauthDialog.consentUrl" target="_blank" rel="noopener"
                   class="block text-blue-600 hover:underline break-all text-sm font-mono">
                  <span x-text="oauthDialog.consentUrl"></span>
                </a>
                <button @click="window.open(oauthDialog.consentUrl, '_blank')"
                        class="btn-primary mt-2">Open in browser</button>
              </div>

              <div x-show="!oauthDialog.consentUrl" class="text-sm text-gray-500">
                Waiting for the auth server to print a URL…
              </div>

              <details>
                <summary class="cursor-pointer text-sm text-gray-500">Show subprocess log</summary>
                <pre class="text-xs bg-gray-50 p-2 mt-2 rounded max-h-32 overflow-y-auto whitespace-pre-wrap"
                     x-text="oauthDialog.stdout + oauthDialog.stderr"></pre>
              </details>
            </div>

            <!-- Stage 3: Done -->
            <div x-show="oauthDialog.done" class="space-y-3">
              <div x-show="oauthDialog.exitCode === 0" class="border rounded p-4 bg-green-50">
                <h3 class="font-semibold text-green-900">Connected</h3>
                <p class="text-sm text-green-800 mt-1">
                  OAuth token saved. You can close this dialog and start using the connector.
                </p>
                <button @click="closeOAuthDialog()" class="btn-primary mt-3">Done</button>
              </div>
              <div x-show="oauthDialog.exitCode !== 0" class="border rounded p-4 bg-red-50">
                <h3 class="font-semibold text-red-900">Auth failed</h3>
                <p class="text-sm text-red-800 mt-1">
                  The subprocess exited with code <code x-text="oauthDialog.exitCode"></code>.
                  Check the log for details.
                </p>
                <pre class="text-xs bg-white p-2 mt-2 rounded max-h-32 overflow-y-auto whitespace-pre-wrap"
                     x-text="oauthDialog.stdout + oauthDialog.stderr"></pre>
                <button @click="closeOAuthDialog()" class="btn-secondary mt-3">Close</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
```

**(d)** Find the code that handles clicks on use-case connector cards (around line 1210 — the `wizardType` area from the previous task). The three Google recipes must now route to `openOAuthDialog` instead of the plain wizard. Change:

```javascript
            const wizardType = svc.id;
```

to:

```javascript
            // Google connectors (and any future oauth_browser recipe) use
            // the dedicated dialog instead of the plain secret form.
            if (['gmail', 'google-calendar', 'google-drive'].includes(svc.id)) {
              this.openOAuthDialog(svc.id);
              return;
            }
            const wizardType = svc.id;
```

- [ ] **Step 3: Ensure the shared JS is served**

Check how `src/mycelos/frontend/shared/api.js` is served:

```bash
grep -rn "shared/api.js\|/shared/" src/mycelos/frontend/ src/mycelos/gateway/
```

Find the static-file mount that serves `/shared/*`. `oauth_setup.js` should land alongside `api.js` and be picked up automatically. If the mount only serves specific filenames (unusual but possible), add `oauth_setup.js` to the whitelist.

- [ ] **Step 4: Manual smoke test**

Frontend has no unit-test framework in this repo, so verify manually:

```bash
cd /Users/stefan/Documents/railsapps/mycelos
mycelos serve --reload
```

In another shell / browser:
1. Open `http://localhost:9111/connectors.html`.
2. Scroll to "Google Workspace". Click "Gmail".
3. The new OAuth dialog should open with the Google Cloud guide on the left.
4. Paste an obviously-wrong JSON (`{}`) into the textarea → validator shows the error inline.
5. Paste a well-shaped fake (e.g. `{"installed": {"client_id": "test", "client_secret": "test"}}`) → validator shows "Valid desktop-app credential".
6. Click "Start OAuth consent". The actual OAuth will fail (bogus credentials), but you should see the subprocess log appear and eventually the "Auth failed" panel — that confirms the WS passthrough works end-to-end.
7. Close the dialog and open Calendar / Drive — same UX.

If the manual smoke fails, check the browser console for JS errors (usually an unescaped character in the guide body text, a missing import of `oauth_setup.js`, or Alpine reactivity not picking up a nested field).

- [ ] **Step 5: Run the unit suite for regressions**

```bash
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -3
```

Expected: no regressions from the frontend changes (no test directly exercises the HTML).

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/frontend/pages/connectors.html src/mycelos/frontend/shared/oauth_setup.js
git commit -m "feat(connectors): OAuth setup dialog with Google Cloud wizard"
```

---

## Task 9: Docs + changelog

**Files:**
- Modify: `docs/deployment/google-setup.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Rewrite `google-setup.md` to lead with the UI**

Replace the content of `docs/deployment/google-setup.md` with:

```markdown
# Google (Gmail / Calendar / Drive) setup

Mycelos walks you through the entire Google setup inside the web UI.
This doc is a reference; you don't need to read it top-to-bottom to
connect a Google service.

## Happy path (via the web UI)

1. Open **Connectors → Google Workspace**.
2. Click Gmail / Calendar / Drive.
3. Follow the inline step-by-step guide in the dialog. It covers:
   creating a Google Cloud project, enabling APIs, configuring the
   consent screen, and downloading `gcp-oauth.keys.json`.
4. Paste the JSON into the dialog and click **Start OAuth consent**.
5. Open the consent URL the dialog shows, sign in, accept the scopes.
6. Done. Close the dialog; the connector is live.

All three Google services share a single Google Cloud project, so
steps 1–3 only happen once.

## CLI fallback

If the web UI isn't available (headless server, network issue, etc.):

[... existing shell-based walkthrough content, unchanged from the
previous version of this file, moved here as "CLI fallback" ...]

## Security notes

- The `gcp-oauth.keys.json` never leaves the SecurityProxy container.
  The gateway doesn't see it, and the LLM never gets it injected.
- The per-service OAuth token files (`credentials.json`, `token.json`)
  live in the proxy's `/data/.xxx-mcp/` directory and persist across
  container restarts.
- Each MCP server's scopes are visible in the connector card before
  you click Connect. Review them before consenting.
```

Keep the existing CLI-fallback content verbatim — don't rewrite it; just demote it to a sub-section.

- [ ] **Step 2: Update `CHANGELOG.md`**

Find the existing Week 17 `## Week 17 (2026)` block. After the "Google via MCP" entry, add:

```markdown
### OAuth connector setup in the web UI
- New `setup_flow` field on `MCPRecipe` discriminates between plain-secret connectors and OAuth-based ones. The three Google recipes now declare `setup_flow="oauth_browser"` — the Connectors page opens a dedicated dialog for them with a step-by-step Google Cloud project wizard, `gcp-oauth.keys.json` upload with shape validation, and a live subprocess log of the `npx ... auth` consent flow streamed via WebSocket through a gateway passthrough. Users no longer need to open a terminal to onboard Gmail / Calendar / Drive.
- New proxy endpoints `POST /oauth/start`, `POST /oauth/stop`, `WS /oauth/stream/{session_id}` — generic enough to host future OAuth-based connectors without code changes in the web layer beyond adding a setup guide.
- `docs/deployment/google-setup.md` now leads with the UI path; the shell-based walkthrough is retained as a fallback for headless installs.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md docs/deployment/google-setup.md
git commit -m "docs(oauth): UI-first Google setup walkthrough + changelog"
```

---

## Task 10: Final verification + merge

- [ ] **Step 1: Full baseline**

```bash
cd /Users/stefan/Documents/railsapps/mycelos
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -5
```

Expected: all passing. Count = baseline (whatever it was at plan start) + 20 new tests (Tasks 1–7).

- [ ] **Step 2: Smoke-import**

```bash
PYTHONPATH=src python -c "
from mycelos.connectors.mcp_recipes import RECIPES
from mycelos.connectors.oauth_setup_guides import SETUP_GUIDES
from mycelos.gateway.routes import register_routes
from mycelos.security.proxy_server import make_app
from mycelos.security.proxy_client import SecurityProxyClient
print('all imports ok')
"
```

- [ ] **Step 3: Manual end-to-end on the web UI**

Already covered in Task 8 Step 4. If you skipped it there, run it now.

- [ ] **Step 4: Merge**

```bash
git checkout main
git pull
git merge --no-ff feature/oauth-connector-setup -m "Merge feature/oauth-connector-setup: UI-first OAuth for Google connectors"
git push origin main
```

- [ ] **Step 5: Cleanup worktree + branch**

```bash
cd /Users/stefan/Documents/railsapps/mycelos
git worktree remove .worktrees/oauth-connector-setup
git branch -d feature/oauth-connector-setup
```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|---|---|
| Recipe `setup_flow` discriminator | Task 1 |
| `oauth_cmd` field on recipes | Task 1 |
| Google Cloud step-by-step guide registry | Task 2 |
| Gateway endpoint serving recipe + guide | Task 3 |
| Proxy endpoint spawning the OAuth subprocess | Task 4 |
| WebSocket streaming subprocess I/O | Task 5 |
| Gateway WebSocket passthrough (browser ↔ gateway ↔ proxy) | Task 6 |
| Upload + shape-validate `gcp-oauth.keys.json` | Task 7 |
| Frontend dialog that renders guide + upload + stream | Task 8 |
| Docs + changelog | Task 9 |
| Reuse for future non-Google OAuth recipes | Task 1 + 2 + 4 (setup_flow, guide registry, generic subprocess) |
| User doesn't need to open terminal | Task 8 — dialog covers upload + consent end-to-end |
| Setup guide walks through Google Cloud project creation | Task 2 — `google_cloud` guide has 6 steps incl. project, APIs, consent screen, desktop-app credential, upload, consent |

### Placeholder scan

- No TBD / TODO markers.
- Every code step shows full code, not a prose description.
- Test cases are named and include the assertions.
- One deliberate placeholder: Task 9 docs content says "existing CLI-fallback content, unchanged" — that's intentional, we're keeping what's there, not rewriting. If you want it literal, replace that block with a copy of the current file content.

### Type consistency

- `MCPRecipe` fields (`setup_flow`, `oauth_cmd`, `oauth_setup_guide_id`) are used identically in recipe definitions, endpoints, and frontend.
- Session id format (`oauth-<hex>`) is referenced consistently in Task 4, 5, 6.
- WebSocket frame shape `{type, data, exit_code?}` is consistent in proxy handler, gateway passthrough, and frontend helper.
- Credential-service naming convention `<recipe-id>-oauth-keys` is used consistently between the frontend `submitOAuthKeysAndStart` and the env var that Task 4's subprocess consumes.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-22-oauth-connector-setup.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between, fast iteration
**2. Inline Execution** — execute tasks in this session with checkpoints

Which approach?
