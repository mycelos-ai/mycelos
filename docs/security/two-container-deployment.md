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
