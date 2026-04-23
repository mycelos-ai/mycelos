# Gmail (via Google's official MCP server) setup

Mycelos walks you through the Gmail setup inside the web UI. This
doc is a reference — you don't need to read it top-to-bottom to
connect Gmail.

## Happy path (via the web UI)

1. Open **Connectors → Google Workspace**.
2. Click **Gmail**.
3. Follow the inline step-by-step guide: create a Google Cloud
   project, enable Gmail API + Gmail MCP API, configure the consent
   screen, create an OAuth 2.0 Desktop-app credential, and download
   the `client_secret_*.json`.
4. Paste the JSON into the dialog. Mycelos shows you the exact
   Redirect URI you need to register in Cloud Console.
5. Register the Redirect URI in the same credential screen you just
   created.
6. Click **Start OAuth consent**. Open the URL Mycelos shows, sign
   in with the Test user you added, accept the scopes.
7. Google redirects you back to the connectors page with "Connected".

## How it works under the hood

- The `client_secret_*.json` blob is stored encrypted under
  `gmail-oauth-client` in the credential store.
- When you click Start, the gateway mints a random `state` (CSRF)
  and PKCE `code_verifier`/`code_challenge`, builds the Google auth
  URL, and stores the state in memory (10-minute TTL).
- Google redirects your browser to
  `<your-mycelos-origin>/api/connectors/oauth/callback?code=...&state=...`.
- Gateway validates state, forwards the code + PKCE verifier to the
  proxy, proxy exchanges them at `oauth2.googleapis.com/token` for
  an access + refresh token, stores the token blob under
  `gmail-oauth-token`.
- On every MCP tool call, the proxy lazily refreshes the access
  token if it has <60s of validity left.

## Troubleshooting

### `redirect_uri_mismatch`

The Redirect URI you registered in Cloud Console must **exactly**
match what Mycelos sends (protocol, host, port, path, no trailing
slash). The dialog shows the expected URL verbatim — copy-paste
rather than type.

### `invalid_grant` on reconnect

Google revoked the refresh token. Delete the `gmail-oauth-token`
credential from Settings → Credentials and run the consent flow
again.

### `invalid_state` in the URL after consent

Gateway process restarted while you were mid-consent. Click Gmail
again and restart the flow.

### Google shows "This app isn't verified"

Normal in Developer Preview. Click Advanced → "Go to [appname]
(unsafe)". The warning only appears because you're the developer
of an unverified app; your own account accessing your own data is
safe.

## Security notes

- Client secret, access token, and refresh token are all encrypted
  at rest with the proxy's master key. The gateway holds no
  plaintext.
- PKCE defends against authorization-code interception on the
  redirect.
- State param is single-use with 10-minute TTL — prevents CSRF.
- No `localhost:3000` callback listener, no subprocess, no tmpfs
  HOME directory — the entire flow lives in the Mycelos process.
