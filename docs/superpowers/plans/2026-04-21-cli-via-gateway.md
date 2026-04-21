# CLI via Gateway — move operational queries off the direct-storage path

**Status:** captured, not scheduled.
**Origin:** 2026-04-21 — `mycelos credential list` disagreed with the Settings
UI because the CLI went through `DelegatingCredentialProxy` while the UI
came from a direct gateway-side DB read. The underlying WAL-visibility
bug was fixed in `65ed1ff`, but the architectural fault — two read paths
into one truth — is still there.

## Principle

The Gateway is the single source of truth. Every CLI operation that is
not "bootstrap the stack" or "manage the stack at the host level" talks
to it over `/api/*`, the same way the web UI does.

## Today's three CLI roles

1. **Stack operations (host-side wrapper).** `update`, `restart`, `logs`,
   `shell`, `stop`. Already local docker-compose calls. No change.
2. **Bootstrap operations.** `init`, `serve`. These create or become the
   gateway. Must keep talking to App/Storage directly. No change.
3. **Operational queries.** Everything else — `credential list`,
   `config list/show/diff/rollback`, `schedule list/add/...`,
   `sessions list`, `model list`, `doctor`. These currently bypass the
   gateway and reach into storage or the proxy RPC themselves.

Group 3 is where the work is.

## Target design

A thin HTTP client in `mycelos.cli.gateway_client`:

```python
class GatewayClient:
    def __init__(self, base_url="http://127.0.0.1:9100", password=None):
        self.base = base_url
        self.auth = ("", password) if password else None

    def credentials_list(self) -> list[dict]: ...
    def config_generations(self) -> list[dict]: ...
    def config_rollback(self, gen_id: int) -> dict: ...
    def schedule_list(self) -> list[dict]: ...
    # ... one method per Group-3 command
```

Click commands collapse to shell-glue:

```python
@credential_cmd.command("list")
def list_cmd():
    creds = gateway_client().credentials_list()
    render_table(creds)
```

Configuration resolution order:

1. `--gateway-url` flag
2. `$MYCELOS_GATEWAY_URL` env var
3. `~/.mycelos/gateway.conf` (written by `scripts/install.sh` or by
   `mycelos init`)
4. Default: `http://127.0.0.1:9100`

Password resolution: `$MYCELOS_PASSWORD` or `~/.mycelos/gateway.conf`.
Empty when the gateway is bound to localhost and has no `MYCELOS_PASSWORD`.

## Fallback for the source-install dev path

When `mycelos serve` is NOT running, Group-3 commands need to degrade
gracefully. Two options:

- **Autostart:** if `--data-dir` exists locally and the gateway isn't
  reachable, fork a one-off in-process App and run the command directly.
  Keeps the dev loop tight; adds a code branch per command.
- **Hard fail:** tell the user to run `mycelos serve` first. Cleanest,
  but breaks scripts that use the CLI for smoke tests in CI-without-gw.

Recommendation: **autostart** only when `--data-dir` is passed (or when
`MYCELOS_DATA_DIR` is set and points at a real dir). Otherwise hard fail
with a pointer to `mycelos serve`.

## New API endpoints needed

Most Group-3 commands already have an API counterpart. Gaps:

- `POST /api/config/rollback/{id}` — exists
- `GET /api/schedule` — exists
- `POST /api/schedule` / `DELETE /api/schedule/{id}` — exists as slash
  commands inside the chat, not as clean REST yet
- `GET /api/doctor/full` — returns the whole `run_health_checks()` shape
- `GET /api/doctor/audit?level=suspicious&since=24h` — already there
- `GET /api/models/list` — exists
- `GET /api/sessions` — exists, needs pagination

No new schema. Some handlers need a second argument shape to cover what
the CLI currently formats.

## Migration plan

Five commits, each small enough to review:

1. **GatewayClient skeleton** — `cli/gateway_client.py` with `_get` /
   `_post` / `_delete` helpers, URL + password resolution, a 2-line
   `credentials_list()` and nothing else. Plus a pytest that mocks
   httpx and asserts the URL / auth header shape.

2. **`credential list` migrates.** Drop the `app.credentials.list_services`
   path out of `cli/credential_cmd.py`. CI: the existing integration
   test that calls the command needs to spin up a gateway first, or
   mock `GatewayClient`.

3. **`config` group migrates** (`list`, `show`, `diff`, `rollback`).

4. **`schedule list / show` + `sessions list` + `model list`.**

5. **`doctor` migrates.** The `--why` mode stays special because it
   shells out to Claude Code / Codex — keep that path; the check
   aggregation itself goes over `/api/doctor/full`.

Each commit is: change the command, update tests, run full suite, commit.

## What stays behind

- `mycelos init`: sets the key, writes schema, never touches HTTP.
- `mycelos serve`: becomes the gateway.
- `mycelos chat`: already talks to `/api/chat` (SSE).
- `mycelos update / restart / logs / shell / stop`: local docker-compose.

## Security notes

- The gateway binds to `127.0.0.1` by default in Docker. Local CLI calls
  have nothing to authenticate against — fine.
- When the user exposes the gateway with `MYCELOS_BIND=0.0.0.0` +
  `MYCELOS_PASSWORD`, the CLI picks the password up from env or the
  config file. No new secret storage.
- `GatewayClient` does not accept unencrypted HTTPS downgrades; if
  `MYCELOS_GATEWAY_URL` starts with `http://` for a non-loopback host
  the CLI should warn once.

## Non-goals

- Do not build a generic remote-admin CLI. Passkey auth ships in
  Phase 2; until then, pointing the CLI at a remote gateway with Basic
  Auth works but is out of scope as a first-class experience.
- Do not replace the Python App class with an HTTP client in tests.
  Tests keep using storage directly — they're testing behaviour, not
  integration surface.

## Trigger

Pick this up the next time we hit a CLI-vs-UI data mismatch, or when
someone wants to run `mycelos` against a remote Pi from their laptop
without going through `docker compose exec`.
