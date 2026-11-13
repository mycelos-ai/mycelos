---
title: Security
description: Fail-closed security model with credential encryption, capability scoping, and a tamper-evident audit trail.
order: 7
icon: shield
---

Security in Mycelos is a primary design concern. Every component is built with a fail-closed model: when a security check errors out, the action is denied.

## Two-container deployment (Phase 1b)

The default Docker install runs **two containers** on an isolated internal network:

- **`mycelos-proxy`** holds the master key and is the only process that can decrypt credentials. The key lives in `secrets/` on the host, a directory only this container mounts. All outbound calls — LLM APIs, HTTP, MCP subprocesses, Telegram — originate here.
- **`mycelos-gateway`** serves the web UI and API. It has **no direct internet route**: its only network is `mycelos-internal`, which can reach the proxy but nothing else. It cannot decrypt credentials; calls that need a secret go through the proxy with inline credential injection.

A full threat model, including what each phase does and does not mitigate, lives in [`docs/security/two-container-deployment.md`](https://github.com/mycelos-ai/mycelos/blob/main/docs/security/two-container-deployment.md).

## Credential encryption

Credentials are encrypted at rest using AES-256-GCM with your `MYCELOS_MASTER_KEY`. The master key is generated the first time `scripts/install.sh` runs (or the proxy container starts), stored as `secrets/.master_key` (chmod 600) and mounted only into the proxy container. The gateway mounts `data/` with the encrypted table but never the key. Installs from before week 46 (2026) kept the key in `data/`; the installer and the proxy container move it on the next start.

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

A stored credential is only injected into requests to **its own service**: a fixed HTTPS host list for well-known services (OpenAI, Anthropic, Telegram, GitHub, Brave, OpenRouter, Gemini, …) or the host of the credential's own `api_base` for self-hosted servers. A request that would carry the OpenAI key to any other host is refused with `403` before it leaves the proxy.

## Web UI

- **Sanitized rendering.** Notes, chat replies and workflow results contain outside text (mail, web pages, LLM output). Markdown is rendered with marked and then cleaned with DOMPurify; script, event handlers, `javascript:` links and embedded frames are removed. If the sanitizer is missing, the text is shown escaped.
- **Content-Security-Policy.** Scripts, styles and fonts load only from the gateway (no CDN, so the UI also works without internet access); the browser may only send requests to the gateway and the MCP registry; no plugins, no foreign framing. marked, DOMPurify, Alpine.js, the precompiled Tailwind stylesheet and the Material Symbols icon font are served from the gateway with pinned versions. The pages still use inline scripts, so the policy allows `'unsafe-inline'` for now.
- **DNS-rebinding guard.** A web page can re-point its own domain to `127.0.0.1` and then talk to a local gateway as if it were the same site. Without a password the gateway therefore answers only for known host names — `localhost`, `127.0.0.1`, `[::1]`, the bind address, detected LAN names, `MYCELOS_ALLOWED_ORIGINS` — and refuses others with `421`. Add your own names (e.g. `mycelos.local`) to `MYCELOS_ALLOWED_HOSTS`. With `MYCELOS_PASSWORD` set the check is skipped, since the foreign page never holds the credentials.

## Policy engine

Per-tool access control with strict prefix matching. An agent with capability `github.read` can use `github.read.issues` and `github.read.repos`, but **not** `github.write` or `github` (bare). Prefix matching uses `tool.startswith(cap + ".")` — there is no accidental subset coverage.

## Authentication

**v0.3 ships password authentication only.** Set `MYCELOS_PASSWORD` in `.env` to enable it: browsers get a login page and a session cookie, scripts can use HTTP Basic Auth. After 10 wrong attempts from one address within five minutes, further attempts get `429` until the window has passed; failures are audited as `auth.login_failed`. By default the gateway binds to `127.0.0.1` (see `MYCELOS_BIND`), so no auth is required for a localhost-only install.

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

## Agent code

Custom agent code runs in a separate Python subprocess with a temporary working directory, a scrubbed environment (no credentials) and a timeout. This is process separation, not a container or syscall sandbox: the subprocess has the same file access as the gateway. In the Docker install the gateway container has no internet route of its own, so neither does agent code. Tool call arguments pass through the `PolicyEngine` before they reach the handler, and new agents need human confirmation and a security review before registration.
