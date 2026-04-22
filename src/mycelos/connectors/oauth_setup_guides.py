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
    "title": "Set up your Google Cloud project",
    "intro": (
        "Google requires every app that accesses Gmail / Calendar / Drive "
        "on your behalf to be registered in a Google Cloud project *you* "
        "own. This is a one-time, ~10-minute setup. After it's done, all "
        "three Mycelos Google connectors can share the same project."
    ),
    "steps": [
        {
            "title": "Create or pick a Google Cloud project",
            "body": (
                "Open the Google Cloud Console. Create a new project "
                "(name it anything — e.g. 'Mycelos') or pick one you "
                "already have. Projects are free and never charged unless "
                "you explicitly enable billing."
            ),
            "cta_url": "https://console.cloud.google.com/projectcreate",
            "cta_label": "Open Cloud Console",
        },
        {
            "title": "Enable the APIs you want to use",
            "body": (
                "For each Google service you plan to connect, enable its "
                "API in your project: Gmail API, Google Calendar API, "
                "Google Drive API. Enabling all three now is fine — they "
                "share the project and you can disable them later."
            ),
            "cta_url": "https://console.cloud.google.com/apis/library",
            "cta_label": "Open API Library",
        },
        {
            "title": "Configure the OAuth consent screen",
            "body": (
                "Go to **APIs & Services → OAuth consent screen**. Pick "
                "**External** user type. Fill in an app name (e.g. "
                "'Mycelos'), your email as support contact, and your "
                "email as developer contact. Save and continue. You can "
                "skip the scopes page. On the 'Test users' page, add "
                "*your own Google account* — the account whose Gmail / "
                "Calendar / Drive you want to connect — and save. "
                "Leaving the app in 'Testing' is fine; you do NOT need "
                "to publish it."
            ),
            "cta_url": "https://console.cloud.google.com/apis/credentials/consent",
            "cta_label": "Open Consent Screen",
        },
        {
            "title": "Create an OAuth Desktop-app credential",
            "body": (
                "Go to **APIs & Services → Credentials → Create "
                "credentials → OAuth client ID**. **Application type: "
                "Desktop app** (important — Mycelos only supports this "
                "type). Name it anything. Click Create. A dialog pops up "
                "with a Client ID and Client secret — click "
                "**DOWNLOAD JSON**. Save the file as `gcp-oauth.keys.json`."
            ),
            "cta_url": "https://console.cloud.google.com/apis/credentials",
            "cta_label": "Open Credentials",
        },
        {
            "title": "Upload the keys to Mycelos",
            "body": (
                "Come back to this dialog. In the next step, upload the "
                "`gcp-oauth.keys.json` file you just downloaded. Mycelos "
                "stores it encrypted inside the proxy container — the "
                "gateway and the LLM never see the contents."
            ),
        },
        {
            "title": "Complete the browser consent",
            "body": (
                "After upload, click **Start OAuth consent**. Mycelos "
                "launches the MCP server's one-shot auth command in the "
                "proxy. A Google consent URL appears in this dialog — "
                "open it, sign in with the account you added as a Test "
                "user, and accept the scopes. The server writes a token "
                "file and you're done."
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
