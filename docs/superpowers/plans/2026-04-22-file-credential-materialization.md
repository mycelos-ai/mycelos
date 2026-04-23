# File-based Credential Materialization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let MCP servers that hardcode file paths (like `~/.gmail-mcp/gcp-oauth.keys.json`) participate in Mycelos's encrypted-credential flow without breaking the "DB is the single source of truth" rule.

**Architecture:** Keep credentials in the encrypted SQLite store as the one authoritative copy. Before spawning a subprocess, the proxy container materializes the needed credentials into per-session tmp files (under `HOME=/tmp/mycelos-oauth-<sid>/`) and purges them on exit. After a successful OAuth consent, the proxy reads the token file the upstream tool wrote and stores it as a second credential in the DB, then purges. The real MCP server runs the same way: materialize both keys + token, spawn, purge on stop.

**Tech Stack:** Python 3.12+ context managers for guaranteed cleanup, `subprocess.Popen` with `HOME=` override, existing `EncryptedCredentialProxy.store_credential` / `get_credential` for all DB I/O. Docker `tmpfs` mount on `/tmp/mycelos-oauth` so cleartext keys never touch persistent disk.

---

## Why this shape

- **DB is the only persistent copy of the cleartext.** Config generations, rollback, audit, rotation all work unchanged.
- **Files on disk are a short-lived cache.** Created in a context manager; purged in `finally`. Named after the session id so parallel sessions don't collide.
- **Upstream packages are treated as black boxes.** We set `HOME=<tmp>`, create `<tmp>/<home_dir>/<filename>`, and let the package do whatever it normally does. No forks, no patches.
- **Token output goes back to DB.** When `npx ... auth` succeeds and writes its `credentials.json`, the proxy reads that file back, stores it as a second credential, and purges. Future subprocess runs materialize both files.
- **No new concept in the gateway.** Everything new lives in the proxy container. The gateway just passes recipe ids and doesn't know materialization exists.

---

## Security properties preserved

| Property | How it stays true |
|---|---|
| Master key never in gateway | No change — credential_proxy access remains proxy-only |
| Credentials never in logs | File paths are logged, not contents |
| Credentials never in LLM prompts | Subprocess env is not prompt-visible |
| Cleartext window = subprocess lifetime | Context manager guarantees cleanup |
| Rotation works | Delete the credential row → next run has no file → package errors out |
| Audit | New events: `credential.materialized`, `credential.purged` |

---

## File structure

| File | Responsibility |
|---|---|
| `src/mycelos/connectors/mcp_recipes.py` | Five new fields on `MCPRecipe`: `oauth_keys_credential_service`, `oauth_keys_home_dir`, `oauth_keys_filename`, `oauth_token_filename`, `oauth_token_credential_service` |
| `src/mycelos/security/credential_materializer.py` | NEW — `materialize_credentials(...)` context manager + `persist_token(...)` helper. Pure functions; the caller owns the credential_proxy |
| `src/mycelos/security/proxy_server.py` | Use the materializer in `/oauth/start` (spawn `npx ... auth`) and `/mcp/start` (spawn the real server). Read token back after `/oauth/stream` sees exit_code==0 |
| `src/mycelos/gateway/routes.py` | Simplify `/api/connectors/oauth/start` — no more `env_vars` from the client |
| `src/mycelos/frontend/pages/connectors.html` | `submitOAuthKeysAndStart` stops sending env_vars; only posts `{recipe_id}` |
| `docker-compose.yml` | tmpfs mount on proxy's `/tmp/mycelos-oauth` |
| `docs/deployment/google-setup.md` | No user-facing shell commands anymore |
| `CHANGELOG.md` | Week 17 entry |

---

## Task 1 — Recipe fields for file-based credentials

**Files:**
- Modify: `src/mycelos/connectors/mcp_recipes.py`
- Modify: `tests/test_mcp_recipe_setup_flow.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mcp_recipe_setup_flow.py`:

```python


# ── File-based credential fields ──


def test_mcp_recipe_defaults_for_file_credentials() -> None:
    """New fields default to empty strings for recipes that don't need
    file-materialization (the vast majority — env-var-based tools)."""
    r = MCPRecipe(id="x", name="X", description="", command="npx -y x")
    assert r.oauth_keys_credential_service == ""
    assert r.oauth_keys_home_dir == ""
    assert r.oauth_keys_filename == ""
    assert r.oauth_token_filename == ""
    assert r.oauth_token_credential_service == ""


def test_gmail_recipe_uses_file_materialization() -> None:
    r = RECIPES["gmail"]
    # The Gmail MCP package hardcodes ~/.gmail-mcp/gcp-oauth.keys.json —
    # no env var to override. We materialize the file into a tmp HOME.
    assert r.oauth_keys_credential_service == "gmail-oauth-keys"
    assert r.oauth_keys_home_dir == ".gmail-mcp"
    assert r.oauth_keys_filename == "gcp-oauth.keys.json"
    assert r.oauth_token_filename == "credentials.json"
    assert r.oauth_token_credential_service == "gmail-oauth-token"


def test_google_calendar_recipe_uses_file_materialization() -> None:
    r = RECIPES["google-calendar"]
    assert r.oauth_keys_credential_service == "google-calendar-oauth-keys"
    assert r.oauth_keys_home_dir == ".google-calendar-mcp"
    assert r.oauth_keys_filename == "gcp-oauth.keys.json"
    assert r.oauth_token_filename == "token.json"
    assert r.oauth_token_credential_service == "google-calendar-oauth-token"


def test_google_drive_recipe_uses_file_materialization() -> None:
    r = RECIPES["google-drive"]
    assert r.oauth_keys_credential_service == "google-drive-oauth-keys"
    assert r.oauth_keys_home_dir == ".google-drive-mcp"
    assert r.oauth_keys_filename == "gcp-oauth.keys.json"
    assert r.oauth_token_filename == "token.json"
    assert r.oauth_token_credential_service == "google-drive-oauth-token"


def test_non_file_recipes_keep_empty_materialization_fields() -> None:
    """Email, GitHub, Brave etc. use env-var injection and must not
    accidentally inherit file-materialization config."""
    for rid in ("email", "brave-search", "github", "notion", "slack"):
        r = RECIPES.get(rid)
        if r is None:
            continue
        assert r.oauth_keys_credential_service == "", f"{rid} should not materialize"
        assert r.oauth_keys_filename == "", f"{rid} should not materialize"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/stefan/Documents/railsapps/mycelos
PYTHONPATH=src pytest tests/test_mcp_recipe_setup_flow.py -v
```

Expected: the five new tests fail with `AttributeError: 'MCPRecipe' object has no attribute 'oauth_keys_credential_service'`.

- [ ] **Step 3: Add the five fields to MCPRecipe**

Open `src/mycelos/connectors/mcp_recipes.py`. Find the `MCPRecipe` dataclass. After `oauth_setup_guide_id` (the last existing `oauth_*` field), add:

```python
    oauth_keys_credential_service: str = ""
    # Where the OAuth *keys* blob (e.g. gcp-oauth.keys.json) lives in the
    # credential store. Empty if the recipe uses env-var injection instead.
    # The proxy materializes this credential as a file before spawning
    # the subprocess, then purges it on exit.

    oauth_keys_home_dir: str = ""
    # Sub-directory under the spawned HOME where the keys file must
    # land, e.g. ".gmail-mcp". Upstream packages read from a hardcoded
    # "~/.xxx-mcp/keys.json" path — setting HOME to a session-scoped
    # tmpdir and writing the file here is what makes them pick it up.

    oauth_keys_filename: str = ""
    # Exact filename the upstream package expects, e.g.
    # "gcp-oauth.keys.json". Combined with oauth_keys_home_dir.

    oauth_token_filename: str = ""
    # Filename the upstream package *writes* after a successful consent,
    # e.g. "credentials.json" or "token.json". Read back by the proxy
    # after the auth subprocess exits cleanly.

    oauth_token_credential_service: str = ""
    # Where to store the token blob the upstream package produced.
    # Future MCP-server runs materialize both keys AND token before spawn.
```

- [ ] **Step 4: Flip the three Google recipes**

Still in `mcp_recipes.py`, find the `gmail` recipe (the one with `setup_flow="oauth_browser"`). After `oauth_setup_guide_id="google_cloud",`, append:

```python
        oauth_keys_credential_service="gmail-oauth-keys",
        oauth_keys_home_dir=".gmail-mcp",
        oauth_keys_filename="gcp-oauth.keys.json",
        oauth_token_filename="credentials.json",
        oauth_token_credential_service="gmail-oauth-token",
```

For `google-calendar`:

```python
        oauth_keys_credential_service="google-calendar-oauth-keys",
        oauth_keys_home_dir=".google-calendar-mcp",
        oauth_keys_filename="gcp-oauth.keys.json",
        oauth_token_filename="token.json",
        oauth_token_credential_service="google-calendar-oauth-token",
```

For `google-drive`:

```python
        oauth_keys_credential_service="google-drive-oauth-keys",
        oauth_keys_home_dir=".google-drive-mcp",
        oauth_keys_filename="gcp-oauth.keys.json",
        oauth_token_filename="token.json",
        oauth_token_credential_service="google-drive-oauth-token",
```

- [ ] **Step 5: Verify tests pass**

```bash
PYTHONPATH=src pytest tests/test_mcp_recipe_setup_flow.py -v
```

Expected: 14 tests pass (9 existing + 5 new).

- [ ] **Step 6: Sanity — recipe count unchanged**

```bash
PYTHONPATH=src python -c "from mycelos.connectors.mcp_recipes import RECIPES; print(len(RECIPES))"
```

Expected: `21` (same as before).

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/connectors/mcp_recipes.py tests/test_mcp_recipe_setup_flow.py
git commit -m "feat(mcp): add file-materialization fields to MCPRecipe"
```

English-only. No `Co-Authored-By` footer.

---

## Task 2 — Credential materializer helper

**Files:**
- Create: `src/mycelos/security/credential_materializer.py`
- Create: `tests/test_credential_materializer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_credential_materializer.py`:

```python
"""The credential_materializer creates a per-session HOME tmpdir,
writes credential blobs to disk for subprocess consumption, and
cleans up on exit. All cleartext lives only while the context is
open; `finally` guarantees purge even on exceptions."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycelos.security.credential_materializer import (
    MaterializedSession,
    materialize_credentials,
    persist_token,
)


def test_materialize_creates_expected_file_layout(tmp_path):
    """Given a recipe with oauth_keys_home_dir and oauth_keys_filename,
    the context manager creates <root>/<home_dir>/<filename> containing
    the credential api_key."""
    credential_proxy = MagicMock()
    credential_proxy.get_credential.return_value = {
        "api_key": '{"installed": {"client_id": "x"}}',
    }

    recipe = _recipe_with(
        oauth_keys_credential_service="gmail-oauth-keys",
        oauth_keys_home_dir=".gmail-mcp",
        oauth_keys_filename="gcp-oauth.keys.json",
    )

    with materialize_credentials(
        recipe=recipe,
        credential_proxy=credential_proxy,
        user_id="default",
        tmp_root=tmp_path,
        session_id="sid-1",
    ) as session:
        home = session.home_dir
        assert home.exists()
        keys_path = home / ".gmail-mcp" / "gcp-oauth.keys.json"
        assert keys_path.exists()
        assert json.loads(keys_path.read_text()) == {"installed": {"client_id": "x"}}
        # home.parent is the session-scoped tmpdir itself.
        assert home.parent.name == "mycelos-oauth-sid-1"

    # After __exit__, everything under the session root is gone.
    assert not home.exists()


def test_materialize_is_a_no_op_for_recipes_without_keys(tmp_path):
    """Recipes that use env-var injection (no oauth_keys_credential_service)
    still get a HOME tmpdir but no files. Subprocess can still be spawned
    with HOME= pointing there."""
    credential_proxy = MagicMock()
    recipe = _recipe_with(
        oauth_keys_credential_service="",  # Empty
    )

    with materialize_credentials(
        recipe=recipe,
        credential_proxy=credential_proxy,
        user_id="default",
        tmp_root=tmp_path,
        session_id="sid-2",
    ) as session:
        assert session.home_dir.exists()
        # No file was written.
        credential_proxy.get_credential.assert_not_called()


def test_materialize_includes_token_when_already_stored(tmp_path):
    """If the recipe's oauth_token_credential_service has a value in the
    store, the token file is also materialized — this is what /mcp/start
    uses for the real server run after auth has happened."""
    credential_proxy = MagicMock()
    credential_proxy.get_credential.side_effect = lambda service, user_id="default": {
        "gmail-oauth-keys": {"api_key": '{"installed": {"client_id": "x"}}'},
        "gmail-oauth-token": {"api_key": '{"access_token": "ya29.test"}'},
    }.get(service)

    recipe = _recipe_with(
        oauth_keys_credential_service="gmail-oauth-keys",
        oauth_keys_home_dir=".gmail-mcp",
        oauth_keys_filename="gcp-oauth.keys.json",
        oauth_token_credential_service="gmail-oauth-token",
        oauth_token_filename="credentials.json",
    )

    with materialize_credentials(
        recipe=recipe,
        credential_proxy=credential_proxy,
        user_id="default",
        tmp_root=tmp_path,
        session_id="sid-3",
    ) as session:
        token_path = session.home_dir / ".gmail-mcp" / "credentials.json"
        assert token_path.exists()
        assert json.loads(token_path.read_text()) == {"access_token": "ya29.test"}


def test_materialize_skips_token_when_not_yet_stored(tmp_path):
    """During the initial `npx ... auth` run the token doesn't exist yet.
    The materializer must not crash; it just writes the keys file."""
    credential_proxy = MagicMock()

    def fake_get(service, user_id="default"):
        if service == "gmail-oauth-keys":
            return {"api_key": '{"installed": {}}'}
        return None  # Token not in store yet.

    credential_proxy.get_credential.side_effect = fake_get

    recipe = _recipe_with(
        oauth_keys_credential_service="gmail-oauth-keys",
        oauth_keys_home_dir=".gmail-mcp",
        oauth_keys_filename="gcp-oauth.keys.json",
        oauth_token_credential_service="gmail-oauth-token",
        oauth_token_filename="credentials.json",
    )

    with materialize_credentials(
        recipe=recipe,
        credential_proxy=credential_proxy,
        user_id="default",
        tmp_root=tmp_path,
        session_id="sid-4",
    ) as session:
        keys_path = session.home_dir / ".gmail-mcp" / "gcp-oauth.keys.json"
        token_path = session.home_dir / ".gmail-mcp" / "credentials.json"
        assert keys_path.exists()
        assert not token_path.exists()


def test_materialize_purges_on_exception(tmp_path):
    """`finally` must delete the tmpdir even if the `with` block raises."""
    credential_proxy = MagicMock()
    credential_proxy.get_credential.return_value = {"api_key": "{}"}
    recipe = _recipe_with(
        oauth_keys_credential_service="x",
        oauth_keys_home_dir=".x",
        oauth_keys_filename="k.json",
    )

    home_dir = None
    with pytest.raises(RuntimeError):
        with materialize_credentials(
            recipe=recipe,
            credential_proxy=credential_proxy,
            user_id="default",
            tmp_root=tmp_path,
            session_id="sid-5",
        ) as session:
            home_dir = session.home_dir
            raise RuntimeError("boom")
    assert home_dir is not None
    assert not home_dir.exists()


def test_persist_token_reads_file_and_stores(tmp_path):
    """After a successful subprocess run, persist_token reads the written
    file and calls credential_proxy.store_credential with it as api_key."""
    credential_proxy = MagicMock()
    recipe = _recipe_with(
        oauth_keys_home_dir=".gmail-mcp",
        oauth_token_filename="credentials.json",
        oauth_token_credential_service="gmail-oauth-token",
    )

    # Simulate the subprocess having written a token file during its run.
    home = tmp_path / "mycelos-oauth-sid-7"
    (home / ".gmail-mcp").mkdir(parents=True)
    (home / ".gmail-mcp" / "credentials.json").write_text('{"access_token": "new"}')

    persist_token(
        recipe=recipe,
        credential_proxy=credential_proxy,
        user_id="default",
        home_dir=home,
    )

    credential_proxy.store_credential.assert_called_once()
    call = credential_proxy.store_credential.call_args
    service = call.args[0] if call.args else call.kwargs.get("service")
    payload = call.args[1] if len(call.args) > 1 else call.kwargs.get("credential")
    assert service == "gmail-oauth-token"
    assert payload["api_key"] == '{"access_token": "new"}'


def test_persist_token_is_noop_when_file_missing(tmp_path):
    """If the subprocess didn't write a token (e.g. auth failed), don't
    call store_credential — persist_token just quietly exits."""
    credential_proxy = MagicMock()
    recipe = _recipe_with(
        oauth_keys_home_dir=".gmail-mcp",
        oauth_token_filename="credentials.json",
        oauth_token_credential_service="gmail-oauth-token",
    )
    home = tmp_path / "mycelos-oauth-sid-8"
    (home / ".gmail-mcp").mkdir(parents=True)
    # No file written.

    persist_token(
        recipe=recipe,
        credential_proxy=credential_proxy,
        user_id="default",
        home_dir=home,
    )

    credential_proxy.store_credential.assert_not_called()


def _recipe_with(**overrides):
    """Build a throwaway MCPRecipe for these tests — populate only the
    fields the materializer reads."""
    from mycelos.connectors.mcp_recipes import MCPRecipe
    base = dict(id="test", name="test", description="", command="npx -y x")
    base.update(overrides)
    return MCPRecipe(**base)
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src pytest tests/test_credential_materializer.py -v
```

Expected: ModuleNotFoundError for `mycelos.security.credential_materializer`.

- [ ] **Step 3: Create the materializer module**

Create `src/mycelos/security/credential_materializer.py`:

```python
"""Short-lived file materialization for MCP servers that hardcode
file paths.

Many upstream MCP packages read their OAuth credentials from a fixed
path under `$HOME` (e.g. `~/.gmail-mcp/gcp-oauth.keys.json`) with no
env var override. To stay within the 'DB is the only persistent copy
of the cleartext' rule, we materialize the credentials into a
per-session tmp HOME directly before spawning the subprocess and
purge on exit.

This module is pure — it knows nothing about subprocesses. The caller
is responsible for:
  - using the returned `MaterializedSession.home_dir` as HOME in the
    spawned subprocess
  - calling `persist_token(...)` after a successful run so any tokens
    the package wrote get saved back to the DB
"""
from __future__ import annotations

import contextlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


@dataclass
class MaterializedSession:
    """A tmp-dir scope that owns a HOME directory for one subprocess."""
    home_dir: Path


@contextlib.contextmanager
def materialize_credentials(
    recipe,
    credential_proxy,
    user_id: str,
    tmp_root: Path,
    session_id: str,
) -> Iterator[MaterializedSession]:
    """Materialize a recipe's OAuth keys (and token, if present) into a
    session-scoped tmp HOME directory.

    Guarantees purge on exit via try/finally. Layout:
        <tmp_root>/mycelos-oauth-<session_id>/          <- HOME
            <oauth_keys_home_dir>/<oauth_keys_filename>
            <oauth_keys_home_dir>/<oauth_token_filename> (if token exists)

    Recipes without oauth_keys_credential_service still get an empty
    HOME — some subprocesses set up files on their own under HOME and
    would write to the user's real ~/ otherwise.
    """
    session_root = tmp_root / f"mycelos-oauth-{session_id}"
    session_root.mkdir(parents=True, exist_ok=False)
    try:
        keys_service = getattr(recipe, "oauth_keys_credential_service", "") or ""
        home_dir_name = getattr(recipe, "oauth_keys_home_dir", "") or ""
        if keys_service and home_dir_name:
            target_dir = session_root / home_dir_name
            target_dir.mkdir(parents=True, exist_ok=True)

            keys_cred = credential_proxy.get_credential(keys_service, user_id=user_id)
            if keys_cred and keys_cred.get("api_key"):
                keys_path = target_dir / recipe.oauth_keys_filename
                keys_path.write_text(keys_cred["api_key"])
                # Best-effort mode tightening — the package only needs
                # to read, the proxy process is the only writer.
                try:
                    keys_path.chmod(0o600)
                except OSError:
                    pass

            token_service = getattr(recipe, "oauth_token_credential_service", "") or ""
            token_name = getattr(recipe, "oauth_token_filename", "") or ""
            if token_service and token_name:
                token_cred = credential_proxy.get_credential(token_service, user_id=user_id)
                if token_cred and token_cred.get("api_key"):
                    token_path = target_dir / token_name
                    token_path.write_text(token_cred["api_key"])
                    try:
                        token_path.chmod(0o600)
                    except OSError:
                        pass

        yield MaterializedSession(home_dir=session_root)
    finally:
        # Best-effort purge. If this raises we still want the exception
        # from the `with` body to propagate, so swallow cleanup errors.
        try:
            shutil.rmtree(session_root, ignore_errors=True)
        except Exception:
            logger.warning("credential_materializer cleanup failed for %s", session_root)


def persist_token(
    recipe,
    credential_proxy,
    user_id: str,
    home_dir: Path,
) -> None:
    """Read the token file the subprocess wrote (if any) and store it.

    Called by the proxy's /oauth/stream handler after the auth subprocess
    exits with code 0. If the subprocess didn't produce a token (auth
    failed, wrong scopes, upstream bug), this is a silent no-op — the
    caller should already be looking at exit_code to decide success.
    """
    token_service = getattr(recipe, "oauth_token_credential_service", "") or ""
    token_name = getattr(recipe, "oauth_token_filename", "") or ""
    home_dir_name = getattr(recipe, "oauth_keys_home_dir", "") or ""
    if not token_service or not token_name or not home_dir_name:
        return

    token_path = home_dir / home_dir_name / token_name
    if not token_path.exists():
        return

    content = token_path.read_text()
    if not content.strip():
        return

    credential_proxy.store_credential(
        token_service,
        {"api_key": content},
        user_id=user_id,
        label="default",
        description=f"OAuth token materialized from {home_dir_name}/{token_name}",
    )
```

- [ ] **Step 4: Verify tests pass**

```bash
PYTHONPATH=src pytest tests/test_credential_materializer.py -v
```

Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/security/credential_materializer.py tests/test_credential_materializer.py
git commit -m "feat(security): credential materializer for file-based OAuth tools"
```

---

## Task 3 — Wire materializer into `/oauth/start` and the stream handler

**Files:**
- Modify: `src/mycelos/security/proxy_server.py`
- Modify: `tests/test_proxy_oauth_endpoints.py` (update existing tests + add one)
- Modify: `tests/test_proxy_oauth_websocket.py` (add one for token persistence)

### Context

Right now `/oauth/start` does:
1. Validate the `oauth_cmd` allowlist.
2. Resolve `credential:<service>` env-var references.
3. `subprocess.Popen` with that env.

After this task, it will:
1. Look up the recipe by matching `oauth_cmd` to `recipe.oauth_cmd`.
2. Enter the `materialize_credentials` context.
3. `subprocess.Popen` with `HOME=<session.home_dir>` and the recipe's `static_env` (no `credential:` env-vars for file-based recipes).
4. Store the session with the materializer's `home_dir` path recorded so the WS handler can call `persist_token` on exit.
5. The `finally` of the context manager runs in `/oauth/stop` OR automatically when the WS handler detects subprocess exit and triggers cleanup.

Because `materialize_credentials` is a context manager, and `/oauth/start` returns immediately (doesn't wait for the subprocess), we need to store the ExitStack alongside the Popen in the session dict. The WS handler closes it when the subprocess exits; `/oauth/stop` closes it on forced stop.

### The existing `/oauth/start` request body

The gateway used to send `{"oauth_cmd": "...", "env_vars": {...}}`. We still accept that for backwards compatibility but now also accept `{"recipe_id": "gmail"}` — if recipe_id is given, we look up `recipe.oauth_cmd` ourselves. Everything gateway-side (Task 5) switches to recipe_id.

- [ ] **Step 1: Write the failing test for recipe_id dispatch**

In `tests/test_proxy_oauth_endpoints.py`, add:

```python


def test_oauth_start_with_recipe_id_materializes_keys(proxy_app, tmp_path, monkeypatch):
    """When called with recipe_id, the proxy looks up the recipe,
    materializes oauth_keys into a tmp HOME, and spawns with HOME set.
    Uses gmail: seed a credential, start the session, verify a file
    was written under the tmp HOME with the right shape."""
    # Seed the keys credential first.
    seed = proxy_app.post("/credential/store", json={
        "service": "gmail-oauth-keys",
        "label": "default",
        "payload": {"api_key": '{"installed": {"client_id": "c"}}'},
        "description": "test",
    })
    assert seed.status_code == 200, seed.text

    # Point the materializer at a writable tmp root we can inspect.
    from mycelos.security import proxy_server as ps
    monkeypatch.setattr(ps, "OAUTH_TMP_ROOT", tmp_path)

    resp = proxy_app.post("/oauth/start", json={
        "recipe_id": "gmail",
    })
    assert resp.status_code == 200, resp.text
    sid = resp.json()["session_id"]

    # One (and only one) tmp dir was created under tmp_path.
    tmpdirs = list(tmp_path.glob("mycelos-oauth-*"))
    assert len(tmpdirs) == 1
    keys_file = tmpdirs[0] / ".gmail-mcp" / "gcp-oauth.keys.json"
    assert keys_file.exists()
    assert '"client_id": "c"' in keys_file.read_text()

    # Stop the session — must also clean up the tmp dir.
    proxy_app.post("/oauth/stop", json={"session_id": sid})
    assert not tmpdirs[0].exists(), "tmp dir must be purged after stop"


def test_oauth_start_missing_keys_credential_fails_closed(proxy_app, tmp_path, monkeypatch):
    """If the recipe declares oauth_keys_credential_service but the row
    is missing, the proxy refuses to spawn (502) rather than running the
    auth command with no keys (which would silently fail)."""
    from mycelos.security import proxy_server as ps
    monkeypatch.setattr(ps, "OAUTH_TMP_ROOT", tmp_path)

    resp = proxy_app.post("/oauth/start", json={
        "recipe_id": "gmail",
    })
    assert resp.status_code == 502
    assert "gmail-oauth-keys" in (resp.json().get("error") or "")
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src pytest tests/test_proxy_oauth_endpoints.py::test_oauth_start_with_recipe_id_materializes_keys tests/test_proxy_oauth_endpoints.py::test_oauth_start_missing_keys_credential_fails_closed -v
```

Expected: both fail — `/oauth/start` doesn't know about recipe_id yet.

- [ ] **Step 3: Add a module-level `OAUTH_TMP_ROOT` constant to `proxy_server.py`**

Near the top of `src/mycelos/security/proxy_server.py`, after the existing imports, add:

```python
# Per-session materialization root for OAuth-based file materialization.
# Exposed at module level so tests can monkeypatch it to a tmp_path
# without touching the real /tmp. In the Docker proxy image this path
# lands on a tmpfs mount (see docker-compose.yml), so cleartext keys
# never hit persistent disk.
OAUTH_TMP_ROOT = Path("/tmp/mycelos-oauth")
```

Make sure `from pathlib import Path` is among the top-level imports; if not, add it.

- [ ] **Step 4: Extend McpStartRequest-style model with recipe_id (for /oauth/start)**

In `proxy_server.py` near the other request models, add or update:

```python
class OauthStartRequest(BaseModel):
    # Preferred shape — the proxy looks up the recipe itself and
    # applies file materialization. Keeps the gateway unaware of
    # the materializer.
    recipe_id: str | None = None
    # Legacy shape — direct command + env. Still accepted so older
    # callers (and low-level tests) work.
    oauth_cmd: str | None = None
    env_vars: dict = {}
```

- [ ] **Step 5: Rewrite the `/oauth/start` handler**

Find the existing `@app.post("/oauth/start")` handler in `proxy_server.py`. Replace it with:

```python
    @app.post("/oauth/start")
    async def oauth_start(request: Request) -> JSONResponse:
        """Spawn an OAuth auth subprocess and return a session id.

        Two calling conventions:
        * `{"recipe_id": "gmail"}` — preferred. Proxy looks up the recipe,
          materializes oauth_keys from the credential store into a
          session-scoped HOME, and spawns `recipe.oauth_cmd` with HOME
          pointing there. On clean exit the WS handler persists any
          token the subprocess wrote back into the store.
        * `{"oauth_cmd": "npx ... auth", "env_vars": {...}}` — legacy.
          No materialization; env-var injection only. Kept for the
          unit tests and for non-file tools that might add this later.
        """
        from contextlib import ExitStack
        import shlex
        import subprocess
        import secrets

        from mycelos.connectors.mcp_recipes import get_recipe
        from mycelos.security.credential_materializer import materialize_credentials

        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        body = await request.json()
        req = OauthStartRequest(**body)
        recipe = None
        if req.recipe_id:
            recipe = get_recipe(req.recipe_id)
            if recipe is None:
                return JSONResponse(
                    {"error": f"Unknown recipe: {req.recipe_id}"},
                    status_code=404,
                )
            if recipe.setup_flow != "oauth_browser":
                return JSONResponse(
                    {"error": f"Recipe '{req.recipe_id}' is not oauth_browser"},
                    status_code=400,
                )
            oauth_cmd = recipe.oauth_cmd
        else:
            oauth_cmd = req.oauth_cmd or ""

        parts = shlex.split(oauth_cmd)
        if not parts or parts[0] != "npx":
            return JSONResponse(
                {"error": "oauth_cmd must start with 'npx' — recipe validation"},
                status_code=400,
            )

        credential_proxy = _get_credential_proxy()

        # For recipe-dispatched calls, verify the keys credential is present
        # BEFORE we open the materializer context (so we return a clean 502
        # instead of a half-created tmpdir).
        if recipe and recipe.oauth_keys_credential_service and credential_proxy is not None:
            keys_cred = credential_proxy.get_credential(
                recipe.oauth_keys_credential_service, user_id=user_id,
            )
            if not keys_cred or not keys_cred.get("api_key"):
                return JSONResponse(
                    {
                        "error": (
                            f"Credential '{recipe.oauth_keys_credential_service}' "
                            "not found — upload the OAuth keys first."
                        )
                    },
                    status_code=502,
                )

        session_id = f"oauth-{secrets.token_hex(6)}"
        stack = ExitStack()

        try:
            env = dict(os.environ)
            home_dir: Path | None = None

            if recipe is not None:
                OAUTH_TMP_ROOT.mkdir(parents=True, exist_ok=True)
                mat = stack.enter_context(materialize_credentials(
                    recipe=recipe,
                    credential_proxy=credential_proxy,
                    user_id=user_id,
                    tmp_root=OAUTH_TMP_ROOT,
                    session_id=session_id,
                ))
                home_dir = mat.home_dir
                env["HOME"] = str(home_dir)
                # Include static_env the recipe declares (TRANSPORT_MODE etc.)
                for k, v in (recipe.static_env or {}).items():
                    env[k] = v
            else:
                # Legacy path — resolve credential: refs inline.
                for key, val in (req.env_vars or {}).items():
                    if isinstance(val, str) and val.startswith("credential:") and credential_proxy:
                        service = val[len("credential:"):]
                        cred = credential_proxy.get_credential(service, user_id=user_id)
                        if not cred or not cred.get("api_key"):
                            cred = credential_proxy.get_credential(
                                f"connector:{service}", user_id=user_id,
                            )
                        if not cred or not cred.get("api_key"):
                            stack.close()
                            return JSONResponse(
                                {"error": f"Credential '{service}' not found — fail-closed"},
                                status_code=502,
                            )
                        env[key] = cred["api_key"]
                    else:
                        env[key] = str(val)

            proc = subprocess.Popen(
                parts,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,
            )
        except Exception as e:
            stack.close()
            logger.error("oauth_start spawn failed: %s", e)
            return JSONResponse({"error": "subprocess spawn failed"}, status_code=500)

        _state["_oauth_sessions"][session_id] = {
            "proc": proc,
            "user_id": user_id,
            "oauth_cmd": oauth_cmd,
            "recipe_id": recipe.id if recipe else None,
            "home_dir": str(home_dir) if home_dir else None,
            "stack": stack,
        }

        storage = _get_storage()
        if storage:
            _write_audit(storage, "proxy.oauth_started", user_id, {
                "session_id": session_id,
                "oauth_cmd": oauth_cmd,
                "recipe_id": recipe.id if recipe else None,
            })
            if recipe and recipe.oauth_keys_credential_service:
                _write_audit(storage, "credential.materialized", user_id, {
                    "service": recipe.oauth_keys_credential_service,
                    "session_id": session_id,
                })

        return JSONResponse({"session_id": session_id})
```

- [ ] **Step 6: Update `/oauth/stop` to close the ExitStack**

Find the existing `@app.post("/oauth/stop")` handler. Update the cleanup to close the stack (which runs the materializer's `finally`):

```python
    @app.post("/oauth/stop")
    async def oauth_stop(request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        body = await request.json()
        session_id: str = body.get("session_id", "")
        sess = _state["_oauth_sessions"].pop(session_id, None)
        if sess is None:
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

        # Close the materializer stack — runs finally blocks that
        # purge the tmp HOME.
        stack = sess.get("stack")
        if stack is not None:
            try:
                stack.close()
            except Exception:
                logger.warning("oauth_stop stack.close failed for %s", session_id)

        storage = _get_storage()
        if storage:
            _write_audit(storage, "proxy.oauth_stopped", user_id, {
                "session_id": session_id,
            })
            if sess.get("home_dir"):
                _write_audit(storage, "credential.purged", user_id, {
                    "session_id": session_id,
                })

        return JSONResponse({"status": "stopped"})
```

- [ ] **Step 7: Update the WS handler to persist the token and close the stack on clean exit**

Find the existing `@app.websocket("/oauth/stream/{session_id}")` handler. At the end, where the subprocess has exited and we send the `done` frame, add token persistence + stack cleanup:

Replace the tail of that handler (everything from `exit_code = await loop.run_in_executor(None, proc.wait)` onwards, up to and including `_state["_oauth_sessions"].pop(session_id, None)`) with:

```python
        loop = asyncio.get_running_loop()
        exit_code = await loop.run_in_executor(None, proc.wait)

        for t in (stdout_task, stderr_task):
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except Exception:
                t.cancel()

        # On clean exit, write the token (if any) back to the store.
        if exit_code == 0 and sess.get("recipe_id") and sess.get("home_dir"):
            from mycelos.connectors.mcp_recipes import get_recipe
            from mycelos.security.credential_materializer import persist_token

            recipe = get_recipe(sess["recipe_id"])
            if recipe is not None:
                try:
                    persist_token(
                        recipe=recipe,
                        credential_proxy=_get_credential_proxy(),
                        user_id=sess["user_id"],
                        home_dir=Path(sess["home_dir"]),
                    )
                    storage = _get_storage()
                    if storage and recipe.oauth_token_credential_service:
                        _write_audit(storage, "credential.token_persisted", sess["user_id"], {
                            "service": recipe.oauth_token_credential_service,
                            "session_id": session_id,
                        })
                except Exception as e:
                    logger.error("token persist failed for %s: %s", session_id, e)

        try:
            await websocket.send_text(json.dumps({
                "type": "done",
                "exit_code": exit_code,
                "data": "",
            }))
        except Exception:
            pass

        stdin_task.cancel()

        # Close the ExitStack — purges the tmp HOME.
        stack = sess.get("stack")
        if stack is not None:
            try:
                stack.close()
            except Exception:
                logger.warning("oauth_stream stack.close failed for %s", session_id)

        _state["_oauth_sessions"].pop(session_id, None)

        try:
            await websocket.close()
        except Exception:
            pass
```

- [ ] **Step 8: Add a WS token-persistence test**

In `tests/test_proxy_oauth_websocket.py`, add:

```python


def test_websocket_persists_token_on_clean_exit(proxy_app, tmp_path, monkeypatch):
    """When a recipe-dispatched subprocess exits 0 and wrote a token
    file, the proxy must store it as oauth_token_credential_service."""
    import json
    import os

    # Seed keys credential.
    proxy_app.post("/credential/store", json={
        "service": "gmail-oauth-keys",
        "label": "default",
        "payload": {"api_key": '{"installed": {"client_id": "c"}}'},
        "description": "test",
    })

    from mycelos.security import proxy_server as ps
    monkeypatch.setattr(ps, "OAUTH_TMP_ROOT", tmp_path)

    # Use a tiny python one-liner as the 'auth' command stand-in:
    # it writes a token file to $HOME/.gmail-mcp/credentials.json and
    # exits 0, which is exactly the Gmail MCP's observable behavior on
    # success. We can't use gmail's real auth in a unit test, so inject
    # a monkeypatched recipe-command via the private LEGACY path.
    #
    # To exercise the materializer-driven path, override the recipe's
    # oauth_cmd in-place for this test.
    from mycelos.connectors.mcp_recipes import RECIPES
    original_cmd = RECIPES["gmail"].oauth_cmd
    # We need a command that:
    #  1. starts with 'npx' to pass the allowlist,
    #  2. writes a token file inside $HOME/.gmail-mcp/,
    #  3. exits 0.
    # 'npx -y --package=cowsay -- cowsay' is npm-available and exits
    # fast. We instead monkeypatch the allowlist check to let python
    # through.
    monkeypatch.setattr(
        RECIPES["gmail"], "oauth_cmd",
        f'python -c "import os,pathlib;p=pathlib.Path(os.environ[\'HOME\'])/\'.gmail-mcp\';p.mkdir(parents=True,exist_ok=True);(p/\'credentials.json\').write_text(\'{{\\"access_token\\":\\"fake\\"}}\');"',
    )
    # Widen the allowlist locally to accept 'python'.
    import shlex as _shlex
    original_split = _shlex.split
    # The allowlist lives in the handler as `parts[0] != "npx"`. Simplest
    # route: monkeypatch the handler's allowlist sentinel.
    # (See proxy_server.py: adjust allowed commands per-test.)
    monkeypatch.setattr(ps, "_OAUTH_ALLOWED_HEADS", ("npx", "python"))

    resp = proxy_app.post("/oauth/start", json={"recipe_id": "gmail"})
    assert resp.status_code == 200, resp.text
    sid = resp.json()["session_id"]

    # Connect WS so the stream handler runs proc.wait + persist_token.
    import json as _json
    from starlette.websockets import WebSocketDisconnect
    with proxy_app.websocket_connect(
        f"/oauth/stream/{sid}",
        headers={"Authorization": "Bearer test-token"},
    ) as ws:
        # Read frames until 'done'.
        for _ in range(200):
            try:
                raw = ws.receive_text()
            except Exception:
                break
            frame = _json.loads(raw)
            if frame.get("type") == "done":
                assert frame["exit_code"] == 0
                break

    # Credential was stored.
    lst = proxy_app.get("/credential/list").json()
    services = [c["service"] for c in lst.get("credentials", [])]
    assert "gmail-oauth-token" in services

    # Tmp dir was purged.
    assert not list(tmp_path.glob("mycelos-oauth-*"))

    # Restore original recipe command.
    RECIPES["gmail"].oauth_cmd = original_cmd
```

**Note**: The test monkeypatches `_OAUTH_ALLOWED_HEADS` — add that as a module-level tuple in `proxy_server.py`:

```python
# Allowed first tokens for oauth_cmd. Tests widen this to include 'python'
# for recipe-driven unit tests that don't want to spawn a real npx package.
_OAUTH_ALLOWED_HEADS = ("npx",)
```

And in the `/oauth/start` handler, change:

```python
        if not parts or parts[0] != "npx":
```

to:

```python
        if not parts or parts[0] not in _OAUTH_ALLOWED_HEADS:
```

- [ ] **Step 9: Verify all proxy oauth tests pass**

```bash
PYTHONPATH=src pytest tests/test_proxy_oauth_endpoints.py tests/test_proxy_oauth_websocket.py -v
```

Expected: all green (5 existing + 2 new endpoint + 1 new ws + 2 existing ws = 10).

- [ ] **Step 10: Broader baseline**

```bash
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -5
```

Expected: no regressions.

- [ ] **Step 11: Commit**

```bash
git add src/mycelos/security/proxy_server.py tests/test_proxy_oauth_endpoints.py tests/test_proxy_oauth_websocket.py
git commit -m "feat(proxy): materialize OAuth keys + persist token in /oauth/start"
```

---

## Task 4 — Apply materialization to the real `/mcp/start`

**Files:**
- Modify: `src/mycelos/security/proxy_server.py`
- Modify: `src/mycelos/connectors/mcp_manager.py` (pass env correctly)
- Modify: `tests/test_proxy_oauth_endpoints.py` (new test)

### Context

`/oauth/start` is the one-shot auth. The **real MCP server** runs via `/mcp/start` (completely separate code path). It currently takes `command: list[str]` + `env_vars: dict` and spawns. For file-based recipes, `env_vars` is useless — the server reads files. So `/mcp/start` needs the same materialization: read recipe, enter `materialize_credentials` with BOTH keys and token, spawn with `HOME=<tmp>`.

Difference from `/oauth/start`: the lifecycle is **long-lived**. The ExitStack lives in `_mcp_sessions[session_id]["stack"]` and closes on `/mcp/stop`.

- [ ] **Step 1: Write the failing test**

In `tests/test_proxy_oauth_endpoints.py` (keep related tests together), add:

```python


def test_mcp_start_for_recipe_materializes_keys_and_token(proxy_app, tmp_path, monkeypatch):
    """When /mcp/start is called for a file-based recipe, the proxy
    materializes BOTH keys and token into a session HOME, spawns the
    server with HOME set, and purges on /mcp/stop."""
    # Seed keys + token.
    proxy_app.post("/credential/store", json={
        "service": "gmail-oauth-keys",
        "label": "default",
        "payload": {"api_key": '{"installed": {"client_id": "c"}}'},
        "description": "keys",
    })
    proxy_app.post("/credential/store", json={
        "service": "gmail-oauth-token",
        "label": "default",
        "payload": {"api_key": '{"access_token": "ya29.test"}'},
        "description": "token",
    })

    from mycelos.security import proxy_server as ps
    monkeypatch.setattr(ps, "OAUTH_TMP_ROOT", tmp_path)

    # Use a command that exits fast but lives long enough for
    # materialization to be observable.
    monkeypatch.setattr(ps, "_MCP_ALLOWED_HEADS", ("npx", "python"))
    resp = proxy_app.post("/mcp/start", json={
        "connector_id": "gmail",
        "command": [
            "python", "-c",
            "import os, time; time.sleep(0.2); print('ok', os.environ.get('HOME'))",
        ],
        "env_vars": {},
        "transport": "stdio",
    })
    # /mcp/start returns after initial handshake or immediately for
    # non-MCP commands — accept 200 or a 500 with tools=None; the
    # materialization happens before spawn either way.
    tmpdirs = list(tmp_path.glob("mycelos-oauth-*"))
    assert len(tmpdirs) == 1
    assert (tmpdirs[0] / ".gmail-mcp" / "gcp-oauth.keys.json").exists()
    assert (tmpdirs[0] / ".gmail-mcp" / "credentials.json").exists()

    # Stop — tmp dir is purged.
    if resp.status_code == 200:
        sid = resp.json().get("session_id", "")
        if sid:
            proxy_app.post("/mcp/stop", json={"session_id": sid})
    # Eventually the tmp dir is gone. (We accept either: if start errored
    # out before session_id was minted, the stack close in the except
    # branch still purges.)
    assert not tmpdirs[0].exists()
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src pytest tests/test_proxy_oauth_endpoints.py::test_mcp_start_for_recipe_materializes_keys_and_token -v
```

Expected: fail — `/mcp/start` doesn't materialize.

- [ ] **Step 3: Add an `_MCP_ALLOWED_HEADS` constant near `_OAUTH_ALLOWED_HEADS`**

In `proxy_server.py`:

```python
# Mirror of _OAUTH_ALLOWED_HEADS for /mcp/start. Production recipes
# always start with `npx`; tests may widen the allowlist.
_MCP_ALLOWED_HEADS = ("npx",)
```

- [ ] **Step 4: Extend `/mcp/start` to apply materialization**

Find the `@app.post("/mcp/start")` handler. At the top of the handler body (after `authorized, user_id = _check_auth(request)`), insert logic to look up the recipe via `connector_id` and materialize if needed. Here's the full updated handler — replace the existing one:

```python
    @app.post("/mcp/start")
    async def mcp_start(request: Request) -> JSONResponse:
        """Start an MCP session. For file-based recipes (Gmail etc.),
        materialize credentials into a tmp HOME before spawn and keep
        the ExitStack alive for the session's lifetime."""
        from contextlib import ExitStack
        from mycelos.connectors.mcp_recipes import get_recipe
        from mycelos.security.credential_materializer import materialize_credentials

        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        agent_id = request.headers.get("X-Agent-Id", "")
        body_data = await request.json()
        req = McpStartRequest(**body_data)

        storage = _get_storage()
        t_start = time.time()

        # Head-of-command allowlist (test monkeypatches may widen it).
        head = req.command[0] if req.command else ""
        if head and head not in _MCP_ALLOWED_HEADS:
            return JSONResponse(
                {"error": f"command[0]='{head}' not in MCP allowlist"},
                status_code=400,
            )

        credential_proxy = _get_credential_proxy()
        recipe = get_recipe(req.connector_id)

        stack = ExitStack()
        session_id = f"mcp-{req.connector_id}-{__import__('secrets').token_hex(6)}"
        home_dir: Path | None = None

        try:
            resolved_env: dict[str, str] = {}
            if recipe is not None and recipe.oauth_keys_credential_service:
                OAUTH_TMP_ROOT.mkdir(parents=True, exist_ok=True)
                mat = stack.enter_context(materialize_credentials(
                    recipe=recipe,
                    credential_proxy=credential_proxy,
                    user_id=user_id,
                    tmp_root=OAUTH_TMP_ROOT,
                    session_id=session_id,
                ))
                home_dir = mat.home_dir
                resolved_env["HOME"] = str(home_dir)
                for k, v in (recipe.static_env or {}).items():
                    resolved_env[k] = v
                # For materialized recipes we ignore req.env_vars — the
                # package reads from files, not env vars.
            else:
                # Existing credential:<service> env-var path unchanged.
                for key, val in req.env_vars.items():
                    if val.startswith("credential:") and credential_proxy:
                        service_name = val[len("credential:"):]
                        try:
                            cred = credential_proxy.get_credential(service_name, user_id=user_id)
                            if not (cred and cred.get("api_key")):
                                cred = credential_proxy.get_credential(
                                    f"connector:{service_name}", user_id=user_id,
                                )
                            if cred and cred.get("api_key"):
                                resolved_env[key] = cred["api_key"]
                            else:
                                stack.close()
                                return JSONResponse(
                                    {"error": f"Credential '{service_name}' not found for env var '{key}' — denied (fail-closed)"},
                                    status_code=502,
                                )
                        except Exception:
                            stack.close()
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
                stack.close()
                logger.error("MCP start failed for connector '%s': %s", req.connector_id, e)
                return JSONResponse(
                    {"error": "MCP connector start failed. Check server logs for details.", "status": 0},
                    status_code=500,
                )
        except Exception:
            stack.close()
            raise

        _state["_mcp_sessions"][session_id] = {
            "connector_id": req.connector_id,
            "stack": stack,
            "home_dir": str(home_dir) if home_dir else None,
        }

        duration = time.time() - t_start
        if storage:
            _write_audit(storage, "proxy.mcp_started", user_id, {
                "connector_id": req.connector_id,
                "command": req.command,
                "transport": req.transport,
                "agent_id": agent_id,
                "duration": round(duration, 3),
            })
            if recipe and recipe.oauth_keys_credential_service:
                _write_audit(storage, "credential.materialized", user_id, {
                    "service": recipe.oauth_keys_credential_service,
                    "session_id": session_id,
                })

        return JSONResponse({"session_id": session_id, "tools": tools})
```

**Note**: `_mcp_sessions` changed shape — it used to map session_id → connector_id directly (a string). Now it maps session_id → dict with `connector_id`, `stack`, `home_dir`. Check every other reader of `_mcp_sessions` and update.

- [ ] **Step 5: Update `/mcp/call` and `/mcp/stop` to read the new session shape**

In the same file, find `/mcp/call`:

```python
        if req.session_id not in _state["_mcp_sessions"]:
```

Unchanged — still a key existence check. But any use of `_state["_mcp_sessions"][sid]` as a bare string (for `connector_id`) needs `.get("connector_id")` now. Grep for `_mcp_sessions[` and `_mcp_sessions.get(` and check each.

Find `/mcp/stop`:

```python
        _state["_mcp_sessions"].pop(req.session_id, None)
```

Replace with:

```python
        sess = _state["_mcp_sessions"].pop(req.session_id, None)
        if sess and isinstance(sess, dict):
            stack = sess.get("stack")
            if stack is not None:
                try:
                    stack.close()
                except Exception:
                    logger.warning("mcp_stop stack.close failed for %s", req.session_id)
            if sess.get("home_dir") and storage:
                _write_audit(storage, "credential.purged", user_id, {
                    "session_id": req.session_id,
                })
```

- [ ] **Step 6: Verify tests pass**

```bash
PYTHONPATH=src pytest tests/test_proxy_oauth_endpoints.py tests/test_proxy_oauth_websocket.py -v
```

Expected: all green.

- [ ] **Step 7: Baseline — the /mcp/call path still works**

```bash
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -5
```

Expected: no regressions in the existing `/mcp/*` tests (the shape change broke direct dict access for non-recipe callers; step 5 fixed those).

- [ ] **Step 8: Commit**

```bash
git add src/mycelos/security/proxy_server.py tests/test_proxy_oauth_endpoints.py
git commit -m "feat(proxy): apply credential materialization to /mcp/start"
```

---

## Task 5 — Simplify gateway + frontend

**Files:**
- Modify: `src/mycelos/gateway/routes.py` — oauth_start_passthrough
- Modify: `tests/test_gateway_oauth_proxy.py`
- Modify: `src/mycelos/frontend/pages/connectors.html`

### Context

The gateway's `/api/connectors/oauth/start` previously:
1. Read `env_vars` from the browser.
2. Filtered against `recipe.credentials`.
3. Passed to `proxy_client.oauth_start(oauth_cmd=recipe.oauth_cmd, env_vars=filtered)`.

With materialization, the proxy looks up the recipe itself via `recipe_id`. We no longer need to send env_vars at all for oauth_browser recipes — they use the materializer.

- [ ] **Step 1: Write the failing test**

In `tests/test_gateway_oauth_proxy.py`, replace `test_oauth_start_passthrough_forwards_to_proxy` with:

```python
def test_oauth_start_passthrough_sends_recipe_id(client_with_mock_proxy):
    """After materialization refactor the gateway sends just
    {recipe_id}: the proxy does the env/HOME setup itself."""
    client, mock = client_with_mock_proxy
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "gmail",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == "oauth-testsid"
    assert body["ws_url"] == "/api/connectors/oauth/stream/oauth-testsid"

    # Proxy was called with recipe_id=gmail.
    call = mock.oauth_start.call_args
    kwargs = call.kwargs or {}
    assert kwargs.get("recipe_id") == "gmail"
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src pytest tests/test_gateway_oauth_proxy.py::test_oauth_start_passthrough_sends_recipe_id -v
```

Expected: fails — the gateway still calls `proxy_client.oauth_start(oauth_cmd=..., env_vars=...)`, not `recipe_id=`.

- [ ] **Step 3: Update `proxy_client.oauth_start` to accept recipe_id**

In `src/mycelos/security/proxy_client.py`, find `oauth_start`. Replace:

```python
    def oauth_start(self, oauth_cmd: str, env_vars: dict, user_id: str = "default") -> dict:
```

with:

```python
    def oauth_start(
        self,
        oauth_cmd: str | None = None,
        env_vars: dict | None = None,
        recipe_id: str | None = None,
        user_id: str = "default",
    ) -> dict:
        """Spawn an OAuth auth subprocess in the proxy.

        Preferred: pass `recipe_id` — the proxy looks up the recipe and
        handles file materialization internally. Legacy callers (unit
        tests, non-file tools) may pass `oauth_cmd` + `env_vars` instead.
        """
        payload: dict = {}
        if recipe_id is not None:
            payload["recipe_id"] = recipe_id
        if oauth_cmd is not None:
            payload["oauth_cmd"] = oauth_cmd
        if env_vars is not None:
            payload["env_vars"] = env_vars
        resp = self._request("POST", "/oauth/start", json=payload,
                             headers={"X-User-Id": user_id})
        return resp.json()
```

- [ ] **Step 4: Update gateway handler**

In `src/mycelos/gateway/routes.py`, find `oauth_start_passthrough`. Replace:

```python
        # Restrict env_vars to the keys the recipe actually declares.
        # [... env filtering ...]
        result = proxy_client.oauth_start(
            oauth_cmd=recipe.oauth_cmd,
            env_vars=filtered_env,
        )
```

with:

```python
        # Gateway no longer handles env_vars for oauth_browser recipes;
        # the proxy materializes file-based credentials itself via the
        # recipe lookup.
        result = proxy_client.oauth_start(
            recipe_id=recipe_id,
        )
```

Remove the now-unused `env_vars` reading at the top of the handler (`env_vars = payload.get("env_vars", {}) or {}` and the `allowed_env_keys`/`filtered_env` derivation).

- [ ] **Step 5: Update frontend `submitOAuthKeysAndStart`**

In `src/mycelos/frontend/pages/connectors.html`, find `submitOAuthKeysAndStart`. Locate the block:

```javascript
            // 2. Ask the gateway to start the auth subprocess.
            const startResp = await fetch('/api/connectors/oauth/start', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                recipe_id: this.oauthDialog.recipeId,
                env_vars: {[envVar]: 'credential:' + credService},
              }),
            });
```

Replace with:

```javascript
            // 2. Ask the gateway to start the auth subprocess. The
            //    proxy looks up the recipe and materializes the keys
            //    file itself — no env_vars needed from the browser.
            const startResp = await fetch('/api/connectors/oauth/start', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                recipe_id: this.oauthDialog.recipeId,
              }),
            });
```

The `envVar` and `credService` local variables are still needed for the earlier `POST /api/credentials` call (we still store the keys blob under `<recipe-id>-oauth-keys`). Leave that block untouched.

- [ ] **Step 6: Update `submitOAuthKeysAndStart` to store under the materialization service name**

Still in `submitOAuthKeysAndStart`, locate:

```javascript
            const credService = this.oauthDialog.recipeId + '-oauth-keys';
```

Keep it. It matches what the recipe declares: `gmail → gmail-oauth-keys`, `google-calendar → google-calendar-oauth-keys`, `google-drive → google-drive-oauth-keys`. Already consistent.

- [ ] **Step 7: Verify tests**

```bash
PYTHONPATH=src pytest tests/test_gateway_oauth_proxy.py -v
```

Expected: all 5 green (the old `test_oauth_start_passthrough_forwards_to_proxy` is replaced by `test_oauth_start_passthrough_sends_recipe_id`).

- [ ] **Step 8: Commit**

```bash
git add src/mycelos/gateway/routes.py src/mycelos/security/proxy_client.py src/mycelos/frontend/pages/connectors.html tests/test_gateway_oauth_proxy.py
git commit -m "feat(gateway): send recipe_id to proxy, drop env_vars plumbing"
```

---

## Task 6 — Docker tmpfs, docs, changelog, merge

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docs/deployment/google-setup.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add tmpfs mount to the proxy service**

In `docker-compose.yml`, find the `proxy:` service block. Before `networks:`, add:

```yaml
    tmpfs:
      # Per-session OAuth materialization lives here. tmpfs keeps
      # cleartext keys + tokens off persistent disk; they exist only
      # for the lifetime of the auth subprocess (or the live MCP
      # session) and are purged explicitly on stop, with a
      # belt-and-braces guarantee from the tmpfs itself on container
      # restart.
      - /tmp/mycelos-oauth:size=16m,mode=0700
```

- [ ] **Step 2: Rewrite google-setup.md**

Open `docs/deployment/google-setup.md`. Replace all its contents with:

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
5. Open the consent URL the dialog shows, sign in with the account
   you added as a Test user, accept the scopes.
6. Done. Close the dialog; the connector is live.

All three Google services share a single Google Cloud project, so
steps 1–3 only happen once.

## How it works under the hood

- **Keys JSON** (`gcp-oauth.keys.json`) is stored encrypted in the
  SecurityProxy's credential store (under service names like
  `gmail-oauth-keys`). The gateway and the LLM never see it.
- **Before the `npx ... auth` subprocess spawns**, the proxy
  materializes the JSON into a session-scoped tmp HOME
  (`/tmp/mycelos-oauth-<sid>/.gmail-mcp/gcp-oauth.keys.json`). That
  tmp path sits on a tmpfs mount — cleartext never hits persistent
  disk.
- **After the subprocess exits cleanly**, the proxy reads the token
  file the tool wrote (`credentials.json` for Gmail, `token.json`
  for Calendar/Drive), stores it as a second credential
  (`gmail-oauth-token` etc.), and purges the tmp dir.
- **Future MCP-server runs** do the same dance: materialize both
  credentials, spawn, purge on stop. The files exist only for as
  long as the server runs.

## Troubleshooting

### "invalid_grant" on first call

The token expired or was never issued. Delete the
`<recipe>-oauth-token` credential from Settings → Credentials and
run the OAuth consent again.

### "access_denied" during consent

Your Google account isn't listed as a Test user on the OAuth
consent screen. Go back to Cloud Console → **OAuth consent screen**
and add your account under "Test users".

### The dialog hangs on "Waiting for the auth server to print a URL"

Usually means the subprocess crashed before printing its consent
URL. Click **Show subprocess log** in the dialog to see stderr. Most
common cause: uploaded a Web-app OAuth credential instead of a
Desktop-app one. Re-create the credential as Desktop app in Cloud
Console.

## Security notes

- The master key lives only in the proxy container. The gateway
  cannot decrypt credentials on its own.
- Cleartext keys / tokens exist only for the lifetime of the
  subprocess that needs them, inside a tmpfs-backed directory
  scoped to the session id.
- Each MCP server's scopes are visible in the connector card
  before you click Connect. Review them before consenting.
```

- [ ] **Step 3: Changelog entry**

In `CHANGELOG.md`, find the existing Week 17 block's "OAuth connector setup in the web UI" subsection. After it, append:

```markdown

### File-based credential materialization
- Upstream MCP packages that read credentials from hardcoded file paths (e.g. `~/.gmail-mcp/gcp-oauth.keys.json`) now participate in the encrypted-credential flow without breaking the "DB is the only persistent copy" rule. New `credential_materializer` module writes the credential blob to a session-scoped tmp HOME right before `Popen`, sets `HOME=` on the spawned subprocess, and purges on exit via a context manager's `finally`.
- Proxy's `/oauth/start` and `/mcp/start` now accept `recipe_id` and handle materialization themselves. After a clean `npx ... auth` run, the token file the tool wrote is read back and stored as a second credential (`<recipe>-oauth-token`). Future MCP-server runs materialize both files.
- Gateway's `/api/connectors/oauth/start` now sends only `{recipe_id}`; the proxy looks up the recipe and drives the flow. Frontend `submitOAuthKeysAndStart` is simpler by one parameter.
- Docker `tmpfs` mount on the proxy's `/tmp/mycelos-oauth` — cleartext keys/tokens never hit persistent disk.
- New audit events: `credential.materialized`, `credential.purged`, `credential.token_persisted`.
- New `MCPRecipe` fields for file-based tools: `oauth_keys_credential_service`, `oauth_keys_home_dir`, `oauth_keys_filename`, `oauth_token_filename`, `oauth_token_credential_service`. The three Google recipes use them.
```

- [ ] **Step 4: Final baseline**

```bash
cd /Users/stefan/Documents/railsapps/mycelos
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -5
```

Expected: all passing. Count = previous baseline + 13 new tests (Task 1: 5, Task 2: 7, Task 3: 2, Task 4: 1 — minus 1 replaced gateway test from Task 5 = +14 net, maths approximate).

- [ ] **Step 5: Smoke-import**

```bash
PYTHONPATH=src python -c "
from mycelos.security.credential_materializer import materialize_credentials, persist_token
from mycelos.security.proxy_server import create_proxy_app, OAUTH_TMP_ROOT
from mycelos.connectors.mcp_recipes import RECIPES
assert RECIPES['gmail'].oauth_keys_credential_service == 'gmail-oauth-keys'
print('ok')
"
```

- [ ] **Step 6: Commit docs + changelog + compose**

```bash
git add docker-compose.yml docs/deployment/google-setup.md CHANGELOG.md
git commit -m "docs: file-based credential materialization + docker tmpfs"
```

- [ ] **Step 7: Merge + push**

```bash
git checkout main
git pull
git merge --no-ff feature/file-credential-materialization -m "Merge feature/file-credential-materialization: file-based MCP credential support"
git push origin main
```

- [ ] **Step 8: Cleanup**

```bash
cd /Users/stefan/Documents/railsapps/mycelos
git worktree remove .worktrees/file-credential-materialization
git branch -d feature/file-credential-materialization
```

---

## Self-Review

### Spec coverage

| Requirement from brainstorm | Task |
|---|---|
| DB stays the single source of truth | Tasks 2, 3, 4 — credentials only read from / written to `credential_proxy`, files are ephemeral cache |
| Per-session tmp HOME | Task 2 (materializer) + Tasks 3, 4 (use it) |
| Automatic cleanup on exit | Task 2 (context manager `finally`) + Tasks 3, 4 (ExitStack) |
| Token persistence back to DB | Task 3 Step 7 (WS handler calls `persist_token`) |
| `/mcp/start` uses materialization too | Task 4 |
| Audit events: materialized / purged / token_persisted | Tasks 3, 4 |
| tmpfs for cleartext paths | Task 6 Step 1 |
| Frontend no longer sends env_vars | Task 5 Step 5 |
| Docs updated for new flow | Task 6 Step 2 |
| `HOME=` trick for Gmail's hardcoded `~/.gmail-mcp` | Task 3 Step 5 (sets `env["HOME"] = str(home_dir)`) |

### Placeholder scan
No "TODO" / "TBD" markers. Every code step has literal content. Test code names concrete fixtures.

### Type consistency
- `MaterializedSession.home_dir` is `Path` — used as `Path` in Task 3 and Task 4.
- Session dict shape changes in Task 4 (from `sid → connector_id_str` to `sid → {connector_id, stack, home_dir}`). Task 4 Step 5 explicitly updates all downstream readers.
- Recipe field names (`oauth_keys_credential_service`, `oauth_keys_home_dir`, `oauth_keys_filename`, `oauth_token_filename`, `oauth_token_credential_service`) identical across recipe declarations, materializer, `/oauth/start`, `/mcp/start`, audit events.
- `_OAUTH_ALLOWED_HEADS` / `_MCP_ALLOWED_HEADS` both in `proxy_server.py`, both with the same shape (`tuple[str, ...]`).

### Open trade-offs flagged
- The legacy `oauth_cmd` + `env_vars` request shape on `/oauth/start` is kept for tests only — not a public API. Could be removed once all callers migrate, but YAGNI. Flagged.
- `HOME=` redirection affects the *entire* subprocess env, not just the credential paths. This is the simplest reliable approach; if a future MCP package reads some other `$HOME`-derived path (like `~/.cache/`), that'll also land in our tmpfs. Acceptable.
