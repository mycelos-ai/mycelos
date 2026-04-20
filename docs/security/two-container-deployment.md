# Two-Container Deployment — Threat Model

## Phase 1a (this release) vs. Phase 1b

Phase 1 ships in two substages. **Phase 1a** is what this release contains:
the container split, the `--role` CLI dispatch, the TCP `SecurityProxyClient`,
the install script, and the compose topology. **Phase 1b** follows up with the
full credential-write RPC, gateway network lockdown, and a write-free master
key in the gateway container.

Because credential writes today still go through `EncryptedCredentialProxy` in
the gateway process, Phase 1a **still mounts the master key into the gateway
container** through `MYCELOS_MASTER_KEY` on first boot — the init path needs it
to seed the database on a fresh install. Phase 1b removes that dependency
entirely. Treat Phase 1a as "process separation done, filesystem separation
staged, network separation coming."

## Architecture

- **Gateway container** (`mycelos-gateway`) — FastAPI web UI, REST, chat service, scheduler, tools. Mounts `/data` read-write (knowledge notes, sessions, audit log, config generations).
- **Proxy container** (`mycelos-proxy`) — SecurityProxy FastAPI. Mounts `.master_key` read-only and `mycelos.db` read-only. Hosts MCP subprocess children in its own process tree, so every MCP-tool credential is injected and used entirely inside the proxy container — the plaintext token never appears in the gateway. Exposes `/llm/complete`, `/http`, `/mcp/*`, `/credential/bootstrap`, `/stt/transcribe` on TCP port 9110. Not reachable from the host.
- **Shared secret** — `MYCELOS_PROXY_TOKEN` (Bearer). Generated at install time. Rotated by regenerating `.env` and restarting both containers.

## Threats Phase 1a mitigates

| Threat | Mitigation |
|---|---|
| LLM-call-time credential leak into agent subprocess env | Proxy resolves `credential:X` placeholders; gateway never sees the real key in tool-invocation paths |
| MCP subprocess leaks a bearer token to the gateway process tree | MCP subprocesses live in the proxy container's PID namespace, not the gateway's |
| Prompt injection that asks the gateway to "print your env" to exfiltrate API keys | Running tools return credential placeholders, not real values; real values exist only in the proxy process |
| Supply-chain CVE in a gateway-only dependency (chat libs, alpine frontend, etc.) | Same CVE in the proxy's smaller dep set (fastapi + httpx + cryptography + litellm) is still the blast radius, but gateway-only deps cannot leak credentials |
| Outbound call to a rogue endpoint from the gateway's tools | `http_tools` routes through the proxy when `proxy_client` is wired; SSRF validation runs in the proxy |

## Threats Phase 1a does NOT mitigate (yet)

| Threat | Status | Resolved in |
|---|---|---|
| `EncryptedCredentialProxy` instantiated in the gateway on first boot reads the master key to seed credentials | Gateway still receives the master key during init on fresh installs | Phase 1b |
| Some gateway tools (`search_tools`, `github_tools`, `mcp_search`, Telegram polling, release-check) still do direct `httpx` outbound | Not blocked by the proxy — the gateway has its own internet route | Phase 1b |
| Compromised proxy container | Full credential access. The proxy is the crown jewel. | Not mitigated — by design |
| Host filesystem compromise | Attacker reads `.master_key` directly | Not mitigated — not a goal of Phase 1 |
| Proxy's own outbound call leaking the credential | By design — the proxy uses the key | Not mitigated |
| Docker-engine-level MITM between gateway and proxy | Bearer token prevents replay; a privileged attacker inside the Docker engine could still tap traffic | Phase 3 (mTLS between containers) |
| Unauthenticated web access | Phase 1 binds to `localhost`-only in the default installer output | Phase 2 (Passkey auth) |

## What Phase 1b adds

- **Credential-write RPC** on the proxy (`POST /credential/store`, `/delete`, `/rotate`). The gateway's `app.credentials` becomes a thin proxy-client wrapper in two-container mode. Master key leaves the gateway for good.
- **Network lockdown**: gateway container drops off the `default` Docker network and only reaches the `mycelos-internal` network. Every outbound call — connector HTTP, search tools, Telegram polling, release check — routes through `proxy_client.http_get/post` or fails.
- **E2E regression**: `docker compose exec gateway curl google.com` must return "no route to host" after Phase 1b.
- **Credential metadata split**: gateway keeps read access for non-sensitive columns (`service`, `label`, `description`, `created_at`) so the Settings UI can render the credential list without ever touching the proxy. Only the encrypted payload requires proxy RPC.

## What Phase 2 adds

- Passkey-based web authentication (WebAuthn). Enables safe public exposure.
- Cloudflare Tunnel / Tailscale Funnel profiles in the installer. No port opens on the host; tunnel provider terminates TLS.
- Optional Caddy sidecar for LAN+TLS for users who want HTTPS locally without tunnels.

## Operational notes

- **Rotate the proxy token:** generate a new value, update `.env`, run `docker compose up -d`. In-flight LLM calls fail once and retry.
- **Rotate the master key:** a data-migration event — credentials must be re-entered. Out of scope for Phase 1.
- **Diagnostics:** `docker compose logs proxy` for credential-resolution errors. `mycelos db audit --suspicious --since 24h` surfaces both containers (audit writes still go through the gateway's storage).
