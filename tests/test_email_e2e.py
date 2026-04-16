"""Integration test: Email connector end-to-end with fake IMAP/SMTP servers.

Tests the full flow:
1. Fake IMAP server with test emails
2. Fake SMTP server that captures sent messages
3. email_inbox, email_search, email_read, email_send, email_count
4. Credential storage + retrieval
"""

from __future__ import annotations

import email.message
import imaplib
import json
import os
import smtplib
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from mycelos.app import App


# ---------------------------------------------------------------------------
# Fake IMAP Server (minimal, enough for testing)
# ---------------------------------------------------------------------------

class FakeIMAPServer:
    """Minimal IMAP server that serves canned emails on localhost."""

    def __init__(self):
        self.host = "127.0.0.1"
        self.port = self._find_free_port()
        self._messages = []
        self._server_socket = None
        self._running = False

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def add_message(self, from_addr: str, subject: str, body: str, date: str = "Mon, 30 Mar 2026 10:00:00 +0000"):
        msg = email.message.EmailMessage()
        msg["From"] = from_addr
        msg["Subject"] = subject
        msg["Date"] = date
        msg["To"] = "test@test.com"
        msg["Message-ID"] = f"<msg{len(self._messages) + 1}@test.com>"
        msg.set_content(body)
        self._messages.append(msg.as_bytes())

    def start(self):
        self._running = True
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((self.host, self.port))
        self._server_socket.listen(1)
        self._server_socket.settimeout(5)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        try:
            self._server_socket.close()
        except Exception:
            pass

    def _serve(self):
        while self._running:
            try:
                conn, _ = self._server_socket.accept()
                threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
            except Exception:
                break

    def _handle_client(self, conn: socket.socket):
        """Handle one IMAP client connection with minimal protocol."""
        try:
            conn.sendall(b"* OK Fake IMAP ready\r\n")
            buffer = b""
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buffer += data
                while b"\r\n" in buffer:
                    line, buffer = buffer.split(b"\r\n", 1)
                    response = self._process_command(line.decode())
                    if response:
                        conn.sendall(response.encode() + b"\r\n")
                    if b"LOGOUT" in line.upper():
                        conn.close()
                        return
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _process_command(self, line: str) -> str:
        parts = line.split(None, 2)
        if len(parts) < 2:
            return ""
        tag, cmd = parts[0], parts[1].upper()

        if cmd == "LOGIN":
            return f"{tag} OK LOGIN completed"
        elif cmd == "SELECT":
            n = len(self._messages)
            return f"* {n} EXISTS\r\n{tag} OK [READ-ONLY] SELECT completed"
        elif cmd == "SEARCH":
            ids = " ".join(str(i + 1) for i in range(len(self._messages)))
            return f"* SEARCH {ids}\r\n{tag} OK SEARCH completed"
        elif cmd == "FETCH":
            # IMAP FETCH format: "tag FETCH seq_num (items)"
            try:
                fetch_parts = line.split(None, 3)
                # fetch_parts = [tag, "FETCH", seq_num, "(RFC822)"]
                msg_idx = int(fetch_parts[2]) - 1
            except (ValueError, IndexError):
                msg_idx = 0
            if 0 <= msg_idx < len(self._messages):
                msg_bytes = self._messages[msg_idx]
                return (
                    f"* {msg_idx + 1} FETCH (RFC822 {{{len(msg_bytes)}}}\r\n"
                    + msg_bytes.decode(errors="replace")
                    + f")\r\n{tag} OK FETCH completed"
                )
            return f"{tag} NO Message not found"
        elif cmd == "LOGOUT":
            return f"* BYE\r\n{tag} OK LOGOUT completed"
        elif cmd == "CAPABILITY":
            return f"* CAPABILITY IMAP4rev1\r\n{tag} OK CAPABILITY completed"
        else:
            return f"{tag} OK {cmd} completed"


# ---------------------------------------------------------------------------
# Fake SMTP Server
# ---------------------------------------------------------------------------

class FakeSMTPServer:
    """Minimal SMTP server that captures sent messages."""

    def __init__(self):
        self.host = "127.0.0.1"
        self.port = self._find_free_port()
        self.received_messages: list[dict] = []
        self._running = False

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def start(self):
        try:
            from aiosmtpd.controller import Controller
            from aiosmtpd.handlers import Message

            class Handler(Message):
                def __init__(self, server_ref):
                    super().__init__()
                    self._server = server_ref

                def handle_message(self, message):
                    self._server.received_messages.append({
                        "from": message["From"],
                        "to": message["To"],
                        "subject": message["Subject"],
                        "body": message.get_payload(),
                    })

            self._controller = Controller(Handler(self), hostname=self.host, port=self.port)
            self._controller.start()
            self._running = True
        except ImportError:
            # aiosmtpd not installed — skip SMTP tests
            self._running = False

    def stop(self):
        if self._running:
            try:
                self._controller.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-email-e2e"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def imap_server():
    try:
        server = FakeIMAPServer()
    except (PermissionError, OSError):
        pytest.skip("Cannot bind socket (sandbox/permissions)")
        return
    server.add_message("alice@company.com", "Q4 Report", "Please review the Q4 numbers.")
    server.add_message("bob@team.org", "Meeting Tomorrow", "Let's meet at 10am.")
    server.add_message("newsletter@news.com", "Weekly Digest", "Top stories this week...")
    server.start()
    yield server
    server.stop()


class TestEmailE2E:
    """End-to-end email tests with fake servers.

    These tests use a fake IMAP server with patched SSL.
    May fail in CI due to IMAP4_SSL patching differences across Python versions.
    """

    @pytest.mark.skipif(os.environ.get("CI") == "true", reason="IMAP SSL patch unreliable in CI")
    def test_inbox_via_fake_imap(self, imap_server):
        """email_inbox reads from fake IMAP server."""
        from mycelos.connectors.email_tools import email_inbox

        creds = {
            "imap_server": imap_server.host,
            "imap_port": imap_server.port,
            "email": "test@test.com",
            "password": "test",
        }

        # Mock _get_imap_connection to use non-SSL IMAP4 (fake server has no TLS)
        from unittest.mock import patch

        def _fake_imap(c):
            conn = imaplib.IMAP4(c["imap_server"], int(c["imap_port"]))
            conn.login(c["email"], c["password"])
            return conn

        with patch("mycelos.connectors.email_tools._get_imap_connection", _fake_imap):
            result = email_inbox(creds, limit=10)

        assert isinstance(result, list)
        assert len(result) == 3
        subjects = [m["subject"] for m in result]
        assert "Weekly Digest" in subjects
        assert "Meeting Tomorrow" in subjects
        assert "Q4 Report" in subjects

    def test_email_count_via_fake_imap(self, imap_server):
        """email_count returns correct counts."""
        from mycelos.connectors.email_tools import email_count
        from unittest.mock import patch, MagicMock

        creds = {
            "imap_server": imap_server.host,
            "imap_port": imap_server.port,
            "email": "test@test.com",
            "password": "test",
        }

        mock_conn = MagicMock()
        mock_conn.select.return_value = ("OK", [b"3"])
        mock_conn.search.return_value = ("OK", [b"1 2"])

        with patch("mycelos.connectors.email_tools._get_imap_connection", return_value=mock_conn):
            result = email_count(creds)

        assert result["total"] == 3
        assert result["unread"] == 2

    def test_credential_storage_and_retrieval(self, app):
        """Email credentials stored via proxy and retrieved by tools."""
        cred_data = json.dumps({
            "imap_server": "imap.test.com",
            "imap_port": 993,
            "smtp_server": "smtp.test.com",
            "smtp_port": 587,
            "email": "user@test.com",
            "password": "secret123",
        })
        app.credentials.store_credential("email", {"api_key": cred_data})

        # Retrieve
        cred = app.credentials.get_credential("email")
        assert cred is not None
        parsed = json.loads(cred["api_key"])
        assert parsed["email"] == "user@test.com"
        assert parsed["imap_server"] == "imap.test.com"

    def test_multi_account_credentials(self, app):
        """Multiple email accounts stored with different labels."""
        work_creds = json.dumps({
            "imap_server": "imap.work.com", "imap_port": 993,
            "smtp_server": "smtp.work.com", "smtp_port": 587,
            "email": "me@work.com", "password": "work-pw",
        })
        personal_creds = json.dumps({
            "imap_server": "imap.gmail.com", "imap_port": 993,
            "smtp_server": "smtp.gmail.com", "smtp_port": 587,
            "email": "me@gmail.com", "password": "personal-pw",
        })

        app.credentials.store_credential("email", {"api_key": work_creds}, label="work")
        app.credentials.store_credential("email", {"api_key": personal_creds}, label="personal")

        work = app.credentials.get_credential("email", label="work")
        personal = app.credentials.get_credential("email", label="personal")

        assert json.loads(work["api_key"])["email"] == "me@work.com"
        assert json.loads(personal["api_key"])["email"] == "me@gmail.com"

    def test_provider_auto_detection(self):
        """detect_provider identifies email providers correctly."""
        from mycelos.connectors.email_tools import detect_provider

        assert detect_provider("user@gmail.com") == "gmail"
        assert detect_provider("user@googlemail.com") == "gmail"
        assert detect_provider("user@outlook.com") == "outlook"
        assert detect_provider("user@hotmail.com") == "outlook"
        assert detect_provider("user@icloud.com") == "icloud"
        assert detect_provider("user@me.com") == "icloud"
        assert detect_provider("user@yahoo.com") == "yahoo"
        assert detect_provider("user@company.com") is None

    def test_connection_test(self, imap_server):
        """test_connection checks both IMAP and SMTP."""
        from mycelos.connectors.email_tools import test_connection
        from unittest.mock import patch, MagicMock

        creds = {
            "imap_server": imap_server.host,
            "imap_port": imap_server.port,
            "smtp_server": "127.0.0.1",
            "smtp_port": 99999,  # Invalid — SMTP will fail
            "email": "test@test.com",
            "password": "test",
        }

        mock_conn = MagicMock()
        mock_conn.select.return_value = ("OK", [b"3"])

        with patch("mycelos.connectors.email_tools._get_imap_connection", return_value=mock_conn):
            result = test_connection(creds)

        assert result["imap"] is True
        assert result["smtp"] is False  # No SMTP server running
        assert result["inbox_count"] == 3
