# Two-Container Deployment — Threat Model

Phase 1 (1a + 1b) is complete. This doc describes the steady state.

## Architecture

- **Gateway container** (`mycelos-gateway`) — FastAPI web UI, REST, chat service, scheduler, tools. Mounts `/data` read-write (knowledge notes, sessions, audit log, config generations, the *encrypted* credentials table). **The master key is neither mounted nor loaded here** — it lives in the proxy-only secrets mount (see below), and with `MYCELOS_PROXY_URL` set the gateway never reads or creates a key file; in two-container mode `app.credentials` is a `DelegatingCredentialProxy` that forwards every write to the proxy via TCP+Bearer. Gateway container is on the `mycelos-internal` Docker network only; the `default` network (internet) is not attached, so every outbound HTTP call routes through the proxy or fails.
- **Proxy container** (`mycelos-proxy`) — SecurityProxy FastAPI. Mounts the full `/data` directory (reads/writes the `credentials` table, sees the SQLite WAL sibling files the gateway creates) **and** `/secrets`, the host's `MYCELOS_SECRETS_DIR` (default `./secrets`), which holds the master key (`MYCELOS_MASTER_KEY_FILE=/secrets/.master_key`). No other container mounts `/secrets`. Hosts MCP subprocess children in its own PID namespace, so every MCP-tool credential is injected and used entirely inside the proxy container — the plaintext token never appears in the gateway. Exposes `/health`, `/http`, `/llm/complete`, `/mcp/{start,call,stop}`, `/credential/{bootstrap,store,delete,list,rotate}`, `/stt/transcribe` on TCP port 9110. Not reachable from the host.
- **Shared secret** — `MYCELOS_PROXY_TOKEN` (Bearer). Generated at install time. Rotated by regenerating `.env` and restarting both containers.

### Where the master key lives

| Install | Key file | Who can read it |
|---|---|---|
| Docker, current `docker-compose.yml` | `secrets/.master_key` on the host → `/secrets/.master_key` in the proxy | proxy container only |
| Docker, compose file from before week 46 (2026) | `data/.master_key` → `/data/.master_key` in **both** containers | proxy **and gateway** — update the compose file |
| `pip install` / `mycelos serve` (single process) | `~/.mycelos/.master_key` | the one Mycelos process |

**Migration.** Older installs kept the key in `data/`, which both containers mount — the gateway could read it, and `mycelos credential export` inside the gateway printed every credential. Re-running the installer fetches the current compose file. With it, the proxy entrypoint (`prepare_proxy_key` in `docker-entrypoint.sh`) moves a key it finds in `/data` into `/secrets` on start: copy, compare, delete the old copy. `scripts/install.sh` / `install.ps1` do the same on the host. If both places hold *different* keys, nothing is touched and the proxy refuses to start — keep the key your credentials were encrypted with (normally the older one in `data/`). The entrypoint also creates the key on first start and hands it to the container user (Docker Desktop shows bind mounts as `root:root`).

**CLI.** `docker compose exec gateway mycelos credential …` talks to the proxy: `store`, `delete`, `import` and `list` (without key preview) work; `export` is refused. To export plaintext, run it in the proxy container: `docker compose exec proxy mycelos credential export`.

## Threats Phase 1 mitigates

| Threat | Mitigation |
|---|---|
| Prompt injection that asks the gateway to exfiltrate API keys | Master key is not in the gateway process. `DelegatingCredentialProxy.get_credential` raises `NotImplementedError`. |
| LLM-call-time credential leak into agent subprocess env | Proxy resolves `credential:X` placeholders; gateway sees only placeholders. |
| MCP subprocess leaks a bearer token to the gateway process tree | MCP subprocesses live in the proxy container's PID namespace, not the gateway's. |
| Prompt injection / RCE in the gateway reads the key from disk | Key file sits in the proxy-only `/secrets` mount, not in the shared `/data`. |
| Gateway asks the proxy to send a stored key to an attacker's server | `POST /http` with `inject_credential` only injects into requests to that credential's own hosts: a fixed HTTPS list for known services (OpenAI, Anthropic, Telegram, GitHub, …) or the host of the credential's own `api_base` (Ollama, custom servers). Anything else → `403`, audited as `proxy.credential_host_denied`. |
| Gateway RCE reaching the internet with a stolen token | Gateway container has no `default` network. `curl https://example.com` fails at the network layer. Every outbound call routes through the proxy's SSRF-validated HTTP endpoint. |
| Supply-chain CVE in a gateway-only dependency (chat libs, Alpine frontend, etc.) | Gateway-only deps cannot leak credentials — the key isn't there. |
| Runtime `pip install` of attacker-named packages | Disabled in Docker mode. Audit event `package.install_blocked` fires, user is pointed at the custom-image doc. |
| Exfil via gateway-process memory dump | Master key never loaded into gateway RAM. |

## Threats Phase 1 does NOT mitigate

| Threat | Status |
|---|---|
| Compromised proxy container | Full credential access. The proxy is the crown jewel. |
| Host filesystem compromise | Attacker reads `secrets/.master_key` directly. Phase 1 is not hardware-root-of-trust. |
| Gateway sends requests *without* a credential to any public host | Allowed by design (web fetch, RSS). The SSRF check still blocks private ranges. |
| Proxy's own outbound call leaking the credential | By design — the proxy uses the key. |
| Docker-engine-level MITM between gateway and proxy | Bearer token prevents replay; a privileged attacker inside the Docker engine could still tap traffic. Mitigation: mTLS between containers (Phase 3). |
| Unauthenticated web access | Phase 1 binds the gateway to `localhost` in the default installer output. Without a password the gateway answers only for known host names (DNS-rebinding guard, `MYCELOS_ALLOWED_HOSTS`). Passkey auth ships in Phase 2. |
| Gateway holding the Telegram bot token in RAM | Deliberate. See "Materializable credentials" below. |

## Materializable credentials (deliberate compromise)

Most credentials (Anthropic, OpenAI, MCP tokens, …) never leave the proxy: the gateway calls `POST /http` with `inject_credential=<name>` and `inject_as=bearer|header:X|url_path`, and the proxy substitutes the secret at the network boundary.

Telegram is the exception. aiogram's authenticated long-poll session keeps its own HTTP connection directly to `api.telegram.org` for minutes at a time, so proxying every request is structurally awkward. We chose the pragmatic path:

- The proxy exposes `POST /credential/materialize` that returns plaintext, but **only for a hard-coded allow-list** (`MATERIALIZABLE_SERVICES = {"telegram"}`).
- The endpoint is **bootstrap-window gated** (10 s after proxy start, shared with `/credential/bootstrap`). A gateway compromised later in the session cannot materialize new credentials — the proxy refuses with `403`.
- The gateway holds the resolved bot token in process RAM for the lifetime of the gateway container. It's never written to disk and never included in any outbound request except to `api.telegram.org` (enforced by the aiogram session).
- Every materialize call is audited as `proxy.credential_materialized`; refusals as `proxy.materialize_denied`.

Net effect: the master key and every other credential remain inside the proxy. Telegram is the single case where the gateway holds a derived secret; that secret is useless without Telegram's network endpoint (no pivot to other providers) and cannot be refreshed after startup.

The cleaner alternative — writing a `ProxyAiohttpSession` that tunnels every aiogram request through `/http` — was considered and postponed. It depends on aiogram internals (token format validation, retry logic) and would cost a moving-parts problem at every aiogram upgrade.

## Operational notes

- **Rotate the proxy token:** generate a new value, update `.env`, run `docker compose up -d`. In-flight LLM calls fail once and retry.
- **Back up the master key:** `secrets/.master_key`. Without it the credentials table cannot be decrypted.
- **Rotate the master key:** a data-migration event — credentials must be re-entered. Out of scope for Phase 1.
- **Diagnostics:** `docker compose logs proxy` for credential-resolution errors. `mycelos db audit --suspicious --since 24h` surfaces both containers (audit writes still go through the gateway's storage).
- **Extra Python packages:** build a custom image. See `docs/deployment/custom-image.md`.

## What Phase 2 adds

- Passkey-based web authentication (WebAuthn). Enables safe public exposure.
- Cloudflare Tunnel / Tailscale Funnel profiles in the installer. No port opens on the host; tunnel provider terminates TLS.
- Optional Caddy sidecar for LAN+TLS for users who want HTTPS locally without tunnels.

## What Phase 1c adds

- `mycelos:rich` Docker image tag with a curated set of common Python packages (Pillow, BeautifulSoup, lxml, pypdf, python-dateutil) preinstalled.
- MCP-first documentation for the most common "I need a tool that needs extra code" scenarios (image gen, browser automation, CLI wrapping).
- Proxy-mediated, validated pip download cache for advanced users who still need arbitrary packages.
