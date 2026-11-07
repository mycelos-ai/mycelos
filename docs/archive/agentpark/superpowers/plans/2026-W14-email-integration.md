# Plan: Email Integration — IMAP/SMTP + Gmail API

> **Week:** 14 (2026)
> **Status:** Planned
> **Priority:** High — most requested feature for beta testers

## Goal

Users can read and send email through Mycelos. Two tiers:
- **Tier 1:** Any email provider via IMAP/SMTP (2 min setup, no GCP project)
- **Tier 2:** Gmail/Google Workspace via gog CLI (full API, Calendar, Drive)

## Tier 1: Generic Email (IMAP/SMTP)

### User Flow

```
User: "Ich möchte meine Emails verbinden"
Mycelos: → /connector add email

System:
  Email Provider Setup

  Provider: [1] Gmail  [2] Outlook  [3] iCloud  [4] Custom

  > 1

  Gmail uses App Passwords for secure access.
  1. Go to myaccount.google.com/apppasswords
  2. Create an app password (16-digit code)
  3. Paste it here

  [Paste App Password]

  Email: stefan@gmail.com
  App Password: ****

  ✓ Email connected! Testing...
  ✓ IMAP: 1,247 messages in inbox
  ✓ SMTP: ready to send

  Try: "Show my unread emails" or "Search emails from Boss"
```

### Provider Presets

| Provider | IMAP Server | SMTP Server | Auth |
|----------|-------------|-------------|------|
| Gmail | imap.gmail.com:993 | smtp.gmail.com:587 | App Password |
| Outlook | outlook.office365.com:993 | smtp.office365.com:587 | Password |
| iCloud | imap.mail.me.com:993 | smtp.mail.me.com:587 | App Password |
| Yahoo | imap.mail.yahoo.com:993 | smtp.mail.yahoo.com:587 | App Password |
| Custom | user-configured | user-configured | user-configured |

### Tools

```
email_inbox(limit=10, unread_only=false)     → List recent emails
email_search(query, limit=10)                → Search by subject/sender/date
email_read(message_id)                       → Read full email content
email_send(to, subject, body, cc?, bcc?)     → Send email
email_reply(message_id, body)                → Reply to email
email_count(folder="INBOX")                  → Count messages
```

### Implementation

New file: `src/mycelos/connectors/email_tools.py`

```python
# Python stdlib only — no external dependencies
import imaplib
import smtplib
import email
from email.mime.text import MIMEText
```

Credentials stored via Credential Proxy:
```python
app.credentials.store_credential("email", {
    "imap_server": "imap.gmail.com",
    "imap_port": 993,
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "email": "stefan@gmail.com",
    "password": "<app-password>",  # encrypted via MYCELOS_MASTER_KEY
})
```

### Security

- Credentials via Credential Proxy (Constitution Rule 4)
- IMAP/SMTP connections always TLS (SSL/STARTTLS)
- Email content sanitized via ResponseSanitizer before passing to LLM
- Email addresses in tool results are NOT redacted (they're needed)
- Attachments: metadata only, no auto-download (explicit user action)

### Tool Registration

```python
# In ToolRegistry — PRIVILEGED level (needs explicit user approval)
registry.register("email_inbox", ..., ToolPermission.PRIVILEGED)
registry.register("email_search", ..., ToolPermission.PRIVILEGED)
registry.register("email_read", ..., ToolPermission.PRIVILEGED)
registry.register("email_send", ..., ToolPermission.PRIVILEGED)  # Extra confirmation
registry.register("email_reply", ..., ToolPermission.PRIVILEGED)
```

`email_send` and `email_reply` require explicit policy confirmation ("always" or per-use).

### Connector Recipe

Add to `mcp_recipes.py`:
```python
"email": MCPRecipe(
    id="email",
    name="Email (IMAP/SMTP)",
    description="Read and send email via IMAP/SMTP — works with Gmail, Outlook, iCloud, etc.",
    command="",
    transport="builtin",
    credentials=[{
        "env_var": "EMAIL_PASSWORD",
        "name": "Email App Password",
        "help": "For Gmail: myaccount.google.com/apppasswords",
    }],
    capabilities_preview=["email.read", "email.send", "email.search"],
    category="communication",
    requires_node=False,
)
```

## Tier 2: Gmail API via gog

### User Flow

```
User: /connector add gmail

System:
  Gmail API Setup (full access: Gmail + Calendar + Drive)

  This requires a Google Cloud project for OAuth.

  [Quick setup: Use IMAP instead (2 min)]
  [Full setup: Google Cloud OAuth (15 min)]

  > Full setup

  Step 1: Install gog CLI
    brew install gogcli

  Step 2: Create Google Cloud credentials
    1. Go to console.cloud.google.com
    2. Create project → Enable Gmail API
    3. Create OAuth credentials (Desktop app)
    4. Download client_secret.json

  [I have the file]

  Step 3: Configure gog
    > gog auth credentials ~/Downloads/client_secret_*.json
    > gog auth add stefan@gmail.com
    (Browser opens for OAuth consent)

  ✓ Gmail API connected!
  Available: Gmail, Calendar, Drive
```

### Tools (already in google_tools.py)

```
gmail_search(query, max_results)   → Search Gmail
gmail_read(message_id)             → Read message
gmail_send(to, subject, body)      → Send email
gmail_labels()                     → List labels
calendar_list(days=7)              → Upcoming events
calendar_today()                   → Today's events
drive_list(query, max_results)     → List files
```

### What's Already Done

- `src/mycelos/connectors/google_tools.py` — gog wrapper (search, read, send, calendar, drive)
- Only missing: Tool registration in ToolRegistry + connector recipe

## Implementation Steps

### Phase 1: IMAP/SMTP (Tier 1)

1. **`src/mycelos/connectors/email_tools.py`** — IMAP read + SMTP send
2. **Tool schemas** — email_inbox, email_search, email_read, email_send, email_reply
3. **Register in ToolRegistry** — PRIVILEGED permission level
4. **Connector recipe** — provider presets (Gmail, Outlook, iCloud, Custom)
5. **`/connector add email`** — interactive setup with provider selection
6. **Tests** — mock IMAP/SMTP, credential storage, tool execution
7. **Content sanitization** — strip HTML, limit size, redact sensitive patterns

### Phase 2: Gmail API (Tier 2)

1. **Register google_tools.py in ToolRegistry** — PRIVILEGED
2. **Connector recipe for gmail** — check gog installed, guide OAuth setup
3. **Fallback** — if gog not available, suggest IMAP Tier 1
4. **Tests** — mock gog subprocess calls

### Phase 3: Email Workflows

1. **email-digest** seed workflow — daily summary of unread emails
2. **email-notify** — forward urgent emails to Telegram
3. **email-to-knowledge** — save important emails as Knowledge notes

## Estimated Effort

| Phase | Effort | Dependencies |
|-------|--------|-------------|
| Phase 1 (IMAP/SMTP) | 2-3h | None (Python stdlib) |
| Phase 2 (Gmail API) | 1h | gog installed by user |
| Phase 3 (Workflows) | 1-2h | Phase 1 or 2 |

## Non-Goals (v1)

- OAuth flow built into Mycelos (use gog for that)
- Attachment processing (metadata only, download on explicit request)
- Email drafts/templates
- Multiple email accounts (one account per connector for now)
- IMAP IDLE / push notifications (polling only)
