"""Email tools — read, search, send via IMAP/SMTP."""

from __future__ import annotations

from typing import Any

from mycelos.tools.registry import ToolPermission

# --- Schemas ---

EMAIL_INBOX_SCHEMA = {
    "type": "function",
    "function": {
        "name": "email_inbox",
        "description": "List recent emails from inbox. Returns subject, sender, date, and preview.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max emails to return (default 10)"},
                "unread_only": {"type": "boolean", "description": "Only show unread emails"},
                "account": {"type": "string", "description": "Email account label (e.g., 'work', 'personal'). Omit for default."},
            },
        },
    },
}

EMAIL_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "email_search",
        "description": "Search emails by subject.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (matched against subject)"},
                "limit": {"type": "integer", "description": "Max results (default 10)"},
            },
            "required": ["query"],
        },
    },
}

EMAIL_READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "email_read",
        "description": "Read a specific email by ID. Returns full body.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Email message ID from inbox/search results"},
            },
            "required": ["message_id"],
        },
    },
}

EMAIL_SEND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "email_send",
        "description": "Send an email. Requires explicit user permission.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body text"},
                "cc": {"type": "string", "description": "CC recipient (optional)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
}

EMAIL_COUNT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "email_count",
        "description": "Count total and unread emails in inbox.",
        "parameters": {"type": "object", "properties": {}},
    },
}


# --- Execution ---

def _get_email_creds(context: dict, account: str | None = None) -> dict | None:
    """Load email credentials from the credential proxy.

    Supports multiple accounts via labels:
      - "email" (default) → /connector add email
      - "email:work" → /connector add email --label work
      - "email:personal" → /connector add email --label personal
    """
    app = context["app"]
    label = account or "default"
    try:
        cred = app.credentials.get_credential("email", label=label)
        if not cred or not cred.get("api_key"):
            cred = app.credentials.get_credential("email")
        if not cred or not cred.get("api_key"):
            # Fallback: connector API stores as "connector:email"
            cred = app.credentials.get_credential("connector:email", label=label)
        if not cred or not cred.get("api_key"):
            cred = app.credentials.get_credential("connector:email")
        if cred and cred.get("api_key"):
            import json
            return json.loads(cred["api_key"])
    except Exception:
        pass
    return None


def execute_email_inbox(args: dict, context: dict) -> Any:
    creds = _get_email_creds(context, account=args.get("account"))
    if not creds:
        return {"error": "Email not configured. Run: /connector add email"}
    from mycelos.connectors.email_tools import email_inbox
    try:
        return email_inbox(
            creds, limit=args.get("limit", 10), unread_only=args.get("unread_only", False),
        )
    except Exception as e:
        return {"error": f"Email inbox failed: {e}"}


def execute_email_search(args: dict, context: dict) -> Any:
    creds = _get_email_creds(context, account=args.get("account"))
    if not creds:
        return {"error": "Email not configured. Run: /connector add email"}
    query = args.get("query", "")
    if not query:
        return {"error": "Missing search query"}
    from mycelos.connectors.email_tools import email_search
    try:
        return email_search(creds, query=query, limit=args.get("limit", 10))
    except Exception as e:
        return {"error": f"Email search failed: {e}"}


def execute_email_read(args: dict, context: dict) -> Any:
    creds = _get_email_creds(context, account=args.get("account"))
    if not creds:
        return {"error": "Email not configured. Run: /connector add email"}
    mid = args.get("message_id", "")
    if not mid:
        return {"error": "Missing message_id"}
    from mycelos.connectors.email_tools import email_read
    try:
        return email_read(creds, message_id=mid)
    except Exception as e:
        return {"error": f"Email read failed: {e}"}


def execute_email_send(args: dict, context: dict) -> Any:
    creds = _get_email_creds(context, account=args.get("account"))
    if not creds:
        return {"error": "Email not configured. Run: /connector add email"}
    to = args.get("to", "")
    subject = args.get("subject", "")
    body = args.get("body", "")
    if not to or not subject or not body:
        return {"error": "Missing required fields: to, subject, body"}
    from mycelos.connectors.email_tools import email_send
    try:
        return email_send(creds, to=to, subject=subject, body=body, cc=args.get("cc"))
    except Exception as e:
        return {"error": f"Email send failed: {e}"}


def execute_email_count(args: dict, context: dict) -> Any:
    creds = _get_email_creds(context, account=args.get("account"))
    if not creds:
        return {"error": "Email not configured. Run: /connector add email"}
    from mycelos.connectors.email_tools import email_count
    try:
        return email_count(creds)
    except Exception as e:
        return {"error": f"Email count failed: {e}"}


# --- Registration ---

def register(registry: type) -> None:
    """Register email tools."""
    registry.register("email_inbox", EMAIL_INBOX_SCHEMA, execute_email_inbox, ToolPermission.PRIVILEGED, concurrent_safe=True, category="email")
    registry.register("email_search", EMAIL_SEARCH_SCHEMA, execute_email_search, ToolPermission.PRIVILEGED, concurrent_safe=True, category="email")
    registry.register("email_read", EMAIL_READ_SCHEMA, execute_email_read, ToolPermission.PRIVILEGED, concurrent_safe=True, category="email")
    registry.register("email_send", EMAIL_SEND_SCHEMA, execute_email_send, ToolPermission.PRIVILEGED, category="email")
    registry.register("email_count", EMAIL_COUNT_SCHEMA, execute_email_count, ToolPermission.PRIVILEGED, concurrent_safe=True, category="email")
