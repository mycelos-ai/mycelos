# Two-Container Deployment — Threat Model

Phase 1 (1a + 1b) is complete. This doc describes the steady state.

## Architecture

- **Gateway container** (`mycelos-gateway`) — FastAPI web UI, REST, chat service, scheduler, tools. Mounts `/data` read-write (knowledge notes, sessions, audit log, config generations). **Master key never loaded in this process** — in two-container mode `app.credentials` is a `DelegatingCredentialProxy` that forwards every write to the proxy via TCP+Bearer. Gateway container is on the `mycelos-internal` Docker network only; the `default` network (internet) is not attached, so every outbound HTTP call routes through the proxy or fails.
- **Proxy container** (`mycelos-proxy`) — SecurityProxy FastAPI. Mounts `.master_key` read-only and `mycelos.db` read-write (writes the encrypted `credentials` table; reads the rest). Hosts MCP subprocess children in its own PID namespace, so every MCP-tool credential is injected and used entirely inside the proxy container — the plaintext token never appears in the gateway. Exposes `/health`, `/http`, `/llm/complete`, `/mcp/{start,call,stop}`, `/credential/{bootstrap,store,delete,list,rotate}`, `/stt/transcribe` on TCP port 9110. Not reachable from the host.
- **Shared secret** — `MYCELOS_PROXY_TOKEN` (Bearer). Generated at install time. Rotated by regenerating `.env` and restarting both containers.

## Threats Phase 1 mitigates

| Threat | Mitigation |
|---|---|
| Prompt injection that asks the gateway to exfiltrate API keys | Master key is not in the gateway process. `DelegatingCredentialProxy.get_credential` raises `NotImplementedError`. |
| LLM-call-time credential leak into agent subprocess env | Proxy resolves `credential:X` placeholders; gateway sees only placeholders. |
| MCP subprocess leaks a bearer token to the gateway process tree | MCP subprocesses live in the proxy container's PID namespace, not the gateway's. |
| Gateway RCE reaching the internet with a stolen token | Gateway container has no `default` network. `curl https://example.com` fails at the network layer. Every outbound call routes through the proxy's SSRF-validated HTTP endpoint. |
| Supply-chain CVE in a gateway-only dependency (chat libs, Alpine frontend, etc.) | Gateway-only deps cannot leak credentials — the key isn't there. |
| Runtime `pip install` of attacker-named packages | Disabled in Docker mode. Audit event `package.install_blocked` fires, user is pointed at the custom-image doc. |
| Exfil via gateway-process memory dump | Master key never loaded into gateway RAM. |

## Threats Phase 1 does NOT mitigate

| Threat | Status |
|---|---|
| Compromised proxy container | Full credential access. The proxy is the crown jewel. |
| Host filesystem compromise | Attacker reads `.master_key` directly. Phase 1 is not hardware-root-of-trust. |
| Proxy's own outbound call leaking the credential | By design — the proxy uses the key. |
| Docker-engine-level MITM between gateway and proxy | Bearer token prevents replay; a privileged attacker inside the Docker engine could still tap traffic. Mitigation: mTLS between containers (Phase 3). |
| Unauthenticated web access | Phase 1 binds the gateway to `localhost` in the default installer output. Passkey auth ships in Phase 2. |
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
