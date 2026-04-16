"""Tests for Google/gog connector."""
import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from mycelos.connectors.google_tools import (
    _run_gog,
    calendar_list,
    calendar_today,
    drive_list,
    get_gog_accounts,
    gmail_labels,
    gmail_read,
    gmail_search,
    gmail_send,
    is_gog_installed,
)


class TestIsGogInstalled:
    """Tests for gog CLI detection."""

    def test_installed(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"):
            assert is_gog_installed() is True

    def test_not_installed(self) -> None:
        with patch("shutil.which", return_value=None):
            assert is_gog_installed() is False


class TestRunGog:
    """Tests for the _run_gog subprocess wrapper."""

    def test_not_installed_returns_error(self) -> None:
        with patch("shutil.which", return_value=None):
            result = _run_gog(["gmail", "search", "test"])
            assert "error" in result
            assert "not installed" in result["error"]

    def test_success_parses_json(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"threads": [{"subject": "Test"}]}),
                stderr="",
            )
            result = _run_gog(["gmail", "search", "test"])
            assert "threads" in result
            assert result["threads"][0]["subject"] == "Test"

    def test_nonzero_exit_returns_error(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="auth error"
            )
            result = _run_gog(["gmail", "search", "test"])
            assert "error" in result
            assert "auth error" in result["error"]

    def test_timeout_returns_error(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch(
                 "subprocess.run",
                 side_effect=subprocess.TimeoutExpired("gog", 30),
             ):
            result = _run_gog(["gmail", "search", "test"])
            assert "error" in result
            assert "timed out" in result["error"]

    def test_invalid_json_returns_error(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="not json {{{", stderr=""
            )
            result = _run_gog(["gmail", "search", "test"])
            assert "error" in result
            assert "invalid JSON" in result["error"]

    def test_empty_stdout_returns_ok(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr=""
            )
            result = _run_gog(["gmail", "send", "--to", "x@y.com"])
            assert result == {"result": "ok", "output": ""}

    def test_file_not_found_returns_error(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run", side_effect=FileNotFoundError):
            result = _run_gog(["gmail", "search", "test"])
            assert "error" in result
            assert "not found" in result["error"]


class TestGmailSearch:
    """Tests for gmail_search argument building."""

    def test_basic_search(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='{"threads": []}', stderr=""
            )
            gmail_search("is:unread", max_results=5)
            call_args = mock_run.call_args[0][0]
            assert call_args == [
                "gog", "gmail", "search", "is:unread",
                "--max", "5", "--json",
            ]

    def test_search_with_account(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='{"threads": []}', stderr=""
            )
            gmail_search("is:unread", max_results=5, account="me@gmail.com")
            call_args = mock_run.call_args[0][0]
            assert "--account" in call_args
            assert "me@gmail.com" in call_args
            assert call_args.index("--account") < call_args.index("gmail")


class TestGmailRead:
    """Tests for gmail_read."""

    def test_read_message(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"id": "abc123", "subject": "Hello"}',
                stderr="",
            )
            result = gmail_read("abc123")
            assert result["id"] == "abc123"


class TestGmailSend:
    """Tests for gmail_send."""

    def test_send_builds_args(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='{"result": "sent"}', stderr=""
            )
            gmail_send("to@example.com", "Subject", "Body text")
            call_args = mock_run.call_args[0][0]
            assert "--to" in call_args
            assert "to@example.com" in call_args
            assert "--subject" in call_args
            assert "Subject" in call_args
            assert "--body" in call_args


class TestGmailLabels:
    """Tests for gmail_labels."""

    def test_labels(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"labels": ["INBOX", "SENT"]}',
                stderr="",
            )
            result = gmail_labels()
            assert "labels" in result


class TestCalendar:
    """Tests for calendar functions."""

    def test_calendar_list(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='{"events": []}', stderr=""
            )
            result = calendar_list(days=3)
            assert "events" in result
            call_args = mock_run.call_args[0][0]
            assert "--days" in call_args
            assert "3" in call_args

    def test_calendar_today(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='{"events": []}', stderr=""
            )
            result = calendar_today()
            assert "events" in result
            call_args = mock_run.call_args[0][0]
            assert "today" in call_args


class TestDriveList:
    """Tests for drive_list."""

    def test_drive_list_basic(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='{"files": []}', stderr=""
            )
            result = drive_list()
            assert "files" in result

    def test_drive_list_with_query(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='{"files": []}', stderr=""
            )
            drive_list(query="type:pdf", max_results=5)
            call_args = mock_run.call_args[0][0]
            assert "--query" in call_args
            assert "type:pdf" in call_args


class TestGetGogAccounts:
    """Tests for get_gog_accounts."""

    def test_empty_list(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="[]", stderr=""
            )
            assert get_gog_accounts() == []

    def test_with_accounts(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps([
                    {"email": "me@gmail.com"},
                    {"email": "work@company.com"},
                ]),
                stderr="",
            )
            accounts = get_gog_accounts()
            assert len(accounts) == 2
            assert "me@gmail.com" in accounts
            assert "work@company.com" in accounts

    def test_not_installed_returns_empty(self) -> None:
        with patch("shutil.which", return_value=None):
            assert get_gog_accounts() == []

    def test_timeout_returns_empty(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gog"), \
             patch(
                 "subprocess.run",
                 side_effect=subprocess.TimeoutExpired("gog", 10),
             ):
            assert get_gog_accounts() == []
