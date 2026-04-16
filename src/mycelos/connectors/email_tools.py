"""Email tools — read and send via IMAP/SMTP.

Works with any email provider (Gmail, Outlook, iCloud, Yahoo, custom).
Uses Python stdlib only — no external dependencies.
Credentials stored via Credential Proxy (never visible to agents).
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import logging
import smtplib
from email.mime.text import MIMEText
from typing import Any

logger = logging.getLogger("mycelos.email")

# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {
        "imap_server": "imap.gmail.com",
        "imap_port": 993,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "help": "Create app password at myaccount.google.com/apppasswords",
    },
    "outlook": {
        "imap_server": "outlook.office365.com",
        "imap_port": 993,
        "smtp_server": "smtp.office365.com",
        "smtp_port": 587,
        "help": "Use your regular password or app password",
    },
    "icloud": {
        "imap_server": "imap.mail.me.com",
        "imap_port": 993,
        "smtp_server": "smtp.mail.me.com",
        "smtp_port": 587,
        "help": "Create app password at appleid.apple.com/account/manage",
    },
    "yahoo": {
        "imap_server": "imap.mail.yahoo.com",
        "imap_port": 993,
        "smtp_server": "smtp.mail.yahoo.com",
        "smtp_port": 587,
        "help": "Create app password in Yahoo account security settings",
    },
}


def detect_provider(email_address: str) -> str | None:
    """Detect provider from email domain."""
    domain = email_address.split("@")[-1].lower() if "@" in email_address else ""
    if "gmail" in domain or "googlemail" in domain:
        return "gmail"
    if "outlook" in domain or "hotmail" in domain or "live." in domain:
        return "outlook"
    if "icloud" in domain or "me.com" in domain or "mac.com" in domain:
        return "icloud"
    if "yahoo" in domain:
        return "yahoo"
    return None


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _sanitize_password(pw: str) -> str:
    """Strip wrapping quotes and internal spaces.

    Gmail App-Passwords are shown as ``abcd efgh ijkl mnop`` (four groups of four
    letters) — users often paste them verbatim, sometimes even wrapped in quotes
    (``"abcd efgh ijkl mnop"``). Google only accepts the bare 16-character code
    without spaces. Normalize once at connection time so the stored credential
    can stay human-readable.
    """
    if not pw:
        return pw
    pw = pw.strip()
    if len(pw) >= 2 and ((pw[0] == '"' and pw[-1] == '"') or (pw[0] == "'" and pw[-1] == "'")):
        pw = pw[1:-1]
    return pw.replace(" ", "")


def _get_imap_connection(creds: dict) -> imaplib.IMAP4_SSL:
    """Open an IMAP SSL connection."""
    conn = imaplib.IMAP4_SSL(
        creds["imap_server"],
        int(creds.get("imap_port", 993)),
        timeout=15,
    )
    conn.login(creds["email"], _sanitize_password(creds["password"]))
    return conn


def _get_smtp_connection(creds: dict) -> smtplib.SMTP:
    """Open an SMTP connection with STARTTLS."""
    conn = smtplib.SMTP(
        creds["smtp_server"],
        int(creds.get("smtp_port", 587)),
        timeout=15,
    )
    conn.starttls()
    conn.login(creds["email"], _sanitize_password(creds["password"]))
    return conn


def _parse_message(msg_data: bytes) -> dict[str, str]:
    """Parse raw email bytes into a structured dict.

    Extracts plain text body if available, otherwise converts HTML to markdown.
    This gives the LLM clean, readable content instead of raw HTML.
    """
    msg = email.message_from_bytes(msg_data)

    # Collect both plain text and HTML parts
    plain_body = ""
    html_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            text = payload.decode("utf-8", errors="replace")
            if ct == "text/plain" and not plain_body:
                plain_body = text
            elif ct == "text/html" and not html_body:
                html_body = text
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode("utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                html_body = text
            else:
                plain_body = text

    # Prefer plain text; fall back to HTML converted to markdown
    if plain_body.strip():
        body = plain_body
    elif html_body:
        body = _html_to_markdown(html_body)
    else:
        body = ""

    # Truncate long bodies
    if len(body) > 4000:
        body = body[:4000] + "\n...[truncated]"

    return {
        "from": str(msg.get("From", "")),
        "to": str(msg.get("To", "")),
        "subject": str(msg.get("Subject", "")),
        "date": str(msg.get("Date", "")),
        "message_id": str(msg.get("Message-ID", "")),
        "body": body,
    }


def _html_to_markdown(html: str) -> str:
    """Convert HTML email to clean markdown text.

    Strips newsletter formatting, tracking pixels, layout tables etc.
    Keeps headings, links, bold/italic, and list items.
    """
    import re

    # Remove script/style/head tags
    text = re.sub(r"<(script|style|head)[^>]*>.*?</\1>", "", html, flags=re.DOTALL)
    # Remove tracking pixels and hidden images
    text = re.sub(r'<img[^>]*(width=["\']1|height=["\']1|display:\s*none)[^>]*/?\s*>', "", text, flags=re.IGNORECASE)
    # Convert headers
    text = re.sub(r"<h(\d)[^>]*>(.*?)</h\d>", lambda m: "#" * int(m.group(1)) + " " + m.group(2), text)
    # Convert links (skip tracking redirects by showing just the text)
    text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL)
    # Convert bold/strong
    text = re.sub(r"<(b|strong)[^>]*>(.*?)</\1>", r"**\2**", text)
    # Convert italic/em
    text = re.sub(r"<(i|em)[^>]*>(.*?)</\1>", r"*\2*", text)
    # Convert list items
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1\n", text, flags=re.DOTALL)
    # Convert paragraphs and breaks
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<p[^>]*>", "\n\n", text)
    text = re.sub(r"</p>", "", text)
    # Remove remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    # Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Email operations
# ---------------------------------------------------------------------------

def email_inbox(
    creds: dict,
    limit: int = 10,
    unread_only: bool = False,
    folder: str = "INBOX",
) -> list[dict]:
    """List recent emails from inbox."""
    conn = _get_imap_connection(creds)
    try:
        conn.select(folder, readonly=True)
        criteria = "UNSEEN" if unread_only else "ALL"
        _, data = conn.search(None, criteria)
        msg_ids = data[0].split()

        # Get most recent N
        recent_ids = msg_ids[-limit:] if msg_ids else []
        messages = []
        for mid in reversed(recent_ids):  # newest first
            _, msg_data = conn.fetch(mid, "(RFC822)")
            if msg_data and msg_data[0] and isinstance(msg_data[0], tuple):
                parsed = _parse_message(msg_data[0][1])
                parsed["id"] = mid.decode()
                messages.append(parsed)
        return messages
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def email_search(
    creds: dict,
    query: str,
    limit: int = 10,
    folder: str = "INBOX",
) -> list[dict]:
    """Search emails by subject."""
    conn = _get_imap_connection(creds)
    try:
        conn.select(folder, readonly=True)
        # IMAP search by subject
        _, data = conn.search(None, f'SUBJECT "{query}"')
        msg_ids = data[0].split()

        recent_ids = msg_ids[-limit:] if msg_ids else []
        messages = []
        for mid in reversed(recent_ids):
            _, msg_data = conn.fetch(mid, "(RFC822)")
            if msg_data and msg_data[0] and isinstance(msg_data[0], tuple):
                parsed = _parse_message(msg_data[0][1])
                parsed["id"] = mid.decode()
                messages.append(parsed)
        return messages
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def email_read(creds: dict, message_id: str, folder: str = "INBOX") -> dict:
    """Read a specific email by IMAP sequence number."""
    conn = _get_imap_connection(creds)
    try:
        conn.select(folder, readonly=True)
        _, msg_data = conn.fetch(message_id.encode(), "(RFC822)")
        if msg_data and msg_data[0] and isinstance(msg_data[0], tuple):
            return _parse_message(msg_data[0][1])
        return {"error": f"Message {message_id} not found"}
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def email_send(
    creds: dict,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
) -> dict:
    """Send an email via SMTP."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = creds["email"]
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc

    conn = _get_smtp_connection(creds)
    try:
        conn.send_message(msg)
        logger.info("Email sent: to=%s subject=%s", to, subject[:50])
        return {"status": "sent", "to": to, "subject": subject}
    finally:
        try:
            conn.quit()
        except Exception:
            pass


def email_count(creds: dict, folder: str = "INBOX") -> dict:
    """Count messages in a folder."""
    conn = _get_imap_connection(creds)
    try:
        _, data = conn.select(folder, readonly=True)
        total = int(data[0]) if data else 0
        _, unseen_data = conn.search(None, "UNSEEN")
        unseen = len(unseen_data[0].split()) if unseen_data and unseen_data[0] else 0
        return {"folder": folder, "total": total, "unread": unseen}
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def test_connection(creds: dict) -> dict:
    """Test IMAP and SMTP connections."""
    result = {"imap": False, "smtp": False, "imap_error": None, "smtp_error": None}

    try:
        conn = _get_imap_connection(creds)
        _, data = conn.select("INBOX", readonly=True)
        result["imap"] = True
        result["inbox_count"] = int(data[0]) if data else 0
        conn.logout()
    except Exception as e:
        result["imap_error"] = str(e)

    try:
        conn = _get_smtp_connection(creds)
        result["smtp"] = True
        conn.quit()
    except Exception as e:
        result["smtp_error"] = str(e)

    return result
