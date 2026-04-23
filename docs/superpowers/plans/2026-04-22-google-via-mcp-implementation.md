# Google Tools via MCP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken in-process `gog`-CLI Google integration with three MCP-server recipes (Gmail, Google Calendar, Google Drive), so Gmail search/send, Calendar CRUD, and Drive read/search work again under two-container deployment.

**Architecture:** Each Google service becomes its own MCP subprocess spawned and supervised by the SecurityProxy container (same shape as the email recipe from `7d63b7c`). The gateway never sees OAuth tokens or the `gcp-oauth.keys.json` credential file — both live in the proxy's bind-mounted data directory. Tool calls reach the servers through the existing `proxy_client.mcp_call` path; no gateway-side code paths change beyond registry plumbing.

**Tech Stack:** Python 3.12+, FastAPI (gateway), npm packages via `npx -y`:
- `@gongrzhe/server-gmail-autoauth-mcp` — Gmail (search, read, send, filters, labels, attachments)
- `@cocal/google-calendar-mcp` — Calendar (list, create, update, delete events)
- `@piotr-agier/google-drive-mcp` — Drive (list, read, search, upload)

All three use a shared `gcp-oauth.keys.json` (Google Cloud OAuth 2.0 desktop-app credential) plus per-service auth tokens stored in the proxy container's `/data/.google-mcp/` directory.

---

## Spec reference

Source: `docs/superpowers/plans/2026-04-22-google-via-mcp.md` (the design doc).
This plan implements that spec end-to-end.

## File structure

| File | Responsibility |
|---|---|
| `src/mycelos/connectors/mcp_recipes.py` | Add three recipes; remove stale `gmail` (gog CLI) and stale `google-drive` (dead npm package) entries |
| `src/mycelos/connectors/google_tools.py` | **Delete** — replaced by MCP servers |
| `src/mycelos/connectors/registry.py` | Remove the five `google.*` tool registrations; remove the `google_tools` import |
| `src/mycelos/frontend/pages/connectors.html` | Replace the single "Gmail API" service entry and the stale `google-drive` connector card with a new "Google" category containing three entries |
| `docs/deployment/google-setup.md` | New — walks through Google Cloud OAuth keys setup, first-time auth via `mycelos shell`, and how to share one OAuth project across the three services |
| `tests/integration/test_gmail_mcp_live.py` | New — live smoke test mirroring `test_email_mcp_live.py` (spawn server, initialize, tools/list, one tool call) |
| `tests/integration/test_google_calendar_mcp_live.py` | New — same pattern for Calendar |
| `tests/integration/test_google_drive_mcp_live.py` | New — same pattern for Drive |
| `tests/test_mcp_recipes_google.py` | New — unit test: the three recipes exist in `RECIPES`, point at the right npm packages, declare the right env vars |
| `CHANGELOG.md` | New "Google via MCP" block under Week 17 |

**Note on the recipe dataclass:** the existing `MCPRecipe` already carries `credentials`, `static_env`, `transport`, and `capabilities_preview`. No dataclass change is needed.

---

## Task 1: Add the three Google MCP recipes

**Files:**
- Modify: `src/mycelos/connectors/mcp_recipes.py:121-178` (replace stale `gmail` + `google-drive`)
- Test: `tests/test_mcp_recipes_google.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_recipes_google.py`:

```python
"""The three Google MCP recipes (gmail, google-calendar, google-drive) must
be present in RECIPES with the right npm packages, env vars, and static_env.
If upstream renames a package or changes an env var name, this test fails
before a user hits a 'server not found' runtime error."""
from __future__ import annotations

from mycelos.connectors.mcp_recipes import RECIPES


def test_gmail_recipe_points_at_gongrzhe_autoauth() -> None:
    r = RECIPES.get("gmail")
    assert r is not None, "gmail recipe must exist"
    assert "@gongrzhe/server-gmail-autoauth-mcp" in r.command
    assert r.transport == "stdio"
    envs = {c["env_var"] for c in r.credentials}
    # The server reads the oauth-keys JSON path from GMAIL_OAUTH_PATH
    # (see upstream README). We store the JSON blob itself via that
    # credential so Mycelos materializes it to disk in the proxy.
    assert "GMAIL_OAUTH_PATH" in envs
    assert r.category == "communication"


def test_google_calendar_recipe_points_at_cocal() -> None:
    r = RECIPES.get("google-calendar")
    assert r is not None, "google-calendar recipe must exist"
    assert "@cocal/google-calendar-mcp" in r.command
    assert r.transport == "stdio"
    envs = {c["env_var"] for c in r.credentials}
    assert "GOOGLE_OAUTH_CREDENTIALS" in envs
    assert r.category == "communication"


def test_google_drive_recipe_points_at_piotr_agier() -> None:
    r = RECIPES.get("google-drive")
    assert r is not None, "google-drive recipe must exist"
    assert "@piotr-agier/google-drive-mcp" in r.command
    assert r.transport == "stdio"
    envs = {c["env_var"] for c in r.credentials}
    assert "GDRIVE_OAUTH_PATH" in envs
    assert r.category == "storage"


def test_stale_gog_gmail_recipe_is_gone() -> None:
    """The pre-MCP `gmail` recipe pointed at the gog CLI via
    transport='builtin' — that code path is being deleted. Make sure
    nothing still claims the gog shape for gmail."""
    r = RECIPES["gmail"]
    assert r.transport != "builtin"
    assert "gog" not in (r.command or "").lower()


def test_stale_google_drive_npm_package_is_gone() -> None:
    """The pre-MCP `google-drive` recipe pointed at
    @modelcontextprotocol/server-google-drive, which was never
    published. Make sure the replacement is in place."""
    r = RECIPES["google-drive"]
    assert "@modelcontextprotocol/server-google-drive" not in r.command
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_recipes_google.py -v`
Expected: all five tests FAIL — the current `gmail` uses `transport="builtin"` with `command=""` (no npm package), and `google-drive` points at `@modelcontextprotocol/server-google-drive`.

- [ ] **Step 3: Replace the stale recipes**

In `src/mycelos/connectors/mcp_recipes.py`, remove the existing `"gmail"` (lines 164–178) and `"google-drive"` (lines 121–133) entries. Insert these three in their place:

```python
    "gmail": MCPRecipe(
        id="gmail",
        name="Gmail (via Google API)",
        description=(
            "Gmail with the full API surface — search, read, send, reply, "
            "filters, labels, attachments. Runs as an MCP server inside the "
            "SecurityProxy; OAuth tokens never leave the proxy container."
        ),
        command="npx -y @gongrzhe/server-gmail-autoauth-mcp",
        transport="stdio",
        credentials=[{
            "env_var": "GMAIL_OAUTH_PATH",
            "name": "Google Cloud OAuth credentials (JSON)",
            "help": (
                "Paste the contents of gcp-oauth.keys.json from the Google "
                "Cloud Console (OAuth 2.0 Desktop app). First run triggers "
                "a browser consent — see docs/deployment/google-setup.md."
            ),
        }],
        capabilities_preview=[
            "gmail_search", "gmail_read", "gmail_send", "gmail_labels",
            "gmail_filters", "gmail_attachments",
        ],
        category="communication",
        requires_node=True,
    ),
    "google-calendar": MCPRecipe(
        id="google-calendar",
        name="Google Calendar",
        description=(
            "Read, create, update, and delete Calendar events across all of "
            "the user's calendars. Same OAuth project as Gmail/Drive but a "
            "separate consent flow per service."
        ),
        command="npx -y @cocal/google-calendar-mcp",
        transport="stdio",
        credentials=[{
            "env_var": "GOOGLE_OAUTH_CREDENTIALS",
            "name": "Google Cloud OAuth credentials (JSON)",
            "help": (
                "Reuse the same gcp-oauth.keys.json from Gmail. First run "
                "opens a browser for Calendar scope consent."
            ),
        }],
        capabilities_preview=[
            "list_calendars", "list_events", "create_event",
            "update_event", "delete_event",
        ],
        category="communication",
        requires_node=True,
    ),
    "google-drive": MCPRecipe(
        id="google-drive",
        name="Google Drive",
        description=(
            "List, read, search, and upload files in Google Drive. Shares "
            "the OAuth project with Gmail and Calendar; each service runs "
            "its own consent flow."
        ),
        command="npx -y @piotr-agier/google-drive-mcp",
        transport="stdio",
        credentials=[{
            "env_var": "GDRIVE_OAUTH_PATH",
            "name": "Google Cloud OAuth credentials (JSON)",
            "help": (
                "Reuse the same gcp-oauth.keys.json. First run opens a "
                "browser for Drive scope consent."
            ),
        }],
        capabilities_preview=[
            "drive_list", "drive_read", "drive_search", "drive_upload",
        ],
        category="storage",
        requires_node=True,
    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_recipes_google.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/connectors/mcp_recipes.py tests/test_mcp_recipes_google.py
git commit -m "feat(mcp): add gmail/calendar/drive MCP recipes; retire gog-era entries"
```

---

## Task 2: Remove `google_tools.py` + registry bindings

**Files:**
- Delete: `src/mycelos/connectors/google_tools.py`
- Modify: `src/mycelos/connectors/registry.py` (lines 7–13 import, lines 91–131 tool registrations)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcp_recipes_google.py`:

```python
def test_no_gog_tool_registrations_remain() -> None:
    """The in-process google.* tools are being deleted in favor of MCP.
    Make sure the registry doesn't still try to import or register them,
    or the gateway will ImportError at startup."""
    import importlib
    import mycelos.connectors.registry as reg

    src = __import__("inspect").getsource(reg)
    assert "google_tools" not in src, "registry.py still imports google_tools"
    assert "google.gmail.search" not in src, "old gmail tool still registered"
    assert "google.calendar.list" not in src, "old calendar tool still registered"
    assert "google.drive.list" not in src, "old drive tool still registered"
```

Run: `pytest tests/test_mcp_recipes_google.py::test_no_gog_tool_registrations_remain -v`
Expected: FAIL (the import and names are still there).

- [ ] **Step 2: Delete `google_tools.py`**

```bash
rm src/mycelos/connectors/google_tools.py
```

- [ ] **Step 3: Remove the import and five registrations from `registry.py`**

Edit `src/mycelos/connectors/registry.py`.

Remove the block (lines 7–13):

```python
from mycelos.connectors.google_tools import (
    calendar_list,
    calendar_today,
    drive_list,
    gmail_labels,
    gmail_search,
)
```

Remove the block (lines 91–131) — the five `google.*` `registry.register(...)` calls, including the preceding comment `# Google tools via gog CLI (no credential proxy needed — gog handles OAuth)`.

After edits, the last remaining `registry.register(...)` call in the function should be `search.news` (from line 82–89 of the current file).

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_mcp_recipes_google.py::test_no_gog_tool_registrations_remain -v
python -c "from mycelos.connectors.registry import register_builtin_tools; print('import ok')"
```

Expected: test passes, import prints `import ok`.

- [ ] **Step 5: Run full unit suite to catch indirect breakage**

Run: `pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q`
Expected: all tests pass. Any test that imported `google_tools` or asserted on `google.*` tool names will surface here — if so, delete the assertions (they are obsolete).

- [ ] **Step 6: Commit**

```bash
git add -A src/mycelos/connectors/ tests/test_mcp_recipes_google.py
git commit -m "feat(mcp): remove in-process google_tools — MCP recipes own this now"
```

---

## Task 3: Update the connectors page UI

**Files:**
- Modify: `src/mycelos/frontend/pages/connectors.html` (around line 872 `services` array; around line 892 `google-drive` under `files` use case)

Context: the `services` block has a single `gmail` entry that opens the IMAP wizard (`wizardType = svc.id === 'gmail' ? 'email' : svc.id`). That's the stale gog-era card and must come out. The `files` use case has a `google-drive` entry pointing at the defunct npm package — also out. In their place we add a dedicated "Google" use case containing all three services, so the gallery doubles as the OAuth-setup funnel.

- [ ] **Step 1: Remove the stale `gmail` card from `services`**

At line 872 of `connectors.html`, delete this entry:

```js
          { id: 'gmail', icon: '\uD83D\uDCE8', name: 'Gmail API', description: 'Full Gmail + Calendar + Drive via Google API', credentials_needed: true, command: '' },
```

- [ ] **Step 2: Remove the stale `google-drive` card from `files`**

At line 892, delete:

```js
            { id: 'google-drive', name: 'Google Drive', description: 'Access Google Drive files', command: 'npx -y @modelcontextprotocol/server-google-drive', credentials_needed: true },
```

- [ ] **Step 3: Add a new "Google" use case with all three services**

Find the `useCases` array (starts around line 876). After the `files` entry, insert a new use-case block:

```js
          { id: 'google', icon: '\uD83C\uDF31', label: 'Google Workspace', connectors: [
            { id: 'gmail', name: 'Gmail', description: 'Search, read, send, filters, labels, attachments via the Gmail API', command: 'npx -y @gongrzhe/server-gmail-autoauth-mcp', credentials_needed: true },
            { id: 'google-calendar', name: 'Google Calendar', description: 'List, create, update, delete events across all calendars', command: 'npx -y @cocal/google-calendar-mcp', credentials_needed: true },
            { id: 'google-drive', name: 'Google Drive', description: 'List, read, search, and upload files in Drive', command: 'npx -y @piotr-agier/google-drive-mcp', credentials_needed: true },
          ]},
```

- [ ] **Step 4: Fix the wizardType mapping**

Around line 1212, find:

```js
          const wizardType = svc.id === 'gmail' ? 'email' : svc.id;
```

The `gmail` ID now refers to the MCP-backed service, not the IMAP wizard. Remove the special case:

```js
          const wizardType = svc.id;
```

- [ ] **Step 5: Manual smoke check in the browser**

Start the dev server (`mycelos serve --reload`) and open `http://localhost:9111/connectors.html`. Verify:
1. The stale "Gmail API" service card is gone.
2. The stale "Google Drive" card under "Files" is gone.
3. A new "Google Workspace" use case exists with three connectors.
4. Clicking "Gmail" opens the credential-setup form (not the old IMAP/password wizard).

If the UI shows a server-error toast on page load, check the browser console for a JS syntax error — the most likely cause is a dangling comma from the deletions above.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/frontend/pages/connectors.html
git commit -m "feat(connectors): group Gmail/Calendar/Drive under Google Workspace"
```

---

## Task 4: Gmail MCP live integration test

**Files:**
- Create: `tests/integration/test_gmail_mcp_live.py`

This is a direct clone of `test_email_mcp_live.py` (commit `a5bf41e`) pointed at the Gmail MCP server, so the test can catch breaking upstream changes before users do. It only runs when someone sets `GMAIL_OAUTH_KEYS_JSON` + `GMAIL_TOKEN_JSON` in `.env.test`; otherwise it skips cleanly.

**How the real auth works** (for the test writer's benefit):
1. The user runs `npx -y @gongrzhe/server-gmail-autoauth-mcp auth` once interactively, which writes a token file to `~/.gmail-mcp/credentials.json` (or a path configured via env). After that, subsequent runs are silent.
2. For CI/integration tests, the test puts both the oauth-keys file **and** the token file on disk in a temp dir, points `GMAIL_OAUTH_PATH` at the keys file, and points `GMAIL_CREDENTIALS_PATH` at the token. The test never opens a browser.

- [ ] **Step 1: Write the test file**

Create `tests/integration/test_gmail_mcp_live.py`:

```python
"""Integration test for @gongrzhe/server-gmail-autoauth-mcp.

Spawns the MCP server via npx, drives it over JSON-RPC stdio, and
verifies that Mycelos still talks to it correctly. This catches
breaking upstream changes (renamed tools, new required env vars)
before they hit a user.

Requires:
  - Node.js + npx on PATH
  - GMAIL_OAUTH_KEYS_JSON in .env.test — the contents of
    gcp-oauth.keys.json as a single-line JSON string
  - GMAIL_TOKEN_JSON in .env.test — the contents of the per-user
    token file (credentials.json from ~/.gmail-mcp/), also as a
    single-line JSON string

Without both env vars the test skips cleanly. The shape mirrors
test_email_mcp_live.py — same _MCPServer helper, same assertions
style — so maintenance patterns carry over.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import threading
import time
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
def gmail_oauth_files(tmp_path_factory):
    """Materialize the keys JSON + token JSON to disk in a temp dir."""
    keys = os.environ.get("GMAIL_OAUTH_KEYS_JSON")
    token = os.environ.get("GMAIL_TOKEN_JSON")
    if not keys or not token:
        dotenv = _load_dotenv_test()
        keys = keys or dotenv.get("GMAIL_OAUTH_KEYS_JSON")
        token = token or dotenv.get("GMAIL_TOKEN_JSON")
    if not keys or not token:
        pytest.skip(
            "GMAIL_OAUTH_KEYS_JSON and GMAIL_TOKEN_JSON not set in .env.test"
        )
    tmp = tmp_path_factory.mktemp("gmail-mcp")
    keys_path = tmp / "gcp-oauth.keys.json"
    token_path = tmp / "credentials.json"
    keys_path.write_text(keys)
    token_path.write_text(token)
    return keys_path, token_path


@pytest.fixture(scope="module")
def npx_available() -> None:
    if shutil.which("npx") is None:
        pytest.skip("npx not on PATH")


class _MCPServer:
    def __init__(self, cmd: list[str], env: dict[str, str]) -> None:
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
        self._stderr_lines: list[str] = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._id = 0

    def _drain_stderr(self) -> None:
        assert self._proc.stderr is not None
        for raw in iter(self._proc.stderr.readline, b""):
            self._stderr_lines.append(raw.decode("utf-8", "replace").rstrip())

    def rpc(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            req["params"] = params
        self._proc.stdin.write((json.dumps(req) + "\n").encode())
        self._proc.stdin.flush()
        want_id = req["id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self._proc.stdout], [], [], 1.0)
            if not ready:
                continue
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError(
                    f"server closed stdout; stderr tail: {self._stderr_lines[-5:]}"
                )
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                return msg
        raise TimeoutError(
            f"no response to {method} within {timeout}s; stderr tail: {self._stderr_lines[-5:]}"
        )

    def notify(self, method: str, params: dict | None = None) -> None:
        assert self._proc.stdin is not None
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        self._proc.stdin.flush()

    def stop(self) -> None:
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.close()
        except Exception:
            pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()


@pytest.fixture(scope="module")
def gmail_mcp(gmail_oauth_files, npx_available):
    keys_path, token_path = gmail_oauth_files
    env = {
        **os.environ,
        "GMAIL_OAUTH_PATH": str(keys_path),
        "GMAIL_CREDENTIALS_PATH": str(token_path),
        "NO_COLOR": "1",
        "CI": "1",
    }
    server = _MCPServer(["npx", "-y", "@gongrzhe/server-gmail-autoauth-mcp"], env)
    try:
        init = server.rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mycelos-integration", "version": "0.1"},
        })
        assert "result" in init, f"initialize failed: {init}"
        server.notify("notifications/initialized")
        yield server
    finally:
        server.stop()


@pytest.mark.integration
def test_gmail_server_exposes_expected_tools(gmail_mcp):
    """Keep the recipe's capabilities_preview in sync with reality.
    If upstream drops or renames a tool, we hear about it here."""
    resp = gmail_mcp.rpc("tools/list")
    assert "result" in resp, resp
    tools = {t["name"] for t in resp["result"].get("tools", [])}
    # A minimal subset we absolutely need. Upstream may add more —
    # that's fine. This only catches *removed* ones.
    expected = {"search_emails", "send_email"}
    missing = expected - tools
    assert not missing, (
        f"Upstream @gongrzhe/server-gmail-autoauth-mcp removed {missing}. "
        f"Update mcp_recipes.py + prompts. Current tools: {sorted(tools)}"
    )


@pytest.mark.integration
def test_gmail_search_round_trips(gmail_mcp):
    """Real API round-trip — an empty 'in:inbox' search returns a shape
    we can parse. Doesn't assert on message count (account may be empty)."""
    resp = gmail_mcp.rpc("tools/call", {
        "name": "search_emails",
        "arguments": {"query": "in:inbox", "maxResults": 1},
    }, timeout=60)
    assert "result" in resp, f"search_emails failed: {resp}"
    content = resp["result"].get("content", [])
    assert content, "empty content — server did not return a response body"
    text = content[0].get("text", "")
    assert text.strip(), "response text was empty"
```

- [ ] **Step 2: Verify it skips cleanly without creds**

Run: `pytest tests/integration/test_gmail_mcp_live.py -v`
Expected: 2 skipped (no env vars).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_gmail_mcp_live.py
git commit -m "test(integration): live Gmail MCP smoke test"
```

---

## Task 5: Google Calendar MCP live integration test

**Files:**
- Create: `tests/integration/test_google_calendar_mcp_live.py`

- [ ] **Step 1: Write the test file**

Create `tests/integration/test_google_calendar_mcp_live.py` with the same shape as the Gmail test, adapted for the Calendar server:

```python
"""Integration test for @cocal/google-calendar-mcp.

Spawns the MCP server via npx and verifies that list_calendars and
list_events round-trip. Skips unless GOOGLE_OAUTH_KEYS_JSON and
GOOGLE_CALENDAR_TOKEN_JSON are set in .env.test.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import threading
import time
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
def calendar_oauth_files(tmp_path_factory):
    keys = os.environ.get("GOOGLE_OAUTH_KEYS_JSON")
    token = os.environ.get("GOOGLE_CALENDAR_TOKEN_JSON")
    if not keys or not token:
        dotenv = _load_dotenv_test()
        keys = keys or dotenv.get("GOOGLE_OAUTH_KEYS_JSON")
        token = token or dotenv.get("GOOGLE_CALENDAR_TOKEN_JSON")
    if not keys or not token:
        pytest.skip(
            "GOOGLE_OAUTH_KEYS_JSON and GOOGLE_CALENDAR_TOKEN_JSON "
            "not set in .env.test"
        )
    tmp = tmp_path_factory.mktemp("gcal-mcp")
    keys_path = tmp / "gcp-oauth.keys.json"
    token_path = tmp / "token.json"
    keys_path.write_text(keys)
    token_path.write_text(token)
    return keys_path, token_path


@pytest.fixture(scope="module")
def npx_available() -> None:
    if shutil.which("npx") is None:
        pytest.skip("npx not on PATH")


class _MCPServer:
    def __init__(self, cmd: list[str], env: dict[str, str]) -> None:
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
        self._stderr_lines: list[str] = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._id = 0

    def _drain_stderr(self) -> None:
        assert self._proc.stderr is not None
        for raw in iter(self._proc.stderr.readline, b""):
            self._stderr_lines.append(raw.decode("utf-8", "replace").rstrip())

    def rpc(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            req["params"] = params
        self._proc.stdin.write((json.dumps(req) + "\n").encode())
        self._proc.stdin.flush()
        want_id = req["id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self._proc.stdout], [], [], 1.0)
            if not ready:
                continue
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError(
                    f"server closed stdout; stderr tail: {self._stderr_lines[-5:]}"
                )
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                return msg
        raise TimeoutError(
            f"no response to {method} within {timeout}s; stderr tail: {self._stderr_lines[-5:]}"
        )

    def notify(self, method: str, params: dict | None = None) -> None:
        assert self._proc.stdin is not None
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        self._proc.stdin.flush()

    def stop(self) -> None:
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.close()
        except Exception:
            pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()


@pytest.fixture(scope="module")
def calendar_mcp(calendar_oauth_files, npx_available):
    keys_path, token_path = calendar_oauth_files
    env = {
        **os.environ,
        "GOOGLE_OAUTH_CREDENTIALS": str(keys_path),
        "GOOGLE_CALENDAR_TOKEN_PATH": str(token_path),
        "NO_COLOR": "1",
        "CI": "1",
    }
    server = _MCPServer(["npx", "-y", "@cocal/google-calendar-mcp"], env)
    try:
        init = server.rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mycelos-integration", "version": "0.1"},
        })
        assert "result" in init, f"initialize failed: {init}"
        server.notify("notifications/initialized")
        yield server
    finally:
        server.stop()


@pytest.mark.integration
def test_calendar_server_exposes_expected_tools(calendar_mcp):
    resp = calendar_mcp.rpc("tools/list")
    assert "result" in resp, resp
    tools = {t["name"] for t in resp["result"].get("tools", [])}
    expected = {"list_calendars", "list_events"}
    missing = expected - tools
    assert not missing, (
        f"Upstream @cocal/google-calendar-mcp removed {missing}. "
        f"Current tools: {sorted(tools)}"
    )


@pytest.mark.integration
def test_calendar_list_calendars_round_trips(calendar_mcp):
    resp = calendar_mcp.rpc("tools/call", {
        "name": "list_calendars",
        "arguments": {},
    }, timeout=60)
    assert "result" in resp, f"list_calendars failed: {resp}"
    content = resp["result"].get("content", [])
    assert content, "empty content — server did not return a response body"
```

- [ ] **Step 2: Verify it skips cleanly without creds**

Run: `pytest tests/integration/test_google_calendar_mcp_live.py -v`
Expected: 2 skipped.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_google_calendar_mcp_live.py
git commit -m "test(integration): live Google Calendar MCP smoke test"
```

---

## Task 6: Google Drive MCP live integration test

**Files:**
- Create: `tests/integration/test_google_drive_mcp_live.py`

- [ ] **Step 1: Write the test file**

Create `tests/integration/test_google_drive_mcp_live.py` with the same shape:

```python
"""Integration test for @piotr-agier/google-drive-mcp.

Same shape as the Gmail + Calendar MCP tests. Skips unless
GOOGLE_OAUTH_KEYS_JSON and GOOGLE_DRIVE_TOKEN_JSON are set in
.env.test.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import threading
import time
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
def drive_oauth_files(tmp_path_factory):
    keys = os.environ.get("GOOGLE_OAUTH_KEYS_JSON")
    token = os.environ.get("GOOGLE_DRIVE_TOKEN_JSON")
    if not keys or not token:
        dotenv = _load_dotenv_test()
        keys = keys or dotenv.get("GOOGLE_OAUTH_KEYS_JSON")
        token = token or dotenv.get("GOOGLE_DRIVE_TOKEN_JSON")
    if not keys or not token:
        pytest.skip(
            "GOOGLE_OAUTH_KEYS_JSON and GOOGLE_DRIVE_TOKEN_JSON "
            "not set in .env.test"
        )
    tmp = tmp_path_factory.mktemp("gdrive-mcp")
    keys_path = tmp / "gcp-oauth.keys.json"
    token_path = tmp / "token.json"
    keys_path.write_text(keys)
    token_path.write_text(token)
    return keys_path, token_path


@pytest.fixture(scope="module")
def npx_available() -> None:
    if shutil.which("npx") is None:
        pytest.skip("npx not on PATH")


class _MCPServer:
    def __init__(self, cmd: list[str], env: dict[str, str]) -> None:
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
        self._stderr_lines: list[str] = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._id = 0

    def _drain_stderr(self) -> None:
        assert self._proc.stderr is not None
        for raw in iter(self._proc.stderr.readline, b""):
            self._stderr_lines.append(raw.decode("utf-8", "replace").rstrip())

    def rpc(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            req["params"] = params
        self._proc.stdin.write((json.dumps(req) + "\n").encode())
        self._proc.stdin.flush()
        want_id = req["id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self._proc.stdout], [], [], 1.0)
            if not ready:
                continue
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError(
                    f"server closed stdout; stderr tail: {self._stderr_lines[-5:]}"
                )
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                return msg
        raise TimeoutError(
            f"no response to {method} within {timeout}s; stderr tail: {self._stderr_lines[-5:]}"
        )

    def notify(self, method: str, params: dict | None = None) -> None:
        assert self._proc.stdin is not None
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        self._proc.stdin.flush()

    def stop(self) -> None:
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.close()
        except Exception:
            pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()


@pytest.fixture(scope="module")
def drive_mcp(drive_oauth_files, npx_available):
    keys_path, token_path = drive_oauth_files
    env = {
        **os.environ,
        "GDRIVE_OAUTH_PATH": str(keys_path),
        "GDRIVE_TOKEN_PATH": str(token_path),
        "NO_COLOR": "1",
        "CI": "1",
    }
    server = _MCPServer(["npx", "-y", "@piotr-agier/google-drive-mcp"], env)
    try:
        init = server.rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mycelos-integration", "version": "0.1"},
        })
        assert "result" in init, f"initialize failed: {init}"
        server.notify("notifications/initialized")
        yield server
    finally:
        server.stop()


@pytest.mark.integration
def test_drive_server_exposes_expected_tools(drive_mcp):
    resp = drive_mcp.rpc("tools/list")
    assert "result" in resp, resp
    tools = {t["name"] for t in resp["result"].get("tools", [])}
    expected = {"drive_list", "drive_search"}
    missing = expected - tools
    assert not missing, (
        f"Upstream @piotr-agier/google-drive-mcp removed {missing}. "
        f"Current tools: {sorted(tools)}"
    )


@pytest.mark.integration
def test_drive_list_round_trips(drive_mcp):
    resp = drive_mcp.rpc("tools/call", {
        "name": "drive_list",
        "arguments": {"pageSize": 1},
    }, timeout=60)
    assert "result" in resp, f"drive_list failed: {resp}"
    content = resp["result"].get("content", [])
    assert content, "empty content — server did not return a response body"
```

- [ ] **Step 2: Verify it skips cleanly without creds**

Run: `pytest tests/integration/test_google_drive_mcp_live.py -v`
Expected: 2 skipped.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_google_drive_mcp_live.py
git commit -m "test(integration): live Google Drive MCP smoke test"
```

---

## Task 7: Setup documentation

**Files:**
- Create: `docs/deployment/google-setup.md`

This is the doc users will actually follow. It has to cover Google Cloud OAuth creation, the two separate concepts (oauth-keys vs. per-user-token), running the one-shot `npx ... auth` inside the proxy container, and the caveats for a Pi-at-home deployment where `localhost:3000` callback URLs only work from the proxy host.

- [ ] **Step 1: Write the doc**

Create `docs/deployment/google-setup.md`:

````markdown
# Google (Gmail / Calendar / Drive) setup

Mycelos integrates with Google Workspace through three separate MCP
servers, each running inside the SecurityProxy container. This keeps
OAuth tokens out of the gateway process and out of the LLM's reach.

This document covers the one-time setup. Once it's done, Gmail /
Calendar / Drive connectors work the same as any other connector —
click "Connect" in the UI, the server uses the stored credentials
silently.

## What you'll end up with

- A Google Cloud **OAuth 2.0 Desktop-app credential** (`gcp-oauth.keys.json`)
  — one file, reused across all three services.
- Three **per-service tokens**, one for each service you enable:
  `~/.gmail-mcp/credentials.json`, `~/.google-calendar-mcp/token.json`,
  `~/.google-drive-mcp/token.json` (paths inside the proxy container).
- Three Mycelos connectors: `gmail`, `google-calendar`, `google-drive`.

You only need to do the OAuth-key step once. You do need to run the
per-service consent flow once per service.

## Step 1 — Create a Google Cloud OAuth credential

1. Open https://console.cloud.google.com/ and create (or pick) a project.
2. Go to **APIs & Services → Library** and enable:
   - Gmail API (if you want Gmail)
   - Google Calendar API (if you want Calendar)
   - Google Drive API (if you want Drive)
3. Go to **APIs & Services → OAuth consent screen**, pick **External**,
   fill in the app name / support email / developer email, and save.
   You do NOT need to publish the app — keeping it in "Testing" is fine,
   which means you'll have to add your own Google account as a "Test user"
   further down the same page.
4. Go to **APIs & Services → Credentials → Create credentials → OAuth
   client ID**, pick **Desktop app**, give it a name (e.g. "Mycelos"),
   and click Create.
5. Download the JSON. Rename it to `gcp-oauth.keys.json`.

## Step 2 — Seed the keys file in the proxy container

The Mycelos proxy bind-mounts `/data`, so anything you put in
`./data/.google/` on the host is visible to it as `/data/.google/`:

```bash
mkdir -p ./data/.google
cp ~/Downloads/gcp-oauth.keys.json ./data/.google/
```

## Step 3 — Run the one-time consent flow per service

Each service needs its own browser consent (different scopes). You run
these **inside** the proxy container, once per service:

```bash
mycelos shell proxy   # drops you into a shell in the proxy container

# --- Gmail ---
export GMAIL_OAUTH_PATH=/data/.google/gcp-oauth.keys.json
export GMAIL_CREDENTIALS_PATH=/data/.gmail-mcp/credentials.json
mkdir -p /data/.gmail-mcp
npx -y @gongrzhe/server-gmail-autoauth-mcp auth
# A URL is printed. Open it in a browser on your laptop, consent,
# paste the callback URL back when prompted. Token file gets
# written to /data/.gmail-mcp/credentials.json.

# --- Calendar ---
export GOOGLE_OAUTH_CREDENTIALS=/data/.google/gcp-oauth.keys.json
export GOOGLE_CALENDAR_TOKEN_PATH=/data/.google-calendar-mcp/token.json
mkdir -p /data/.google-calendar-mcp
npx -y @cocal/google-calendar-mcp auth

# --- Drive ---
export GDRIVE_OAUTH_PATH=/data/.google/gcp-oauth.keys.json
export GDRIVE_TOKEN_PATH=/data/.google-drive-mcp/token.json
mkdir -p /data/.google-drive-mcp
npx -y @piotr-agier/google-drive-mcp auth
```

> **Callback URL caveat:** All three packages default to
> `http://localhost:3000` as the OAuth redirect. On a home-network
> deployment (e.g. Raspberry Pi), you must run `mycelos shell proxy`
> *from a browser-capable machine with network reachability* to
> `localhost:3000` on the Pi — or configure a custom redirect URL in
> Google Cloud Console. The former is easier: SSH to the Pi with port
> forwarding (`ssh -L 3000:localhost:3000 pi-host`), then open the
> consent URL in your local browser.

After the three consents are done, exit the shell. Tokens persist
across container restarts (they live in the bind-mounted `/data`).

## Step 4 — Wire up the connectors in Mycelos

In the web UI, go to **Connectors**, find the "Google Workspace"
category, and click each service you want. In the credential form,
paste the path to the keys file (`/data/.google/gcp-oauth.keys.json`)
into the OAuth-credentials field.

That's it. The server picks up the token file Mycelos wrote in Step 3
and starts serving API calls silently.

## Troubleshooting

### "invalid_grant" on first call

The token expired or was never issued. Re-run the Step-3 `auth` command
for that service.

### Server logs "Please run `npx ... auth` first"

The server couldn't find the token file. Either the path env var is
wrong, or the one-shot `auth` didn't write the token. Re-run Step 3.

### Token works locally but not in the container

Usually caused by a host-vs-container path mismatch. The token file
must be reachable at the path the env var points to — `/data/.gmail-mcp/...`
inside the container, `./data/.gmail-mcp/...` on the host.

### "access_denied" during consent

Your Google account isn't listed as a Test user on the OAuth consent
screen. Go back to **OAuth consent screen** in Cloud Console and add
your account under "Test users".

## Security notes

- `gcp-oauth.keys.json` is a client credential pair, not a secret per
  se (it identifies your Google Cloud project, not your account). We
  still treat it as sensitive — keep it out of version control and
  off public shares.
- The per-service token files (`credentials.json`, `token.json`) are
  the real sensitive artifacts. They grant API access to your Google
  account. They live only inside the proxy container's `/data`
  bind-mount; the gateway never sees them.
- Each server's scopes are visible in the Mycelos connector card
  before you click Connect. Review them before consenting.
````

- [ ] **Step 2: Commit**

```bash
git add docs/deployment/google-setup.md
git commit -m "docs: Google (Gmail/Calendar/Drive) MCP setup walkthrough"
```

---

## Task 8: Changelog + final verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add a changelog entry under Week 17 (2026)**

In `CHANGELOG.md`, find the existing `## Week 17 (2026)` block and add this subsection at the end of it:

```markdown
### Google via MCP (retiring the gog-CLI path)
- Gmail, Google Calendar, and Google Drive each get a dedicated MCP recipe (`@gongrzhe/server-gmail-autoauth-mcp`, `@cocal/google-calendar-mcp`, `@piotr-agier/google-drive-mcp`), spawned inside the SecurityProxy container like every other MCP connector. The single gog-CLI `gmail` recipe (which was broken under two-container deployment — `gog` wasn't in the proxy image and the gateway has no direct internet anyway) is retired, and so are the five in-process `google.*` tool bindings in `connectors/registry.py`.
- The stale `@modelcontextprotocol/server-google-drive` reference (package was never published) is replaced by the real `@piotr-agier/google-drive-mcp`.
- Connectors page gains a "Google Workspace" use-case group surfacing all three services as separate cards so users can enable only the scopes they need.
- New doc: `docs/deployment/google-setup.md` walks through OAuth-keys creation, the shared Google Cloud project, the one-shot `npx ... auth` consent flow inside the proxy container, and the `localhost:3000` callback caveats for home-network (Raspberry Pi) deployments.
- Three live integration tests under `tests/integration/test_*_mcp_live.py` catch upstream breaking changes before users do; all skip cleanly without credentials.
```

- [ ] **Step 2: Run full unit suite one more time**

Run: `pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q`
Expected: all tests pass (current baseline: 2358 + 5 new from `test_mcp_recipes_google.py` ≈ 2363 passing).

- [ ] **Step 3: Smoke-import the gateway with the deleted module gone**

Run: `python -c "from mycelos.gateway.routes import register_routes; print('ok')"`
Expected: `ok`. If this raises `ImportError` mentioning `google_tools`, go back to Task 2 Step 3 — something else in the codebase still imports it.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): Google via MCP (Gmail/Calendar/Drive)"
```

- [ ] **Step 5: Push**

```bash
git push origin main
```

---

## Self-Review

### Spec coverage
| Spec section | Task |
|---|---|
| Three MCP recipes (Gmail/Calendar/Drive) with env vars | Task 1 |
| Delete `google_tools.py`, remove registry entries | Task 2 |
| Frontend connector list update ("Google" category) | Task 3 |
| OAuth-flow docs (creation, auth, Pi caveats) | Task 7 |
| Integration tests per service | Tasks 4, 5, 6 |
| Non-goal: unified OAuth across services | Honored — three separate recipes, each with its own consent flow, no cross-service sharing logic |
| Non-goal: replace email MCP recipe | Honored — `email` recipe is untouched; new `gmail` lives alongside it |

### Placeholder scan
No "TBD", "TODO", or "similar to X" references. Every code block is literal.

### Type consistency
- Recipe env-var names referenced in tests match the recipe definitions (`GMAIL_OAUTH_PATH`, `GOOGLE_OAUTH_CREDENTIALS`, `GDRIVE_OAUTH_PATH`).
- Connectors-page card IDs (`gmail`, `google-calendar`, `google-drive`) match recipe IDs.
- Docker bind-mount path (`/data`) matches what `docker-compose.yml` already sets for the proxy container.

## Trigger / handoff

Implementation proceeds task-by-task. After Task 8, the three Google connectors are ready for live use; the user follows `docs/deployment/google-setup.md` once per service to grant OAuth consent, then uses them via the UI like any other connector.
