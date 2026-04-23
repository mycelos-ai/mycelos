# Google (Gmail / Calendar / Drive) setup

Mycelos walks you through the entire Google setup inside the web UI.
This doc is a reference; you don't need to read it top-to-bottom to
connect a Google service.

## Happy path (via the web UI)

1. Open **Connectors → Google Workspace**.
2. Click Gmail / Calendar / Drive.
3. Follow the inline step-by-step guide in the dialog. It covers:
   creating a Google Cloud project, enabling APIs, configuring the
   consent screen, and downloading `gcp-oauth.keys.json`.
4. Paste the JSON into the dialog and click **Start OAuth consent**.
5. Open the consent URL the dialog shows, sign in with the account
   you added as a Test user, accept the scopes.
6. Done. Close the dialog; the connector is live.

All three Google services share a single Google Cloud project, so
steps 1–3 only happen once.

## How it works under the hood

- **Keys JSON** (`gcp-oauth.keys.json`) is stored encrypted in the
  SecurityProxy's credential store (under service names like
  `gmail-oauth-keys`). The gateway and the LLM never see it.
- **Before the `npx ... auth` subprocess spawns**, the proxy
  materializes the JSON into a session-scoped tmp HOME
  (`/tmp/mycelos-oauth-<sid>/.gmail-mcp/gcp-oauth.keys.json`). That
  tmp path sits on a tmpfs mount — cleartext never hits persistent
  disk.
- **After the subprocess exits cleanly**, the proxy reads the token
  file the tool wrote (`credentials.json` for Gmail, `token.json`
  for Calendar/Drive), stores it as a second credential
  (`gmail-oauth-token` etc.), and purges the tmp dir.
- **Future MCP-server runs** do the same dance: materialize both
  credentials, spawn, purge on stop. The files exist only for as
  long as the server runs.

## Troubleshooting

### "invalid_grant" on first call

The token expired or was never issued. Delete the
`<recipe>-oauth-token` credential from Settings → Credentials and
run the OAuth consent again.

### "access_denied" during consent

Your Google account isn't listed as a Test user on the OAuth
consent screen. Go back to Cloud Console → **OAuth consent screen**
and add your account under "Test users".

### The dialog hangs on "Waiting for the auth server to print a URL"

Usually means the subprocess crashed before printing its consent
URL. Click **Show subprocess log** in the dialog to see stderr. Most
common cause: uploaded a Web-app OAuth credential instead of a
Desktop-app one. Re-create the credential as Desktop app in Cloud
Console.

## Security notes

- The master key lives only in the proxy container. The gateway
  cannot decrypt credentials on its own.
- Cleartext keys / tokens exist only for the lifetime of the
  subprocess that needs them, inside a tmpfs-backed directory
  scoped to the session id.
- Each MCP server's scopes are visible in the connector card
  before you click Connect. Review them before consenting.
