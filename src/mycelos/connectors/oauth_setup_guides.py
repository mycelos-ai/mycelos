"""Step-by-step setup guides for OAuth-based MCP connectors.

Each guide is a list of rendered steps the frontend walks the user
through inside the connector setup dialog. Keeps platform-specific
instructions out of the recipe dataclass (recipes just reference a
guide by id) and makes the guides reusable across recipes that share
a setup path — all three Google MCP servers need the same Google
Cloud project, so they all point at `google_cloud`.

Step shape:
    {
        "title": "Short step label",
        "body":  "Markdown explanation shown to the user",
        "cta_url": "https://example.com"  # optional 'open this page' link
        "cta_label": "Open Cloud Console"  # optional — defaults to "Open"
    }

Add a new guide by putting it in SETUP_GUIDES keyed by its id. Keep
the id snake_case so it looks natural in JSON APIs.
"""
from __future__ import annotations

from typing import Any


GOOGLE_CLOUD_GUIDE: dict[str, Any] = {
    "id": "google_cloud",
    "title": "Set up Gmail via Google's official MCP server",
    "intro": (
        "Mycelos connects to Google's remote MCP server for Gmail. "
        "You need a Google Cloud project with two APIs enabled (Gmail API + "
        "Gmail MCP API) and an OAuth 2.0 Desktop-app credential. This is a "
        "one-time ~10-minute setup."
    ),
    "steps": [
        {
            "title": "Create or pick a Google Cloud project",
            "body": (
                "Open Google Cloud Console. Create a new project (name it "
                "e.g. 'Mycelos') or pick an existing one. Projects are free."
            ),
            "cta_url": "https://console.cloud.google.com/projectcreate",
            "cta_label": "Open Cloud Console",
        },
        {
            "title": "Enable the Gmail API",
            "body": (
                "In **APIs & Services → Library**, search for 'Gmail API' "
                "and enable it."
            ),
            "cta_url": "https://console.cloud.google.com/apis/library",
            "cta_label": "Open API Library",
        },
        {
            "title": "Enable the Gmail MCP API",
            "body": (
                "Also enable the **Gmail MCP API** (separate from the plain "
                "Gmail API — this one is in Developer Preview and lives at "
                "`gmailmcp.googleapis.com`). In the same API Library, search "
                "for 'Gmail MCP' and enable."
            ),
            "cta_url": "https://console.cloud.google.com/apis/library",
            "cta_label": "Open API Library",
        },
        {
            "title": "Configure the OAuth consent screen",
            "body": (
                "**APIs & Services → OAuth consent screen**. Pick **External**. "
                "Fill in an app name, your email as support + developer "
                "contact. Save. Add your Google account as a **Test user**. "
                "Add these scopes: `gmail.readonly`, `gmail.compose`."
            ),
            "cta_url": "https://console.cloud.google.com/apis/credentials/consent",
            "cta_label": "Open Consent Screen",
        },
        {
            "title": "Create an OAuth Desktop-app credential",
            "body": (
                "**APIs & Services → Credentials → Create credentials → "
                "OAuth client ID**. Pick **Desktop app** (important — Mycelos "
                "only supports Desktop). Give it a name. Click Create. Click "
                "**DOWNLOAD JSON** on the dialog that pops up. Save the file "
                "(typically named `client_secret_*.json`)."
            ),
            "cta_url": "https://console.cloud.google.com/apis/credentials",
            "cta_label": "Open Credentials",
        },
        {
            "title": "Register the Redirect URI",
            "body": (
                "Open the credential you just created. Under **Authorized "
                "redirect URIs**, click **Add URI** and paste the URL Mycelos "
                "will show you in the dialog after you upload the credential. "
                "This URL is derived from your Mycelos server's address and "
                "must match *exactly* (http vs https, trailing slash, port) "
                "what Mycelos sends in the auth request."
            ),
        },
        {
            "title": "Upload the client secret to Mycelos",
            "body": (
                "Come back to this dialog. In the textarea below, paste the "
                "full contents of `client_secret_*.json`. Mycelos stores the "
                "file encrypted; the gateway and the LLM never see it."
            ),
        },
        {
            "title": "Complete the consent",
            "body": (
                "Click **Start OAuth consent**. Mycelos builds a Google "
                "consent URL. Open it, sign in as the Test user you added, "
                "accept the scopes. Google redirects back to Mycelos and "
                "the dialog shows 'Connected'."
            ),
        },
    ],
}


SETUP_GUIDES: dict[str, dict[str, Any]] = {
    "google_cloud": GOOGLE_CLOUD_GUIDE,
}


def get_setup_guide(guide_id: str) -> dict[str, Any] | None:
    """Return the guide by id or None if unknown.

    Unknown ids return None rather than raising so the frontend can
    gracefully degrade to 'no guide, just show the upload form' when
    a recipe references a guide this backend doesn't know about (e.g.
    a newer recipe on an older backend version).
    """
    return SETUP_GUIDES.get(guide_id)
