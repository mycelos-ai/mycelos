# Google Tools via MCP — replace the in-process `gog` subprocess path

**Status:** captured, not scheduled.
**Origin:** 2026-04-22 gateway isolation audit, finding #3.4.
**Depends on:** 2026-04-19 Phase 1a + 2026-04-20 Phase 1b (two-container
deployment, proxy-managed MCP subprocesses).

## Why

`src/mycelos/connectors/google_tools.py` exposes Gmail, Calendar, and
Drive through an **in-process subprocess** call to the `gog` CLI. In
two-container deployment this is broken twice over:

1. `gog` is not in the gateway image's PATH — we never installed it.
2. Even if it were, `gog` opens direct OAuth-authenticated HTTPS to
   `googleapis.com`. The gateway has no direct internet route, so
   every call would fail at the network layer.

Same architectural shape as the email fix in commit `7d63b7c`:
move the subprocess into the proxy container, expose it as an MCP
server, let the gateway reach tools via `connector_call`.

## Target design

Three MCP recipes — one per Google service — so users can install
only what they need. Each is an established npm package with auto
OAuth flow:

| Recipe id | npm package | Scope | Auth |
|---|---|---|---|
| `gmail` | `@gongrzhe/server-gmail-autoauth-mcp` | Gmail (search, read, send, filters, labels, attachments) | OAuth 2.0 browser flow, token at `~/.gmail-mcp/` |
| `google-calendar` | `@cocal/google-calendar-mcp` | Calendar (list, create, update, delete events) | OAuth 2.0, shares the same `gcp-oauth.keys.json` shape |
| `google-drive` | `@piotr-agier/google-drive-mcp` | Drive (list, read, search, upload) | OAuth 2.0 |

All three use the standard Google OAuth desktop-app credentials
(`gcp-oauth.keys.json`). The flow per recipe:

1. User creates a Google Cloud OAuth desktop-app credential and
   downloads `gcp-oauth.keys.json` (we document this once — applies
   to all three recipes).
2. User pastes the JSON into the connector setup form. Mycelos
   stores it as a credential under the service name (`gmail`,
   `google-calendar`, `google-drive`) with the env-var the server
   expects (typically `GMAIL_OAUTH_PATH` / `CALENDAR_OAUTH_PATH` /
   `GDRIVE_OAUTH_PATH`).
3. First run of the MCP server triggers a browser-based OAuth
   consent. Token is stored on disk inside the proxy container
   (separate from the OAuth key so it's refresh-safe).
4. Subsequent runs use the cached token silently.

Known caveat: the OAuth browser callback needs a reachable URL. On
a Raspberry Pi in a home network that means either `localhost:3000`
(if the user is authing from the same host — they'd need to run a
one-shot `npx ... auth` with `mycelos shell`) or a custom callback
URL they configure in Google Cloud Console. This doc spells out
both paths.

## Why three recipes, not one aggregate

The `aibus-goo-mcp` package exposes all three Google services in a
single MCP server with 101 tools. Tempting as one-stop-shop but two
practical concerns:

- **Tool fan-out**: 101 tools in one server overwhelms an LLM's
  tool-selection attention. The per-service recipes cap at ~25 tools
  each and cover the 80% use case.
- **Blast radius**: a single OAuth scope set shared across Gmail /
  Calendar / Drive is an opt-in-to-everything. Three recipes let the
  user install Gmail without granting Drive scope.
- **Upstream quality**: the three separate packages we picked
  (`@gongrzhe/server-gmail-autoauth-mcp`, `@cocal/google-calendar-mcp`,
  `@piotr-agier/google-drive-mcp`) are MIT-licensed, maintained by
  known MCP contributors, and wired the same shape as every other
  MCP recipe we ship. `aibus-goo-mcp` is a single-maintainer package
  with partially Chinese-language UX strings and no version history
  beyond v1.0.2.

## Migration steps

1. **Add three MCP recipes** in `connectors/mcp_recipes.py`
   (`gmail`, `google-calendar`, `google-drive`). `credentials` entry
   carries `env_var` naming per README of each server. `static_env`
   may be needed for callback-URL overrides.

2. **Delete `connectors/google_tools.py`**. Remove its import from
   `connectors/registry.py`. Remove the five `google.*` tool
   registrations. Leave policy entries alone — they match nothing
   after registration is gone.

3. **Update the frontend recipe list** in
   `frontend/pages/connectors.html`: replace the single `gmail`
   recipe (which currently points to the gog CLI) with three entries
   grouped under a "Google" category.

4. **Help-text for the OAuth-key upload**. The connector-setup form
   already supports a `secret` field (plain string). JSON won't fit
   well in a single-line password input — the recipe's help text
   should point at `mycelos shell` + `npx ... auth` for the initial
   credential placement, then the Mycelos setup form only needs to
   know "done" (service row gets a sentinel like `"oauth_handled"`).

5. **Docs**: `docs/deployment/google-setup.md` walks through the
   OAuth-keys creation, the `mycelos shell`-based auth flow, and how
   to share one OAuth project across all three services.

6. **Tests**: integration test at
   `tests/integration/test_gmail_mcp_live.py` pattern-matches
   `test_email_mcp_live.py` (commit `a5bf41e`) — spawn the server,
   `initialize`, assert expected tool names, smoke-test one tool
   against a live account.

## Security notes

- OAuth refresh tokens live in the proxy container's bind-mounted
  data dir (`/data/.gmail-mcp/credentials.json` etc.). Never in the
  gateway.
- The OAuth key file (`gcp-oauth.keys.json`) is a client credential
  pair, not a secret per se, but we treat it as one and store it
  through the proxy's credential store.
- Each server's scopes are visible in the recipe's `help` text so
  the user sees what they're consenting to before the browser pops
  open.

## Non-goals

- Not trying to centralise OAuth across the three servers. Each
  runs its own flow. Users who install all three can still reuse
  the same OAuth key file, but we don't hide the three separate
  consent flows from them.
- Not replacing existing email functionality. `email` MCP recipe
  (`@n24q02m/better-email-mcp`) from commit `7d63b7c` already does
  Gmail via IMAP + app password. The new `gmail` recipe here is
  API-based: richer tool surface (filters, threading, labels), but
  needs the OAuth dance. Users pick whichever fits their setup.

## Trigger

Take this on when a user asks for Google Calendar or Drive support,
or when the next pass over the connector gallery would benefit from
filling the "Google" category with working recipes instead of the
broken `gmail`-via-gog entry.
