---
title: Security
description: Fail-closed security model with credential encryption, capability scoping, and a tamper-evident audit trail.
order: 7
icon: shield
---

Security in Mycelos is a primary design concern. Every component is built with a fail-closed model: when a security check errors out, the action is denied.

## Two-container deployment (Phase 1b)

The default Docker install runs **two containers** on an isolated internal network:

- **`mycelos-proxy`** holds the master key and is the only process that can decrypt credentials. It bind-mounts `.master_key` read-only. All outbound calls — LLM APIs, HTTP, MCP subprocesses, Telegram — originate here.
- **`mycelos-gateway`** serves the web UI and API. It has **no direct internet route**: its only network is `mycelos-internal`, which can reach the proxy but nothing else. It cannot decrypt credentials; calls that need a secret go through the proxy with inline credential injection.

A full threat model, including what each phase does and does not mitigate, lives in [`docs/security/two-container-deployment.md`](https://github.com/mycelos-ai/mycelos/blob/main/docs/security/two-container-deployment.md).

## Credential encryption

Credentials are encrypted at rest using AES-256-GCM with your `MYCELOS_MASTER_KEY`. The master key is generated the first time `scripts/install.sh` runs, stored as `data/.master_key` (chmod 600) and mounted only into the proxy container. It is decrypted only at the moment of use, inside the proxy process.

The gateway never sees plaintext credentials — with one documented exception: the Telegram bot token, which aiogram's long-poll session requires. That token is materialized once at startup through a narrow allow-list + bootstrap-window-gated RPC (`POST /credential/materialize`) and kept in gateway RAM for the process lifetime. See the threat model doc for the rationale.

## Credential isolation

No credential ever appears in:

- LLM prompts or context windows
- Log files or audit trails
- Error messages or tracebacks
- Agent memory or the knowledge base
- The gateway's `/api/credentials/list` response — only metadata (service, label, creation time) is returned

## SSRF protection

Every outbound HTTP call the gateway makes goes through the proxy's `POST /http` endpoint, which resolves the hostname and rejects anything pointing at loopback, private RFC 1918 ranges, link-local, or the AWS metadata endpoint (169.254.169.254). Credential injection (bearer, header, or URL-path substitution) happens inside the proxy after validation — tokens never reach the gateway.

## Policy engine

Per-tool access control with strict prefix matching. An agent with capability `github.read` can use `github.read.issues` and `github.read.repos`, but **not** `github.write` or `github` (bare). Prefix matching uses `tool.startswith(cap + ".")` — there is no accidental subset coverage.

## Authentication

**v0.3 ships HTTP Basic Auth only.** Set `MYCELOS_PASSWORD` in `.env` to enable it. By default the gateway binds to `127.0.0.1` (see `MYCELOS_BIND`), so no auth is required for a localhost-only install.

Passkey (WebAuthn) authentication arrives in Phase 2. Until then, exposing the gateway on the LAN or the public internet is **your** responsibility: set `MYCELOS_PASSWORD`, put a TLS-terminating reverse proxy (Caddy, nginx, Tailscale serve, Cloudflare tunnel) in front, and treat the service as unauthenticated behind that.

## Audit trail

Every state-mutating operation is logged to `audit_events`. The trail is append-only and tamper-evident. View it from the Settings page in the web UI, or from the CLI:

```bash
mycelos db audit                      # recent events
mycelos db audit --suspicious         # filtered to security-relevant events
mycelos db audit --since 24h          # time window
```

All credential-proxy calls log (`proxy.http_request`, `proxy.credential_materialized`, `proxy.auth_failed`, `proxy.ssrf_blocked`, …) without ever including the resolved secret.

## Disabled in Docker

Some operations that are safe from a developer shell are disabled inside the container:

- **Runtime `pip install`** — blocked with a `package.install_blocked` audit event. For extra Python packages, build a custom image: see [`docs/deployment/custom-image.md`](https://github.com/mycelos-ai/mycelos/blob/main/docs/deployment/custom-image.md).
- **Arbitrary subprocess launch** — MCP servers start through the proxy's controlled manager, with env-var substitution from encrypted credentials.

## Sandbox

Agents run in sandboxed subprocesses with limited filesystem access, no direct network, and restricted system calls. Tool call arguments pass through the `PolicyEngine` before they reach the handler.
