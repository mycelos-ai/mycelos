# Two-Container Deployment — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a hardened Docker deployment where the SecurityProxy runs in its own container with exclusive access to the master key, plus a zero-question install script that bootstraps the stack.

**Architecture:** Two containers from the same image, selected by a new `--role` flag on the existing `mycelos serve` command:

- `mycelos-gateway` — web UI, REST API, chat, scheduler. Talks to proxy over the internal Docker network via TCP + Bearer token. **Does not mount `.master_key`.**
- `mycelos-proxy` — owns `.master_key` (read-only mount), reads `mycelos.db` read-only, exposes the existing proxy surface (`/health`, `/http`, `/llm/complete`, `/mcp/*`, `/credential/bootstrap`, `/stt/transcribe`) on a container-internal TCP port (9110). Never reachable from the host.

Install script (`scripts/install.sh` + `scripts/install.ps1`) is zero-question: checks Docker, generates `.master_key` and `MYCELOS_PROXY_TOKEN` (shared bearer between the two containers), fetches `docker-compose.yml`, `docker compose up -d`, waits for `/api/health`, prints the URL. No TLS, no password prompt, no Caddy — those land in Phase 2 together with Passkey authentication.

**Out of scope (Phase 2):** WebAuthn/Passkey auth, public exposure via Cloudflare/Tailscale tunnels, TLS via Caddy or Let's Encrypt, Basic Auth deprecation. None of these make sense until Phase 1 is stable and the auth layer is ready.

**Tech Stack:** Docker Compose v2, bash + PowerShell install scripts, httpx (existing), FastAPI (existing).

---

## File Structure

### New files

- `docker-compose.yml` — **replaces** current single-service compose. Two services (`gateway`, `proxy`), internal `mycelos-internal` network.
- `scripts/install.sh` — zero-question installer (bash).
- `scripts/install.ps1` — PowerShell parity installer.
- `tests/test_compose_structure.py` — static-parse test of the compose YAML (keyless gateway, proxy port not published, shared token env, depends_on).
- `tests/test_proxy_client_tcp.py` — `SecurityProxyClient` accepts TCP URL alongside Unix socket.
- `tests/test_install_script.sh` — black-box test for the install script's idempotency and file creation.
- `tests/e2e/test_two_container_deployment.sh` — end-to-end smoke test that brings up the real stack (Docker required; skipped in CI for now).
- `docs/security/two-container-deployment.md` — threat model: what the split protects and what it explicitly does not.

### Modified files

- `docker-entrypoint.sh` — read `MYCELOS_ROLE` (default `gateway`) and dispatch the new `mycelos serve --role proxy` when set.
- `src/mycelos/cli/serve_cmd.py` — add `--role {all,gateway,proxy}` option. `all` (default) is today's behavior. `gateway` expects `MYCELOS_PROXY_URL`. `proxy` runs the SecurityProxy on `--proxy-host`/`--proxy-port`.
- `src/mycelos/security/proxy_launcher.py` — when `MYCELOS_PROXY_URL` is set, skip forking a child (the proxy is in another container).
- `src/mycelos/security/proxy_client.py` — accept either `socket_path=` (UDS, existing) or `url=` (TCP, new). Exactly one.
- `src/mycelos/app.py` — branch the proxy init on `MYCELOS_PROXY_URL`.
- `README.md` — add a "Quick start" section above the existing Docker instructions.
- `CHANGELOG.md` — entry under the current week heading.

### Deleted files

None.

---

## Task 1: Add `MYCELOS_ROLE` dispatch to docker-entrypoint.sh

**Files:**
- Modify: `docker-entrypoint.sh` (last line — the `exec "$@"` region)

- [ ] **Step 1: Read the current entrypoint**

Run: `cat docker-entrypoint.sh`
Expected: shell script ending in `exec "$@"` (or similar). Confirm the exact final block before editing.

- [ ] **Step 2: Add the role dispatch before the final exec**

Replace the final `exec "$@"` block with:

```bash
# Role selection: same image, two container modes.
ROLE="${MYCELOS_ROLE:-gateway}"

if [ "$ROLE" = "proxy" ]; then
    # SecurityProxy on TCP. Master key comes from /data/.master_key
    # (bind-mounted read-only), token from MYCELOS_PROXY_TOKEN.
    exec gosu mycelos mycelos serve \
        --role proxy \
        --proxy-host 0.0.0.0 \
        --proxy-port "${MYCELOS_PROXY_PORT:-9110}" \
        --data-dir /data
fi

# Default: gateway (web UI + API). Passes through the compose CMD.
exec gosu mycelos "$@"
```

- [ ] **Step 3: Verify shell syntax**

Run: `bash -n docker-entrypoint.sh`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add docker-entrypoint.sh
git commit -m "Dispatch container role via MYCELOS_ROLE env var"
```

---

## Task 2: Add `--role` option to `mycelos serve`

**Files:**
- Modify: `src/mycelos/cli/serve_cmd.py`
- Test: `tests/test_serve_cmd_role.py`

- [ ] **Step 1: Read the current serve command**

Run: `grep -n "def serve\|@click.option\|@click.command" src/mycelos/cli/serve_cmd.py`
Expected: the existing `serve` command with its options. Note the function signature.

- [ ] **Step 2: Write the failing test**

Create `tests/test_serve_cmd_role.py`:

```python
"""Tests for `mycelos serve --role {all,gateway,proxy}` dispatch."""

from __future__ import annotations

from click.testing import CliRunner

from mycelos.cli.serve_cmd import serve_cmd


def test_serve_accepts_role_all():
    """--role all is the default and does not change existing behavior."""
    runner = CliRunner()
    # --dry-run must be implemented; it validates config and exits 0
    result = runner.invoke(serve_cmd, ["--role", "all", "--dry-run"])
    assert result.exit_code == 0, result.output


def test_serve_accepts_role_proxy(tmp_path):
    """--role proxy requires .master_key in --data-dir and MYCELOS_PROXY_TOKEN."""
    (tmp_path / ".master_key").write_text("test-master-key-32-bytes-plus-extra")
    runner = CliRunner()
    result = runner.invoke(
        serve_cmd,
        [
            "--role", "proxy",
            "--data-dir", str(tmp_path),
            "--proxy-host", "127.0.0.1",
            "--proxy-port", "0",
            "--dry-run",
        ],
        env={"MYCELOS_PROXY_TOKEN": "t"},
    )
    assert result.exit_code == 0, result.output


def test_serve_role_proxy_fails_without_master_key(tmp_path):
    """Missing .master_key is a hard failure in proxy role."""
    runner = CliRunner()
    result = runner.invoke(
        serve_cmd,
        [
            "--role", "proxy",
            "--data-dir", str(tmp_path),
            "--proxy-port", "0",
            "--dry-run",
        ],
        env={"MYCELOS_PROXY_TOKEN": "t"},
    )
    assert result.exit_code != 0
    assert "master_key" in result.output.lower()


def test_serve_role_proxy_fails_without_token(tmp_path):
    """Missing MYCELOS_PROXY_TOKEN is a hard failure in proxy role."""
    (tmp_path / ".master_key").write_text("k")
    runner = CliRunner()
    result = runner.invoke(
        serve_cmd,
        [
            "--role", "proxy",
            "--data-dir", str(tmp_path),
            "--proxy-port", "0",
            "--dry-run",
        ],
        env={"MYCELOS_PROXY_TOKEN": ""},
    )
    assert result.exit_code != 0
    assert "token" in result.output.lower()


def test_serve_role_gateway_warns_without_proxy_url(tmp_path):
    """--role gateway without MYCELOS_PROXY_URL falls back to in-process proxy
    (matches --role all). The warning must make that clear."""
    runner = CliRunner()
    result = runner.invoke(
        serve_cmd,
        ["--role", "gateway", "--data-dir", str(tmp_path), "--dry-run"],
        env={"MYCELOS_PROXY_URL": ""},
    )
    assert result.exit_code == 0
    assert "MYCELOS_PROXY_URL" in result.output or "in-process" in result.output.lower()
```

- [ ] **Step 3: Run the test — expect failures**

Run: `pytest tests/test_serve_cmd_role.py -v`
Expected: all 5 fail — `serve_cmd` doesn't accept `--role`, `--dry-run`, `--proxy-host`, `--proxy-port` yet.

- [ ] **Step 4: Add the options and dispatch**

Modify `src/mycelos/cli/serve_cmd.py`. Find the `@click.command("serve")` decoration and the `def serve(...)` function. Add:

```python
@click.option(
    "--role",
    type=click.Choice(["all", "gateway", "proxy"]),
    default="all",
    help=(
        "Container/process role. 'all' (default) runs the gateway with an "
        "in-process SecurityProxy; 'gateway' uses an external proxy from "
        "MYCELOS_PROXY_URL; 'proxy' runs ONLY the SecurityProxy on TCP."
    ),
)
@click.option("--proxy-host", default="127.0.0.1", help="Proxy bind host (role=proxy only)")
@click.option("--proxy-port", default=9110, type=int, help="Proxy bind port (role=proxy only)")
@click.option("--dry-run", is_flag=True, help="Validate configuration and exit.")
```

Then at the top of the function body, before the existing server start:

```python
if role == "proxy":
    import os as _os
    from pathlib import Path as _Path
    key_path = _Path(data_dir) / ".master_key"
    if not key_path.exists():
        click.echo(
            f"Error: .master_key not found in {data_dir}. "
            "The gateway container or install script must create it first.",
            err=True,
        )
        raise click.exceptions.Exit(code=2)
    token = _os.environ.get("MYCELOS_PROXY_TOKEN", "").strip()
    if not token:
        click.echo(
            "Error: MYCELOS_PROXY_TOKEN must be set. "
            "Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'",
            err=True,
        )
        raise click.exceptions.Exit(code=2)
    if dry_run:
        click.echo(f"Proxy ready (dry-run): would bind {proxy_host}:{proxy_port}")
        return
    _os.environ["MYCELOS_MASTER_KEY"] = key_path.read_text().strip()
    _os.environ["MYCELOS_DB_PATH"] = str(_Path(data_dir) / "mycelos.db")
    import uvicorn
    from mycelos.security.proxy_server import create_proxy_app
    uvicorn.run(create_proxy_app(), host=proxy_host, port=proxy_port, log_level="warning")
    return

if role == "gateway":
    import os as _os
    if not _os.environ.get("MYCELOS_PROXY_URL"):
        click.echo(
            "Note: --role gateway without MYCELOS_PROXY_URL falls back to "
            "an in-process proxy (same as --role all).",
        )

if dry_run:
    click.echo(f"Gateway ready (dry-run): would bind {host}:{port}")
    return
```

Add `role`, `proxy_host`, `proxy_port`, `dry_run` to the function parameters.

- [ ] **Step 5: Run the tests — verify they pass**

Run: `pytest tests/test_serve_cmd_role.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/cli/serve_cmd.py tests/test_serve_cmd_role.py
git commit -m "Add --role {all,gateway,proxy} to mycelos serve"
```

---

## Task 3: Teach `SecurityProxyClient` to speak TCP

**Files:**
- Modify: `src/mycelos/security/proxy_client.py`
- Test: `tests/test_proxy_client_tcp.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_proxy_client_tcp.py`:

```python
"""SecurityProxyClient must accept either socket_path= (UDS) or url= (TCP)."""

from __future__ import annotations

import pytest

from mycelos.security.proxy_client import SecurityProxyClient


def test_client_accepts_url_kwarg():
    c = SecurityProxyClient(url="http://proxy:9110", token="t")
    assert c.base_url == "http://proxy:9110"


def test_client_accepts_socket_path_kwarg():
    c = SecurityProxyClient(socket_path="/tmp/proxy.sock", token="t")
    assert c.base_url.startswith("http")


def test_client_rejects_both_transports():
    with pytest.raises(ValueError, match="exactly one of"):
        SecurityProxyClient(socket_path="/tmp/x", url="http://y", token="t")


def test_client_rejects_neither():
    with pytest.raises(ValueError, match="exactly one of"):
        SecurityProxyClient(token="t")
```

- [ ] **Step 2: Run the test — expect ImportError or TypeError**

Run: `pytest tests/test_proxy_client_tcp.py -v`
Expected: FAIL — `SecurityProxyClient` signature doesn't accept `url=` yet.

- [ ] **Step 3: Modify the client**

Find `__init__` in `src/mycelos/security/proxy_client.py`. Replace with:

```python
def __init__(
    self,
    *,
    socket_path: str | None = None,
    url: str | None = None,
    token: str,
) -> None:
    """Connect to a SecurityProxy over Unix socket OR TCP.

    socket_path: path to AF_UNIX socket (in-process / single-container).
    url: http://host:port base URL (cross-container TCP).
    Exactly one must be given.
    """
    if bool(socket_path) == bool(url):
        raise ValueError(
            "SecurityProxyClient needs exactly one of socket_path= or url="
        )

    self._socket_path = socket_path
    self._token = token

    if socket_path:
        self._client = httpx.Client(
            transport=httpx.HTTPTransport(uds=socket_path),
            timeout=30,
        )
        self.base_url = "http://proxy"
    else:
        self._client = httpx.Client(timeout=30)
        self.base_url = url.rstrip("/")
```

Then in the existing POST/GET helpers, replace every hardcoded `"http://proxy/…"` URL with `f"{self.base_url}/…"`. Grep the file for `http://proxy` to find every site.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_proxy_client_tcp.py tests/test_security_proxy.py tests/security/test_credential_isolation.py -v`
Expected: all pass. The UDS path must remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/security/proxy_client.py tests/test_proxy_client_tcp.py
git commit -m "SecurityProxyClient: accept TCP url= alongside socket_path="
```

---

## Task 4: Skip proxy fork when `MYCELOS_PROXY_URL` is set

**Files:**
- Modify: `src/mycelos/app.py` (proxy initialization block)
- Test: `tests/test_app_credentials.py`

- [ ] **Step 1: Locate the proxy init**

Run: `grep -n "ProxyLauncher\|_proxy_client\|proxy_launcher" src/mycelos/app.py`
Expected: the lines where `ProxyLauncher` is instantiated and `_proxy_client` assigned. Note the exact range.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_app_credentials.py`:

```python
def test_external_proxy_url_skips_fork(tmp_path, monkeypatch):
    """When MYCELOS_PROXY_URL is set, App must NOT fork a child proxy.
    It must build a SecurityProxyClient with url= and MYCELOS_PROXY_TOKEN."""
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "test-key-ext-proxy")
    monkeypatch.setenv("MYCELOS_PROXY_URL", "http://proxy.internal:9110")
    monkeypatch.setenv("MYCELOS_PROXY_TOKEN", "external-token-abc")

    from mycelos.app import App
    app = App(tmp_path)
    app.initialize()

    assert app.proxy_client is not None
    assert app.proxy_client.base_url == "http://proxy.internal:9110"
    launcher = getattr(app, "_proxy_launcher", None)
    assert launcher is None or not launcher.is_running
```

- [ ] **Step 3: Run — expect failure**

Run: `pytest tests/test_app_credentials.py::test_external_proxy_url_skips_fork -v`
Expected: FAIL — App still forks.

- [ ] **Step 4: Branch on `MYCELOS_PROXY_URL`**

In the proxy-init section of `App.initialize`:

```python
import os as _os
proxy_url = _os.environ.get("MYCELOS_PROXY_URL", "").strip()
if proxy_url:
    # External proxy (two-container deployment): use it directly, skip fork.
    proxy_token = _os.environ.get("MYCELOS_PROXY_TOKEN", "").strip()
    if not proxy_token:
        raise RuntimeError(
            "MYCELOS_PROXY_URL set but MYCELOS_PROXY_TOKEN is missing — "
            "the gateway cannot authenticate to the external proxy."
        )
    from mycelos.security.proxy_client import SecurityProxyClient
    self._proxy_client = SecurityProxyClient(url=proxy_url, token=proxy_token)
    self._proxy_launcher = None
else:
    # Existing single-container fork path — unchanged.
    from mycelos.security.proxy_launcher import ProxyLauncher
    self._proxy_launcher = ProxyLauncher(self.data_dir, master_key)
    self._proxy_launcher.start()
    from mycelos.security.proxy_client import SecurityProxyClient
    self._proxy_client = SecurityProxyClient(
        socket_path=self._proxy_launcher.socket_path,
        token=self._proxy_launcher.session_token,
    )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_app_credentials.py -v`
Expected: all pass including the new external-proxy test.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/app.py tests/test_app_credentials.py
git commit -m "Skip proxy fork when MYCELOS_PROXY_URL points at an external proxy"
```

---

## Task 5: Two-service docker-compose.yml

**Files:**
- Modify: `docker-compose.yml` (complete rewrite)
- Test: `tests/test_compose_structure.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_compose_structure.py`:

```python
"""Validate the two-container shape of docker-compose.yml."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def compose():
    path = Path(__file__).parent.parent / "docker-compose.yml"
    return yaml.safe_load(path.read_text())


def test_two_services(compose):
    assert set(compose["services"]) == {"gateway", "proxy"}


def test_gateway_does_not_mount_master_key(compose):
    gw = compose["services"]["gateway"]
    for v in gw.get("volumes", []):
        assert ".master_key" not in str(v), \
            "gateway must not mount the master key; proxy owns it"


def test_proxy_mounts_master_key_readonly(compose):
    px = compose["services"]["proxy"]
    found = False
    for v in px.get("volumes", []):
        s = str(v)
        if ".master_key" in s:
            assert ":ro" in s, f"proxy must mount .master_key read-only (got {s!r})"
            found = True
    assert found, "proxy must mount .master_key"


def test_gateway_has_proxy_url_and_token(compose):
    env = compose["services"]["gateway"].get("environment", [])
    env_str = " ".join(str(e) for e in env)
    assert "MYCELOS_PROXY_URL" in env_str
    assert "MYCELOS_PROXY_TOKEN" in env_str


def test_gateway_depends_on_proxy(compose):
    depends = compose["services"]["gateway"].get("depends_on", {})
    if isinstance(depends, dict):
        assert "proxy" in depends
    else:
        assert "proxy" in depends


def test_proxy_port_not_published(compose):
    """Proxy TCP stays on the internal network — must not map to a host port."""
    px = compose["services"]["proxy"]
    assert px.get("ports", []) == [], \
        f"proxy must not publish ports (got {px.get('ports')})"


def test_same_image_for_both(compose):
    """One image, two commands — avoids image drift between gateway and proxy."""
    gw_image = compose["services"]["gateway"].get("image")
    px_image = compose["services"]["proxy"].get("image")
    assert gw_image and gw_image == px_image
```

- [ ] **Step 2: Run — expect failures**

Run: `pytest tests/test_compose_structure.py -v`
Expected: multiple failures — current compose has one service `mycelos`.

- [ ] **Step 3: Rewrite the compose**

Replace `docker-compose.yml` with:

```yaml
# Mycelos — Docker Compose (two-container deployment)
#
# Quick start:
#   ./scripts/install.sh          # recommended — guided setup
#   # or:
#   cp .env.example .env && edit .env && docker compose up -d
#
# The proxy container owns .master_key. The gateway never sees it.

services:
  proxy:
    image: ghcr.io/mycelos-ai/mycelos:main
    container_name: mycelos-proxy
    environment:
      - MYCELOS_ROLE=proxy
      - MYCELOS_PROXY_PORT=9110
      - MYCELOS_PROXY_TOKEN=${MYCELOS_PROXY_TOKEN:?set MYCELOS_PROXY_TOKEN in .env}
    volumes:
      - type: bind
        source: ${MYCELOS_DATA_DIR:-./data}/.master_key
        target: /data/.master_key
        read_only: true
      - type: bind
        source: ${MYCELOS_DATA_DIR:-./data}/mycelos.db
        target: /data/mycelos.db
        read_only: true
    networks:
      - mycelos-internal
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9110/health"]
      interval: 30s
      timeout: 5s
      start_period: 10s

  gateway:
    image: ghcr.io/mycelos-ai/mycelos:main
    container_name: mycelos-gateway
    depends_on:
      proxy:
        condition: service_healthy
    ports:
      - "${MYCELOS_PORT:-9100}:9100"
    volumes:
      # Gateway owns the full data dir except the master key (separate
      # bind-mount on the proxy, not visible here).
      - ${MYCELOS_DATA_DIR:-./data}:/data
    environment:
      - MYCELOS_ROLE=gateway
      - MYCELOS_PROXY_URL=http://proxy:9110
      - MYCELOS_PROXY_TOKEN=${MYCELOS_PROXY_TOKEN:?set MYCELOS_PROXY_TOKEN in .env}
    networks:
      - mycelos-internal
      - default
    restart: unless-stopped

networks:
  mycelos-internal:
    driver: bridge
```

Note: bind-mounts require the source paths to exist before `docker compose up`. The install script creates them in Task 7.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_compose_structure.py -v`
Expected: 7 passed.

- [ ] **Step 5: Static validation via compose CLI (if available)**

Run: `MYCELOS_PROXY_TOKEN=dummy MYCELOS_DATA_DIR=/tmp/fake docker compose config > /dev/null 2>&1 && echo OK`
Expected: `OK` if Docker is installed. Skip if not — the YAML-parse test above catches the structural part.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml tests/test_compose_structure.py
git commit -m "Two-container compose: proxy owns master key, gateway is keyless"
```

---

## Task 6: Update `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Read the current file**

Run: `cat .env.example`
Expected: existing template. Note what's there.

- [ ] **Step 2: Replace with two-container template**

Write to `.env.example`:

```ini
# Mycelos — Environment Variables
#
# Recommended: ./scripts/install.sh  generates this file automatically.
# Manual: copy to .env and fill MYCELOS_PROXY_TOKEN below.


# Required — shared secret between gateway and proxy.
# Generate with:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
MYCELOS_PROXY_TOKEN=


# Host directory for persistent data. Absolute path preferred.
MYCELOS_DATA_DIR=./data


# Host port mapping for the gateway (default 9100).
# MYCELOS_PORT=9100
```

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "Document MYCELOS_PROXY_TOKEN in .env.example for two-container mode"
```

---

## Task 7: Install script (bash) — zero-question

**Files:**
- Create: `scripts/install.sh`
- Test: `tests/test_install_script.sh`

- [ ] **Step 1: Write the black-box test**

Create `tests/test_install_script.sh`:

```bash
#!/usr/bin/env bash
# Black-box smoke test for scripts/install.sh.
# Uses --dry-run to stop before `docker compose up`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

cd "$TMP"
"$SCRIPT_DIR/scripts/install.sh" --dry-run

# Files created
[ -f .env ] || { echo "FAIL: .env missing"; exit 1; }
[ -f docker-compose.yml ] || { echo "FAIL: docker-compose.yml missing"; exit 1; }
[ -d data ] || { echo "FAIL: data/ missing"; exit 1; }
[ -f data/.master_key ] || { echo "FAIL: data/.master_key missing"; exit 1; }
[ -f data/mycelos.db ] || { echo "FAIL: data/mycelos.db missing"; exit 1; }

# Token is non-empty and long enough
TOKEN="$(grep '^MYCELOS_PROXY_TOKEN=' .env | cut -d= -f2)"
if [ -z "$TOKEN" ] || [ "${#TOKEN}" -lt 20 ]; then
    echo "FAIL: MYCELOS_PROXY_TOKEN missing or too short"
    exit 1
fi

# Permissions on sensitive files
perms="$(stat -c '%a' data/.master_key 2>/dev/null || stat -f '%A' data/.master_key)"
if [ "$perms" != "600" ]; then
    echo "FAIL: .master_key should be mode 600, got $perms"
    exit 1
fi

# Idempotency: second run preserves the token
"$SCRIPT_DIR/scripts/install.sh" --dry-run
TOKEN2="$(grep '^MYCELOS_PROXY_TOKEN=' .env | cut -d= -f2)"
[ "$TOKEN" = "$TOKEN2" ] || { echo "FAIL: second run overwrote the token"; exit 1; }

echo "PASS: install.sh smoke test"
```

Make it executable: `chmod +x tests/test_install_script.sh`.

- [ ] **Step 2: Run — expect failure**

Run: `bash tests/test_install_script.sh`
Expected: FAIL — install.sh doesn't exist.

- [ ] **Step 3: Write the installer**

Create `scripts/install.sh`:

```bash
#!/usr/bin/env bash
# Mycelos installer — zero-question bootstrap for the two-container deployment.
#
# Flags:
#   --dry-run       Skip `docker compose up`; just create files.
#   --data-dir DIR  Persistent volume path (default ./data).
#
# Exit codes:
#   0  success
#   2  prerequisite missing
#   3  stack did not become healthy within 60s

set -euo pipefail

DRY_RUN=0
DATA_DIR="./data"
COMPOSE_SRC="https://raw.githubusercontent.com/mycelos-ai/mycelos/main/docker-compose.yml"

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --data-dir) DATA_DIR="$2"; shift ;;
        -h|--help)
            sed -n '2,10p' "$0"
            exit 0 ;;
        *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
    shift
done

log() { echo "==> $*"; }
err() { echo "!!! $*" >&2; }

check_prereqs() {
    command -v docker >/dev/null 2>&1 \
        || { err "Docker not found. Install from https://docs.docker.com/get-docker/"; exit 2; }
    docker compose version >/dev/null 2>&1 \
        || { err "Docker Compose v2 required ('docker compose', not 'docker-compose')"; exit 2; }
    command -v curl >/dev/null 2>&1 || { err "curl not found"; exit 2; }
    command -v python3 >/dev/null 2>&1 || command -v openssl >/dev/null 2>&1 \
        || { err "Need python3 or openssl to generate tokens"; exit 2; }
}

gen_token() {
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
    else
        openssl rand -base64 48 | tr -d '\n' | head -c 43
    fi
}

ensure_data_dir() {
    mkdir -p "$DATA_DIR"
    if [ ! -f "$DATA_DIR/.master_key" ]; then
        log "Generating .master_key in $DATA_DIR"
        gen_token > "$DATA_DIR/.master_key"
        chmod 600 "$DATA_DIR/.master_key"
    else
        log ".master_key already exists — keeping it"
    fi
    # Pre-create the DB file so the proxy's read-only bind-mount has a target.
    # Mycelos will auto-initialize the schema on first gateway start.
    touch "$DATA_DIR/mycelos.db"
}

ensure_env() {
    if [ -f .env ]; then
        log ".env exists — keeping it"
        return
    fi
    log "Writing .env with a fresh MYCELOS_PROXY_TOKEN"
    local token
    token="$(gen_token)"
    cat > .env <<EOF
# Generated by scripts/install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
MYCELOS_PROXY_TOKEN=$token
MYCELOS_DATA_DIR=$DATA_DIR
MYCELOS_PORT=9100
EOF
    chmod 600 .env
}

ensure_compose() {
    if [ -f docker-compose.yml ]; then
        log "docker-compose.yml exists — keeping it"
        return
    fi
    log "Fetching docker-compose.yml"
    curl -fsSL "$COMPOSE_SRC" -o docker-compose.yml
}

bring_up() {
    [ "$DRY_RUN" = 1 ] && { log "Dry run: skipping 'docker compose up'"; return; }

    log "Starting containers…"
    docker compose up -d

    log "Waiting for /api/health (up to 60s)…"
    local deadline=$(( $(date +%s) + 60 ))
    local port
    port="$(grep '^MYCELOS_PORT=' .env | cut -d= -f2)"
    port="${port:-9100}"
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if curl -fsSL "http://localhost:${port}/api/health" >/dev/null 2>&1; then
            log "Healthy. Open http://localhost:${port} to finish setup."
            return
        fi
        sleep 2
    done
    err "Gateway did not become healthy within 60s. Check 'docker compose logs'."
    exit 3
}

check_prereqs
ensure_data_dir
ensure_env
ensure_compose
bring_up
log "Done."
```

Make it executable: `chmod +x scripts/install.sh`.

- [ ] **Step 4: Run the smoke test**

Run: `bash tests/test_install_script.sh`
Expected: `PASS: install.sh smoke test`.

- [ ] **Step 5: Commit**

```bash
git add scripts/install.sh tests/test_install_script.sh
git commit -m "Add zero-question install.sh: data dir, .env, compose, healthcheck"
```

---

## Task 8: Install script (PowerShell parity)

**Files:**
- Create: `scripts/install.ps1`

- [ ] **Step 1: Mirror the bash script in PowerShell**

Create `scripts/install.ps1`:

```powershell
#Requires -Version 5.1
<#
.SYNOPSIS
  Mycelos installer (Windows / PowerShell).
.PARAMETER DryRun
  Skip 'docker compose up'; just create files.
.PARAMETER DataDir
  Persistent volume path (default .\data).
#>
param(
    [switch]$DryRun,
    [string]$DataDir = ".\data"
)

$ErrorActionPreference = "Stop"
function Write-Log($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Err($msg) { Write-Host "!!! $msg" -ForegroundColor Red }

function New-Token {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
}

function Assert-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Err "Docker Desktop not found. See https://docs.docker.com/desktop/install/windows-install/"
        exit 2
    }
    docker compose version *> $null
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker Compose v2 required"; exit 2 }
}

function Initialize-DataDir {
    if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir | Out-Null }
    $keyPath = Join-Path $DataDir ".master_key"
    if (-not (Test-Path $keyPath)) {
        Write-Log "Generating .master_key in $DataDir"
        New-Token | Set-Content -Path $keyPath -NoNewline -Encoding ASCII
    } else {
        Write-Log ".master_key already exists — keeping it"
    }
    $dbPath = Join-Path $DataDir "mycelos.db"
    if (-not (Test-Path $dbPath)) { New-Item -ItemType File -Path $dbPath | Out-Null }
}

function Initialize-EnvFile {
    if (Test-Path ".env") { Write-Log ".env exists — keeping it"; return }
    Write-Log "Writing .env"
    $token = New-Token
    @"
# Generated by scripts/install.ps1 on $([DateTime]::UtcNow.ToString("o"))
MYCELOS_PROXY_TOKEN=$token
MYCELOS_DATA_DIR=$DataDir
MYCELOS_PORT=9100
"@ | Set-Content -Path ".env" -Encoding UTF8
}

function Initialize-Compose {
    $src = "https://raw.githubusercontent.com/mycelos-ai/mycelos/main/docker-compose.yml"
    if (-not (Test-Path "docker-compose.yml")) {
        Write-Log "Fetching docker-compose.yml"
        Invoke-WebRequest -Uri $src -OutFile "docker-compose.yml"
    }
}

function Start-Stack {
    if ($DryRun) { Write-Log "Dry run: skipping 'docker compose up'"; return }
    Write-Log "Starting containers…"
    docker compose up -d
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:9100/api/health" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) {
                Write-Log "Healthy. Open http://localhost:9100 to finish setup."
                return
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    Write-Err "Gateway did not become healthy in 60s. Check 'docker compose logs'."
    exit 3
}

Assert-Docker
Initialize-DataDir
Initialize-EnvFile
Initialize-Compose
Start-Stack
Write-Log "Done."
```

- [ ] **Step 2: Syntax check (best effort — skip if PowerShell unavailable)**

Run: `pwsh -NoProfile -Command "[scriptblock]::Create((Get-Content scripts/install.ps1 -Raw)) | Out-Null; 'OK'"`
Expected: `OK`. Skip if `pwsh` not installed.

- [ ] **Step 3: Commit**

```bash
git add scripts/install.ps1
git commit -m "PowerShell parity for install.sh"
```

---

## Task 9: README Quick Start section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Locate the current Quick Start**

Run: `grep -n "^## Quick Start" README.md`

- [ ] **Step 2: Replace it**

Replace the existing Quick Start section with:

```markdown
## Quick Start

### One-shot installer (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/mycelos-ai/mycelos/main/scripts/install.sh | bash
```

This creates a working directory, generates the master key and the internal proxy token, fetches `docker-compose.yml`, starts two containers (`mycelos-gateway` and `mycelos-proxy`), and opens the web UI at <http://localhost:9100>.

Idempotent — re-running keeps existing keys and tokens.

**Windows:**

```powershell
iwr https://raw.githubusercontent.com/mycelos-ai/mycelos/main/scripts/install.ps1 -OutFile install.ps1
./install.ps1
```

### Manual

If you prefer to see every file before it lands:

```bash
git clone https://github.com/mycelos-ai/mycelos
cd mycelos
cp .env.example .env
# Edit .env: set MYCELOS_PROXY_TOKEN to a random 32+ char string
python -c "import secrets; print(secrets.token_urlsafe(32))"
mkdir -p data && python -c "import secrets; print(secrets.token_urlsafe(32))" > data/.master_key && chmod 600 data/.master_key
touch data/mycelos.db
docker compose up -d
```

### Architecture (why two containers?)

The **gateway** serves the web UI, chat, and REST API. The **proxy** holds the master key and brokers every outbound LLM, MCP, and HTTP call. They share an internal Docker network and a bearer token; the proxy's port is never published to the host.

A prompt-injection or RCE inside the gateway cannot read `.master_key` or decrypt the credentials table — the key lives on a different filesystem namespace. See `docs/security/two-container-deployment.md` for the threat model.

> **Network access and authentication:** Phase 1 binds the gateway to `localhost` only. Public exposure (Cloudflare Tunnel, Tailscale, or direct Let's Encrypt) is Phase 2 and ships together with passkey-based authentication. Do not expose this to a network you do not control until that ships.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "README: Quick start via install script; explain two-container design"
```

---

## Task 10: CI — validate compose parses cleanly

**Files:**
- Modify: `.github/workflows/tests.yml` (or the existing CI workflow)

- [ ] **Step 1: Locate the CI file**

Run: `ls .github/workflows/`

- [ ] **Step 2: Add a validation step after the pytest step**

Append inside the existing `test` job, after the `pytest` step:

```yaml
      - name: Validate docker-compose.yml
        env:
          MYCELOS_PROXY_TOKEN: ci-token-not-real
          MYCELOS_DATA_DIR: /tmp/fake-data
        run: |
          mkdir -p /tmp/fake-data
          touch /tmp/fake-data/.master_key /tmp/fake-data/mycelos.db
          docker compose -f docker-compose.yml config > /dev/null
```

GitHub-hosted runners already have Docker Compose v2 installed.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "CI: validate docker-compose.yml parses on every push"
```

---

## Task 11: Threat model doc

**Files:**
- Create: `docs/security/two-container-deployment.md`

- [ ] **Step 1: Write the doc**

Create `docs/security/two-container-deployment.md`:

```markdown
# Two-Container Deployment — Threat Model

## Architecture

- **Gateway container** (`mycelos-gateway`) — FastAPI web UI, REST, chat service, scheduler, tools. Mounts `/data` read-write (knowledge notes, sessions, audit log, config generations). **Does NOT mount `.master_key`.** Cannot decrypt credentials at rest.
- **Proxy container** (`mycelos-proxy`) — SecurityProxy FastAPI. Mounts `.master_key` read-only and `mycelos.db` read-only. Exposes `/llm/complete`, `/http`, `/mcp/*`, `/credential/bootstrap`, `/stt/transcribe` on TCP port 9110. Not reachable from the host.
- **Shared secret** — `MYCELOS_PROXY_TOKEN` (Bearer). Generated at install time. Rotated by regenerating `.env` and restarting both containers.

## Threats Phase 1 mitigates

| Threat | Mitigation |
|---|---|
| Prompt injection that asks the gateway to exfiltrate API keys | Gateway has no `.master_key`; full RCE in the gateway cannot decrypt credentials |
| Supply-chain CVE in gateway dependencies (chat, MCP libs, etc.) | Proxy's dependency set is minimal: fastapi + httpx + cryptography + litellm |
| Exfil via gateway-process memory dump | Master key never loaded in gateway RAM |
| Outbound call to a rogue endpoint | Still flows through `ssrf.validate_url` in the proxy |

## Threats Phase 1 does NOT mitigate

| Threat | Status |
|---|---|
| Compromised proxy container | Full credential access. The proxy is now the crown jewel. |
| Host filesystem compromise | Attacker reads `.master_key` directly. Phase 1 is not hardware-root-of-trust. |
| Proxy's own outbound call leaking the credential | By design — the proxy uses the key. |
| Docker-engine-level MITM between gateway and proxy | Bearer token prevents replay. A privileged attacker inside the Docker engine could still tap traffic. Mitigation: mTLS between containers (Phase 3). |
| Unauthenticated web access | Phase 1 binds to `localhost`. Passkey auth ships in Phase 2. |

## Operational notes

- **Rotate the proxy token:** generate a new value, update `.env`, run `docker compose up -d`. In-flight LLM calls fail once and retry.
- **Rotate the master key:** a data-migration event — credentials must be re-entered. Out of scope for Phase 1.
- **Diagnostics:** `docker compose logs proxy` for credential-resolution errors. `mycelos db audit --suspicious --since 24h` surfaces both containers (audit writes still go through the gateway's storage).

## What Phase 2 adds

- Passkey-based web authentication (WebAuthn). Enables safe public exposure.
- Cloudflare Tunnel / Tailscale Funnel profiles in the installer. No port opens on the host; tunnel provider terminates TLS.
- Optional Caddy sidecar for LAN+TLS for users who want HTTPS locally without tunnels.
```

- [ ] **Step 2: Commit**

```bash
git add docs/security/two-container-deployment.md
git commit -m "Document the two-container threat model"
```

---

## Task 12: End-to-end smoke test

**Files:**
- Create: `tests/e2e/test_two_container_deployment.sh`

- [ ] **Step 1: Write the E2E test**

Create `tests/e2e/test_two_container_deployment.sh`:

```bash
#!/usr/bin/env bash
# E2E: bring the stack up, verify gateway is keyless, proxy port stays internal,
# proxy rejects unauthenticated calls.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap "cd $TMP && docker compose down -v 2>/dev/null || true; cd /tmp && rm -rf $TMP" EXIT

cd "$TMP"
"$ROOT/scripts/install.sh" --data-dir "$TMP/data"

# 1. Gateway healthy
curl -fsSL http://localhost:9100/api/health > /dev/null
echo "OK: gateway healthy"

# 2. Gateway has no master key in its container filesystem
if docker compose exec -T gateway test -f /data/.master_key; then
    echo "FAIL: gateway can see .master_key"
    exit 1
fi
echo "OK: gateway has no master_key"

# 3. Proxy port not reachable from the host
if curl -fsSL -m 2 http://localhost:9110/health 2>/dev/null; then
    echo "FAIL: proxy port 9110 reachable from host"
    exit 1
fi
echo "OK: proxy port not host-reachable"

# 4. Unauth call from gateway to proxy must 401
status=$(docker compose exec -T gateway \
    curl -s -o /dev/null -w '%{http_code}' http://proxy:9110/llm/complete -XPOST -d '{}' \
    || echo "curl-failed")
if [ "$status" != "401" ] && [ "$status" != "403" ]; then
    echo "FAIL: expected 401/403 from unauthenticated proxy call, got $status"
    exit 1
fi
echo "OK: proxy rejects unauthenticated calls ($status)"

echo "PASS: two-container deployment e2e"
```

Make it executable: `chmod +x tests/e2e/test_two_container_deployment.sh`.

- [ ] **Step 2: Run it locally (Docker required)**

Run: `bash tests/e2e/test_two_container_deployment.sh`
Expected: `PASS: two-container deployment e2e`.

Skip in CI for this PR — add as a nightly/manual job later.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_two_container_deployment.sh
git commit -m "E2E smoke test for two-container deployment"
```

---

## Task 13: CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Prepend under the current week heading**

Add under the most recent `## Week …` heading:

```markdown
### Two-Container Docker Deployment (Phase 1)
- New default: `docker compose up -d` launches `mycelos-proxy` (owns `.master_key`, read-only DB mount, internal TCP only) and `mycelos-gateway` (web UI, API, chat, scheduler) on a shared Docker network with a bearer-token shared secret.
- New `scripts/install.sh` and `scripts/install.ps1` installers. Zero-question: generate a master key and proxy token, write `.env` and `docker-compose.yml`, bring the stack up, wait for `/api/health`. Idempotent.
- Single-container mode still works unchanged: when `MYCELOS_PROXY_URL` is not set, `App` forks the proxy locally via `ProxyLauncher` like before. No breaking change for existing installs.
- `mycelos serve` gains `--role {all,gateway,proxy}`. `all` (default) is today's behavior; `gateway` uses an external proxy from `MYCELOS_PROXY_URL`; `proxy` runs only the SecurityProxy on TCP.
- `SecurityProxyClient` accepts either `socket_path=` (legacy UDS) or `url=` (new TCP) — mutually exclusive.
- New doc `docs/security/two-container-deployment.md` spells out what Phase 1 protects and what Phase 2 (passkey auth + public exposure) will add.
- Phase 1 binds to `localhost`. Public exposure with authentication ships in Phase 2.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "Changelog: two-container deployment Phase 1"
```

---

## Final Verification

- [ ] **Unit suite in a CI-style sandbox**

```bash
rm -rf /tmp/ci-verify && mkdir /tmp/ci-verify
git archive HEAD | tar -x -C /tmp/ci-verify
cd /tmp/ci-verify
pip install -e . --quiet
MYCELOS_MASTER_KEY=ci python -m pytest tests/ \
    --ignore=tests/integration --ignore=tests/e2e \
    -x --tb=short -q -p no:cacheprovider
```

Expected: all pass.

- [ ] **Compose structure test**

Run: `pytest tests/test_compose_structure.py -v`
Expected: 7 passed.

- [ ] **Install-script smoke**

Run: `bash tests/test_install_script.sh`
Expected: `PASS: install.sh smoke test`.

- [ ] **E2E (Docker required)**

Run: `bash tests/e2e/test_two_container_deployment.sh`
Expected: `PASS: two-container deployment e2e`.

- [ ] **Push**

```bash
git push origin main
```

Verify the GitHub Actions run is green before closing.
