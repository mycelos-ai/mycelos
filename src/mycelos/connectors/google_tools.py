"""Google Tools — Gmail, Calendar, Drive via gog CLI.

gog (gogcli) handles OAuth and token management. We call it as a
subprocess and parse JSON output. Agents never see Google credentials.

Install: brew install gogcli
Auth: gog auth add your@gmail.com
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any


def is_gog_installed() -> bool:
    """Check if gog CLI is available."""
    return shutil.which("gog") is not None


def get_gog_accounts() -> list[str]:
    """List configured gog accounts."""
    if not is_gog_installed():
        return []
    try:
        result = subprocess.run(
            ["gog", "auth", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if isinstance(data, list):
                return [
                    a.get("email", a.get("account", ""))
                    for a in data
                    if isinstance(a, dict)
                ]
            return []
        return []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def _run_gog(args: list[str], timeout: int = 30) -> dict[str, Any]:
    """Run a gog command and return parsed JSON or error.

    Args:
        args: Command arguments to pass to gog (e.g. ["gmail", "search", "test"]).
        timeout: Maximum seconds to wait for the command.

    Returns:
        Parsed JSON response from gog, or a dict with an "error" key.
    """
    if not is_gog_installed():
        return {"error": "gog is not installed. Run: brew install gogcli"}
    try:
        result = subprocess.run(
            ["gog"] + args + ["--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return {
                "error": f"gog error: {result.stderr.strip() or result.stdout.strip()}"
            }
        if result.stdout.strip():
            return json.loads(result.stdout)
        return {"result": "ok", "output": ""}
    except subprocess.TimeoutExpired:
        return {"error": f"gog timed out after {timeout}s"}
    except json.JSONDecodeError:
        return {"error": "gog returned invalid JSON", "raw": result.stdout[:500]}
    except FileNotFoundError:
        return {"error": "gog not found"}


def gmail_search(
    query: str, max_results: int = 10, account: str | None = None
) -> dict[str, Any]:
    """Search Gmail via gog. Returns threads/messages.

    Args:
        query: Gmail search query (e.g. "is:unread", "from:boss@co.com").
        max_results: Maximum number of results to return.
        account: Optional Google account email to use.
    """
    args = ["gmail", "search", query, "--max", str(max_results)]
    if account:
        args = ["--account", account] + args
    return _run_gog(args)


def gmail_read(
    message_id: str, account: str | None = None
) -> dict[str, Any]:
    """Read a specific Gmail message.

    Args:
        message_id: The Gmail message ID.
        account: Optional Google account email to use.
    """
    args = ["gmail", "message", "get", message_id]
    if account:
        args = ["--account", account] + args
    return _run_gog(args)


def gmail_send(
    to: str, subject: str, body: str, account: str | None = None
) -> dict[str, Any]:
    """Send an email via Gmail.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body text.
        account: Optional Google account email to use.
    """
    args = ["gmail", "send", "--to", to, "--subject", subject, "--body", body]
    if account:
        args = ["--account", account] + args
    return _run_gog(args, timeout=30)


def gmail_labels(account: str | None = None) -> dict[str, Any]:
    """List Gmail labels.

    Args:
        account: Optional Google account email to use.
    """
    args = ["gmail", "labels", "list"]
    if account:
        args = ["--account", account] + args
    return _run_gog(args)


def calendar_list(
    days: int = 7, account: str | None = None
) -> dict[str, Any]:
    """List upcoming calendar events.

    Args:
        days: Number of days ahead to look.
        account: Optional Google account email to use.
    """
    args = ["calendar", "list", "--days", str(days)]
    if account:
        args = ["--account", account] + args
    return _run_gog(args)


def calendar_today(account: str | None = None) -> dict[str, Any]:
    """List today's calendar events.

    Args:
        account: Optional Google account email to use.
    """
    args = ["calendar", "today"]
    if account:
        args = ["--account", account] + args
    return _run_gog(args)


def drive_list(
    query: str = "", max_results: int = 10, account: str | None = None
) -> dict[str, Any]:
    """List Google Drive files.

    Args:
        query: Optional search query to filter files.
        max_results: Maximum number of results to return.
        account: Optional Google account email to use.
    """
    args = ["drive", "list", "--max", str(max_results)]
    if query:
        args.extend(["--query", query])
    if account:
        args = ["--account", account] + args
    return _run_gog(args)
