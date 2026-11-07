# Connector Setup UX Design

## Research Summary

**Chat-embedded forms (Slack, Intercom, Typeform):** Modern chat UIs handle structured input by rendering inline form widgets within the message stream. Slack's Block Kit uses "modal" dialogs triggered from chat actions. Intercom and Typeform embed multi-step cards inline. The key insight: the chat message acts as the trigger, but the actual data entry happens in a dedicated UI element -- never as raw text in the conversation.

**Wizard-style setup (Zapier, n8n, Make.com):** All three use a staged approach: (1) pick service, (2) authenticate (OAuth redirect or paste token), (3) configure options, (4) test connection. Each stage shows only what is relevant. n8n collapses advanced fields behind a toggle. Zapier auto-detects OAuth vs API key based on the service.

**Email auto-detection (Apple Mail, Thunderbird):** Apple Mail asks for email address first, then auto-detects IMAP/SMTP settings via DNS (MX records, autoconfig/autodiscover protocols). If auto-detection fails, it expands manual fields. Thunderbird uses Mozilla's ISPDB (a public database of provider settings). This means 90% of users never see server configuration.

**OAuth vs App Passwords:** Gmail requires OAuth2 for third-party apps (or App Passwords with 2FA enabled). Outlook supports OAuth2 via Azure AD. Yahoo uses App Passwords. For a self-hosted tool like Mycelos, App Passwords are the pragmatic path -- OAuth requires a registered application with redirect URIs.

## Design: Connector Setup Modal

### Pattern: Chat-Triggered Modal

When a user types `/connector add email` or clicks "Add Connector" in the web UI, Mycelos returns a new `ChatEvent` of type `"connector-form"` containing the form schema. The frontend renders this as a modal overlay -- credentials never appear in chat history.

### Architecture

Extend `MCPRecipe` with a `fields` list that defines what the setup form collects:

```python
credentials=[
    {"key": "email", "type": "email", "label": "Email address",
     "placeholder": "you@gmail.com",
     "help": "We auto-detect your mail server from this"},
    {"key": "password", "type": "password", "label": "App Password",
     "help": "For Gmail: myaccount.google.com/apppasswords"},
    {"key": "imap_host", "type": "text", "label": "IMAP server",
     "placeholder": "imap.gmail.com", "advanced": True},
    {"key": "smtp_host", "type": "text", "label": "SMTP server",
     "placeholder": "smtp.gmail.com", "advanced": True},
]
```

Fields marked `advanced: True` are hidden by default and auto-populated from provider detection.

### Connector Complexity Tiers

**Tier 1 -- Single Token** (Telegram, Brave Search, GitHub): Modal shows connector name, one password field, a help link, and a "Test + Save" button. No steps needed.

**Tier 2 -- Multi-Field with Auto-Detect** (Email IMAP/SMTP): Modal has two stages. Stage 1 asks for email address only. On blur/submit, the system resolves the provider (gmail.com, outlook.com, yahoo.com, icloud.com via domain mapping; fallback to MX record lookup). Stage 2 pre-fills server settings and asks for the app password. Advanced fields (host, port, TLS) are collapsed.

**Tier 3 -- OAuth Flow** (Google Drive, future): Modal shows an "Authorize with Google" button that opens an OAuth popup. Token is stored server-side. No manual fields.

### Web UI Modal Wireframe

```
+--------------------------------------------------+
|  [x]                                             |
|                                                  |
|  [email icon]  Set Up Email                      |
|  Read and send email from Mycelos                |
|                                                  |
|  ----------------------------------------        |
|                                                  |
|  Email address                                   |
|  +--------------------------------------------+  |
|  | you@gmail.com                              |  |
|  +--------------------------------------------+  |
|  We detect your mail server automatically        |
|                                                  |
|  [Stage 2 -- appears after email entered]        |
|                                                  |
|  Detected: Gmail                    [change]     |
|                                                  |
|  App Password                                    |
|  +--------------------------------------------+  |
|  | ********                                   |  |
|  +--------------------------------------------+  |
|  Create one at myaccount.google.com/apppasswords |
|                                                  |
|  > Show advanced settings                        |
|    IMAP server: [imap.gmail.com       ]          |
|    SMTP server: [smtp.gmail.com       ]          |
|    IMAP port:   [993] TLS [x]                    |
|    SMTP port:   [587] TLS [x]                    |
|                                                  |
|            [Cancel]    [Test + Save]             |
|                                                  |
+--------------------------------------------------+
```

### Validation Rules

- Email address: standard email regex, validated on blur
- App Password: non-empty, minimum 8 characters
- IMAP/SMTP host: valid hostname format
- Port: numeric, 1-65535
- "Test + Save" performs a live IMAP login attempt before storing credentials

### CLI Equivalent

For terminal users, the same flow runs as interactive prompts:

```
$ mycelos connector add email
Email address: stefan@gmail.com
Detected: Gmail (imap.gmail.com / smtp.gmail.com)
App Password: ********
Testing connection... OK (4 messages in inbox)
Connector "email" saved.
```

### Implementation: New ChatEvent Type

```python
def connector_form_event(connector_id: str, fields: list[dict]) -> ChatEvent:
    return ChatEvent(type="connector-form", data={
        "connector_id": connector_id,
        "fields": fields,
    })
```

The frontend renders this as a modal. On submit, it POSTs to `/api/connector/setup` with the filled fields -- the values never enter the chat message stream, preserving credential security.

### Provider Detection Map

Hardcoded for common providers (no network call needed):

| Domain pattern        | IMAP host           | SMTP host           |
|-----------------------|---------------------|---------------------|
| gmail.com             | imap.gmail.com:993  | smtp.gmail.com:587  |
| outlook.com/hotmail.* | outlook.office365.com:993 | smtp.office365.com:587 |
| yahoo.com/ymail.com   | imap.mail.yahoo.com:993 | smtp.mail.yahoo.com:465 |
| icloud.com/me.com     | imap.mail.me.com:993 | smtp.mail.me.com:587 |
| Fallback              | MX record lookup    | MX record lookup    |

### Key Design Decisions

1. **Modal, not inline** -- credentials must never appear in chat history or be sent as chat messages
2. **Email-first for email setup** -- one field unlocks auto-detection, reducing a 6-field form to 2 fields for 90% of users
3. **Progressive disclosure** -- advanced settings hidden by default, shown on demand
4. **Test before save** -- every connector is validated with a live connection test before being stored
5. **Same data model for web and CLI** -- the `fields` schema in `MCPRecipe` drives both the modal renderer and the interactive CLI prompts
