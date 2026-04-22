# Google (Gmail / Calendar / Drive) setup

Mycelos integrates with Google Workspace through three separate MCP
servers, each running inside the SecurityProxy container. This keeps
OAuth tokens out of the gateway process and out of the LLM's reach.

**Happy path (via the web UI):**

1. Open **Connectors → Google Workspace**.
2. Click Gmail / Calendar / Drive.
3. Follow the inline step-by-step guide in the dialog. It walks you
   through creating a Google Cloud project, enabling APIs, configuring
   the consent screen, and downloading `gcp-oauth.keys.json`.
4. Paste the JSON into the dialog and click **Start OAuth consent**.
5. Open the consent URL the dialog shows, sign in, accept the scopes.
6. Done. Close the dialog; the connector is live.

All three Google services share a single Google Cloud project, so
steps 1–3 only happen once. This doc is a reference — you don't need
to read it top-to-bottom to connect a Google service.

## CLI fallback

If the web UI isn't available (headless server, debugging, etc.),
the same outcome can be reached by running the setup commands
directly in the proxy container shell.

## What you'll end up with

- A Google Cloud **OAuth 2.0 Desktop-app credential** (`gcp-oauth.keys.json`)
  — one file, reused across all three services.
- Three **per-service tokens**, one for each service you enable:
  `~/.gmail-mcp/credentials.json`, `~/.google-calendar-mcp/token.json`,
  `~/.google-drive-mcp/token.json` (paths inside the proxy container).
- Three Mycelos connectors: `gmail`, `google-calendar`, `google-drive`.

You only need to do the OAuth-key step once. You do need to run the
per-service consent flow once per service.

## Step 1 — Create a Google Cloud OAuth credential

1. Open https://console.cloud.google.com/ and create (or pick) a project.
2. Go to **APIs & Services → Library** and enable:
   - Gmail API (if you want Gmail)
   - Google Calendar API (if you want Calendar)
   - Google Drive API (if you want Drive)
3. Go to **APIs & Services → OAuth consent screen**, pick **External**,
   fill in the app name / support email / developer email, and save.
   You do NOT need to publish the app — keeping it in "Testing" is fine,
   which means you'll have to add your own Google account as a "Test user"
   further down the same page.
4. Go to **APIs & Services → Credentials → Create credentials → OAuth
   client ID**, pick **Desktop app**, give it a name (e.g. "Mycelos"),
   and click Create.
5. Download the JSON. Rename it to `gcp-oauth.keys.json`.

## Step 2 — Seed the keys file in the proxy container

The Mycelos proxy bind-mounts `/data`, so anything you put in
`./data/.google/` on the host is visible to it as `/data/.google/`:

```bash
mkdir -p ./data/.google
cp ~/Downloads/gcp-oauth.keys.json ./data/.google/
```

## Step 3 — Run the one-time consent flow per service

Each service needs its own browser consent (different scopes). You run
these **inside** the proxy container, once per service:

```bash
mycelos shell proxy   # drops you into a shell in the proxy container

# --- Gmail ---
export GMAIL_OAUTH_PATH=/data/.google/gcp-oauth.keys.json
export GMAIL_CREDENTIALS_PATH=/data/.gmail-mcp/credentials.json
mkdir -p /data/.gmail-mcp
npx -y @gongrzhe/server-gmail-autoauth-mcp auth
# A URL is printed. Open it in a browser on your laptop, consent,
# paste the callback URL back when prompted. Token file gets
# written to /data/.gmail-mcp/credentials.json.

# --- Calendar ---
export GOOGLE_OAUTH_CREDENTIALS=/data/.google/gcp-oauth.keys.json
export GOOGLE_CALENDAR_TOKEN_PATH=/data/.google-calendar-mcp/token.json
mkdir -p /data/.google-calendar-mcp
npx -y @cocal/google-calendar-mcp auth

# --- Drive ---
export GDRIVE_OAUTH_PATH=/data/.google/gcp-oauth.keys.json
export GDRIVE_TOKEN_PATH=/data/.google-drive-mcp/token.json
mkdir -p /data/.google-drive-mcp
npx -y @piotr-agier/google-drive-mcp auth
```

> **Callback URL caveat:** All three packages default to
> `http://localhost:3000` as the OAuth redirect. On a home-network
> deployment (e.g. Raspberry Pi), you must run `mycelos shell proxy`
> *from a browser-capable machine with network reachability* to
> `localhost:3000` on the Pi — or configure a custom redirect URL in
> Google Cloud Console. The former is easier: SSH to the Pi with port
> forwarding (`ssh -L 3000:localhost:3000 pi-host`), then open the
> consent URL in your local browser.

After the three consents are done, exit the shell. Tokens persist
across container restarts (they live in the bind-mounted `/data`).

## Step 4 — Wire up the connectors in Mycelos

In the web UI, go to **Connectors**, find the "Google Workspace"
category, and click each service you want. In the credential form,
paste the path to the keys file (`/data/.google/gcp-oauth.keys.json`)
into the OAuth-credentials field.

That's it. The server picks up the token file Mycelos wrote in Step 3
and starts serving API calls silently.

## Troubleshooting

### "invalid_grant" on first call

The token expired or was never issued. Re-run the Step-3 `auth` command
for that service.

### Server logs "Please run `npx ... auth` first"

The server couldn't find the token file. Either the path env var is
wrong, or the one-shot `auth` didn't write the token. Re-run Step 3.

### Token works locally but not in the container

Usually caused by a host-vs-container path mismatch. The token file
must be reachable at the path the env var points to — `/data/.gmail-mcp/...`
inside the container, `./data/.gmail-mcp/...` on the host.

### "access_denied" during consent

Your Google account isn't listed as a Test user on the OAuth consent
screen. Go back to **OAuth consent screen** in Cloud Console and add
your account under "Test users".

## Security notes

- `gcp-oauth.keys.json` is a client credential pair, not a secret per
  se (it identifies your Google Cloud project, not your account). We
  still treat it as sensitive — keep it out of version control and
  off public shares.
- The per-service token files (`credentials.json`, `token.json`) are
  the real sensitive artifacts. They grant API access to your Google
  account. They live only inside the proxy container's `/data`
  bind-mount; the gateway never sees them.
- Each server's scopes are visible in the Mycelos connector card
  before you click Connect. Review them before consenting.
