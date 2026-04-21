"""Tests for reminder channels — remind_via, smart scheduler, notifications.

Covers:
- Schema: remind_via column
- Auto-populate remind_via from active channels
- ReminderService: check due tasks, generate message, dispatch
- Smart scheduling: next_check_at calculation
- Channel dispatch: chat injection, Telegram send
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-reminder"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def kb(app):
    from mycelos.knowledge.service import KnowledgeBase
    return KnowledgeBase(app)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_remind_via_column_exists(self, tmp_path):
        from mycelos.storage.database import SQLiteStorage
        db = SQLiteStorage(tmp_path / "test.db")
        db.execute(
            "INSERT INTO knowledge_notes (path, title, remind_via) VALUES (?, ?, ?)",
            ("tasks/test", "Test", '["chat", "telegram"]'),
        )
        row = db.fetchone("SELECT remind_via FROM knowledge_notes WHERE path = ?", ("tasks/test",))
        assert json.loads(row["remind_via"]) == ["chat", "telegram"]

    def test_remind_via_defaults_to_null(self, tmp_path):
        """New schema stores NULL when the caller doesn't specify channels.

        The ReminderService resolves NULL at fire time to 'every active
        channel' (chat always, plus telegram/email if they're configured).
        This lets a user who adds Telegram AFTER setting a reminder still
        get it on Telegram.
        """
        from mycelos.storage.database import SQLiteStorage
        db = SQLiteStorage(tmp_path / "test.db")
        db.execute(
            "INSERT INTO knowledge_notes (path, title) VALUES (?, ?)",
            ("tasks/test2", "Test2"),
        )
        row = db.fetchone("SELECT remind_via FROM knowledge_notes WHERE path = ?", ("tasks/test2",))
        assert row["remind_via"] is None


# ---------------------------------------------------------------------------
# Auto-populate remind_via
# ---------------------------------------------------------------------------

class TestRemindViaSemantics:
    """Notes are stored with remind_via=NULL by default; the ReminderService
    resolves that to every currently-active channel at fire time."""

    def test_write_stores_null_by_default(self, app, kb):
        """kb.write() does not hardcode channels — NULL means 'decide at fire time'."""
        path = kb.write("Buy milk", "2L", type="task", reminder=True, due="2026-04-01")
        meta = kb._indexer.get_note_meta(path)
        assert meta["remind_via"] is None

    def test_default_channels_chat_only_without_telegram(self, app, kb):
        """No active channels beyond chat → default resolves to ['chat']."""
        from mycelos.knowledge.reminder import ReminderService
        rs = ReminderService(app)
        assert rs._default_channels() == ["chat"]

    def test_default_channels_includes_telegram_when_active(self, app, kb):
        """Active Telegram channel → default resolves to chat + telegram."""
        from mycelos.knowledge.reminder import ReminderService
        app.storage.execute(
            "INSERT OR IGNORE INTO channels (id, channel_type, status) VALUES (?, ?, ?)",
            ("telegram", "telegram", "active"),
        )
        rs = ReminderService(app)
        defaults = rs._default_channels()
        assert "chat" in defaults
        assert "telegram" in defaults

    def test_existing_reminder_picks_up_telegram_added_later(self, app, kb):
        """A reminder written BEFORE Telegram was configured must still
        fire on Telegram once the channel becomes active — the whole point
        of resolving remind_via at fire time."""
        from mycelos.knowledge.reminder import ReminderService
        path = kb.write("Call dentist", "...", type="task", reminder=True,
                        due=date.today().isoformat())
        # remind_via is NULL at rest
        meta = kb._indexer.get_note_meta(path)
        assert meta["remind_via"] is None

        # User configures Telegram afterwards
        app.storage.execute(
            "INSERT OR IGNORE INTO channels (id, channel_type, status) VALUES (?, ?, ?)",
            ("telegram", "telegram", "active"),
        )

        # Mock LLM + telegram dispatch so check_and_notify can run
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Reminder!"
        mock_response.total_tokens = 5
        mock_llm.complete.return_value = mock_response
        app._llm = mock_llm

        rs = ReminderService(app)
        with patch.object(rs, "_send_telegram", return_value=True) as mock_tg:
            rs.check_and_notify()
            assert mock_tg.called, "telegram must be called even though the note has remind_via=NULL"


# ---------------------------------------------------------------------------
# ReminderService
# ---------------------------------------------------------------------------

class TestReminderService:
    def test_find_due_reminders(self, app, kb):
        """Finds tasks that are due today or overdue with reminder=True."""
        from mycelos.knowledge.reminder import ReminderService
        rs = ReminderService(app)

        kb.write("Due today", "...", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)
        kb.write("Not due", "...", type="task", status="open",
                 due=(date.today() + timedelta(days=5)).isoformat(), reminder=True)
        kb.write("No reminder", "...", type="task", status="open",
                 due=date.today().isoformat(), reminder=False)

        due_tasks = rs.get_due_reminders()
        assert len(due_tasks) == 1
        assert "Due today" in due_tasks[0]["title"]

    def test_find_overdue_reminders(self, app, kb):
        """Overdue tasks with reminder are also found."""
        from mycelos.knowledge.reminder import ReminderService
        rs = ReminderService(app)

        kb.write("Overdue task", "...", type="task", status="open",
                 due=(date.today() - timedelta(days=2)).isoformat(), reminder=True)

        due_tasks = rs.get_due_reminders()
        assert len(due_tasks) == 1
        assert "Overdue" in due_tasks[0]["title"]

    def test_generate_reminder_message(self, app, kb):
        """LLM generates a natural reminder message."""
        from mycelos.knowledge.reminder import ReminderService
        rs = ReminderService(app)

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Hey Stefan! Quick reminder: you wanted to buy milk today."
        mock_response.total_tokens = 30
        mock_llm.complete.return_value = mock_response
        app._llm = mock_llm

        tasks = [{"title": "Buy milk", "due": "2026-03-29", "priority": 0}]
        message = rs.generate_message(tasks)
        assert "milk" in message.lower() or mock_llm.complete.called

    def test_next_check_at_calculation(self, app, kb):
        """next_check_at is the earliest due date of pending reminders."""
        from mycelos.knowledge.reminder import ReminderService
        rs = ReminderService(app)

        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        next_week = (date.today() + timedelta(days=7)).isoformat()

        kb.write("Tomorrow", "...", type="task", status="open",
                 due=tomorrow, reminder=True)
        kb.write("Next week", "...", type="task", status="open",
                 due=next_week, reminder=True)

        next_check = rs.next_check_at()
        assert next_check == tomorrow

    def test_next_check_at_none_when_no_reminders(self, app, kb):
        """No pending reminders → None."""
        from mycelos.knowledge.reminder import ReminderService
        rs = ReminderService(app)

        kb.write("No reminder", "...", type="note")
        assert rs.next_check_at() is None

    def test_dispatch_chat(self, app, kb):
        """Chat dispatch stores the reminder for session injection."""
        from mycelos.knowledge.reminder import ReminderService
        rs = ReminderService(app)

        result = rs.dispatch("chat", "Don't forget to buy milk!")
        assert result is True

    def test_dispatch_telegram(self, app, kb):
        """Telegram dispatch sends via bot API."""
        from mycelos.knowledge.reminder import ReminderService
        rs = ReminderService(app)

        # Register Telegram channel with a chat_id
        app.storage.execute(
            "INSERT OR IGNORE INTO channels (id, channel_type, status, config) VALUES (?, ?, ?, ?)",
            ("telegram", "telegram", "active", '{"chat_id": "12345"}'),
        )

        with patch("mycelos.knowledge.reminder.ReminderService._send_telegram") as mock_send:
            mock_send.return_value = True
            result = rs.dispatch("telegram", "Don't forget to buy milk!")
            mock_send.assert_called_once()
            assert result is True


# ---------------------------------------------------------------------------
# Full flow
# ---------------------------------------------------------------------------

class TestNoteWriteWithRemindIn:
    """note_write with remind_in persists remind_at (no daemon thread)."""

    def test_remind_in_persists_remind_at(self, app, kb):
        """remind_in='5m' writes remind_at≈now+5min to the DB, no thread."""
        from mycelos.tools.knowledge import execute_note_write
        from datetime import datetime, timezone, timedelta
        import threading

        before = threading.active_count()
        result = execute_note_write({
            "title": "Test timer",
            "content": "Should fire soon",
            "note_type": "task",
            "remind_in": "1m",
        }, {"app": app})
        after = threading.active_count()

        assert result["status"] == "created"
        assert "1 min" in result.get("reminder_in", "")

        meta = kb._indexer.get_note_meta(result["path"])
        assert meta["reminder"] == 1
        assert meta["remind_at"] is not None

        # The stored remind_at must be roughly now + 1 minute
        stored = datetime.fromisoformat(meta["remind_at"].replace("Z", "+00:00"))
        expected = datetime.now(timezone.utc) + timedelta(minutes=1)
        delta = abs((stored - expected).total_seconds())
        assert delta < 10, f"remind_at drifted {delta}s from expected"

        # No new reminder daemon thread started
        reminder_threads = [t for t in threading.enumerate() if t.name.startswith("reminder-")]
        assert reminder_threads == []
        # Overall thread count must not grow from note_write alone
        assert after <= before + 1  # tolerance for unrelated pool activity

    def test_remind_in_without_due_still_works(self, app, kb):
        """remind_in without explicit due defaults to today + delta."""
        from mycelos.tools.knowledge import execute_note_write

        result = execute_note_write({
            "title": "Quick reminder",
            "content": "...",
            "note_type": "task",
            "remind_in": "5m",
        }, {"app": app})

        assert result["status"] == "created"
        meta = kb._indexer.get_note_meta(result["path"])
        assert meta["remind_at"] is not None


class TestNoteRemindWithRelative:
    """note_remind('5m') also persists remind_at without a daemon thread."""

    def test_relative_when_persists_remind_at(self, app, kb):
        from mycelos.tools.knowledge import execute_note_write, execute_note_remind
        from datetime import datetime, timezone, timedelta
        import threading

        write_result = execute_note_write({
            "title": "Needs reminder later",
            "content": "x",
            "note_type": "task",
            "status": "open",
        }, {"app": app})
        path = write_result["path"]

        before = threading.active_count()
        result = execute_note_remind({"path": path, "when": "10m"}, {"app": app})
        after = threading.active_count()

        assert result["status"] in ("reminder_scheduled", "reminder_set")

        meta = kb._indexer.get_note_meta(path)
        assert meta["remind_at"] is not None
        stored = datetime.fromisoformat(meta["remind_at"].replace("Z", "+00:00"))
        expected = datetime.now(timezone.utc) + timedelta(minutes=10)
        assert abs((stored - expected).total_seconds()) < 10

        reminder_threads = [t for t in threading.enumerate() if t.name.startswith("reminder-")]
        assert reminder_threads == []
        assert after <= before + 1


class TestFullFlow:
    def test_check_and_notify(self, app, kb):
        """Full flow: create task → check → generate message → dispatch."""
        from mycelos.knowledge.reminder import ReminderService
        rs = ReminderService(app)

        kb.write("Call Edea", "about vacation", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)

        # Mock LLM
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Reminder: Call Edea about vacation today!"
        mock_response.total_tokens = 20
        mock_llm.complete.return_value = mock_response
        app._llm = mock_llm

        results = rs.check_and_notify()
        assert results["tasks_found"] >= 1
        assert results["notifications_sent"] >= 1


# ---------------------------------------------------------------------------
# get_due_reminders_now — remind_at + reminder_fired_at semantics
# ---------------------------------------------------------------------------


class TestDueRemindersNow:
    """Unified "is this reminder ripe *right now*" query.

    Honors both remind_at (full datetime) and due (date granularity),
    and excludes already-dispatched or already-dismissed rows.
    """

    def _mock_llm(self, app):
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Reminder!"
        mock_response.total_tokens = 10
        mock_llm.complete.return_value = mock_response
        app._llm = mock_llm

    def test_remind_at_in_past_is_due(self, app, kb):
        from mycelos.knowledge.reminder import ReminderService
        kb.write("Past task", "x", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)
        path = kb.list_notes(type="task")[0]["path"]
        app.storage.execute(
            "UPDATE knowledge_notes SET remind_at=? WHERE path=?",
            ("2020-01-01T00:00:00Z", path),
        )
        rs = ReminderService(app)
        due = rs.get_due_reminders_now()
        assert any(r["path"] == path for r in due)

    def test_remind_at_in_future_is_not_due(self, app, kb):
        from mycelos.knowledge.reminder import ReminderService
        from datetime import datetime, timedelta, timezone
        kb.write("Future task", "x", type="task", status="open",
                 due=(date.today() + timedelta(days=7)).isoformat(), reminder=True)
        path = kb.list_notes(type="task")[0]["path"]
        future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        app.storage.execute(
            "UPDATE knowledge_notes SET remind_at=? WHERE path=?",
            (future, path),
        )
        rs = ReminderService(app)
        due = rs.get_due_reminders_now()
        assert all(r["path"] != path for r in due)

    def test_due_date_fallback_when_remind_at_absent(self, app, kb):
        """When remind_at is NULL, we still fire on due<=today (classic)."""
        from mycelos.knowledge.reminder import ReminderService
        kb.write("Classic", "x", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)
        path = kb.list_notes(type="task")[0]["path"]
        rs = ReminderService(app)
        due = rs.get_due_reminders_now()
        assert any(r["path"] == path for r in due)

    def test_future_due_date_is_not_due_now(self, app, kb):
        from datetime import timedelta
        from mycelos.knowledge.reminder import ReminderService
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        kb.write("Tomorrow task", "x", type="task", status="open",
                 due=tomorrow, reminder=True)
        path = kb.list_notes(type="task")[0]["path"]
        rs = ReminderService(app)
        due = rs.get_due_reminders_now()
        assert all(r["path"] != path for r in due)

    def test_already_fired_is_excluded(self, app, kb):
        """reminder_fired_at set → the row is done, never fire again."""
        from mycelos.knowledge.reminder import ReminderService
        kb.write("Done", "x", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)
        path = kb.list_notes(type="task")[0]["path"]
        app.storage.execute(
            "UPDATE knowledge_notes SET reminder_fired_at=? WHERE path=?",
            ("2026-04-11T09:00:00Z", path),
        )
        rs = ReminderService(app)
        due = rs.get_due_reminders_now()
        assert all(r["path"] != path for r in due)

    def test_remind_at_wins_over_due(self, app, kb):
        """If remind_at is in the future, the row is not due even if
        due date is today — remind_at takes precedence."""
        from datetime import datetime, timedelta, timezone
        from mycelos.knowledge.reminder import ReminderService
        kb.write("Mixed", "x", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)
        path = kb.list_notes(type="task")[0]["path"]
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        app.storage.execute(
            "UPDATE knowledge_notes SET remind_at=? WHERE path=?",
            (future, path),
        )
        rs = ReminderService(app)
        due = rs.get_due_reminders_now()
        assert all(r["path"] != path for r in due)


class TestFireBookkeeping:
    """check_and_notify sets reminder_fired_at (not reminder=0)."""

    def _mock_llm(self, app):
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Reminder!"
        mock_response.total_tokens = 10
        mock_llm.complete.return_value = mock_response
        app._llm = mock_llm

    def test_successful_dispatch_sets_reminder_fired_at(self, app, kb):
        from mycelos.knowledge.reminder import ReminderService
        kb.write("Milk", "buy it", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)
        self._mock_llm(app)

        rs = ReminderService(app)
        rs.check_and_notify()

        row = app.storage.fetchone(
            "SELECT reminder, reminder_fired_at FROM knowledge_notes WHERE title=?",
            ("Milk",),
        )
        # reminder flag stays 1 (the task still HAS a reminder configured),
        # but reminder_fired_at records that we already dispatched it.
        assert row["reminder"] == 1
        assert row["reminder_fired_at"] is not None

    def test_fired_reminders_dont_refire_on_next_tick(self, app, kb):
        from mycelos.knowledge.reminder import ReminderService
        kb.write("Once", "x", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)
        self._mock_llm(app)

        rs = ReminderService(app)
        rs.check_and_notify()
        second = rs.check_and_notify()
        assert second["tasks_found"] == 0
        assert second["notifications_sent"] == 0


class TestDismiss:
    """mark_dismissed stops a reminder without firing it."""

    def test_dismiss_sets_fired_at(self, app, kb):
        from mycelos.knowledge.reminder import ReminderService
        kb.write("Dismiss me", "x", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)
        path = kb.list_notes(type="task")[0]["path"]
        rs = ReminderService(app)
        assert rs.mark_dismissed(path) is True

        row = app.storage.fetchone(
            "SELECT reminder_fired_at FROM knowledge_notes WHERE path=?", (path,)
        )
        assert row["reminder_fired_at"] is not None

    def test_dismiss_hides_from_due_query(self, app, kb):
        from mycelos.knowledge.reminder import ReminderService
        kb.write("Hide me", "x", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)
        path = kb.list_notes(type="task")[0]["path"]
        rs = ReminderService(app)
        rs.mark_dismissed(path)

        due = rs.get_due_reminders_now()
        assert all(r["path"] != path for r in due)

    def test_dismiss_is_idempotent(self, app, kb):
        """Calling dismiss twice doesn't double-audit and doesn't fail."""
        from mycelos.knowledge.reminder import ReminderService
        kb.write("Twice", "x", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)
        path = kb.list_notes(type="task")[0]["path"]
        rs = ReminderService(app)
        assert rs.mark_dismissed(path) is True
        # Second call is a no-op because fired_at is already set
        assert rs.mark_dismissed(path) is False

    def test_dismiss_unknown_path_returns_false(self, app):
        from mycelos.knowledge.reminder import ReminderService
        rs = ReminderService(app)
        assert rs.mark_dismissed("tasks/does-not-exist") is False


class TestReminderTickJob:
    """The Huey periodic job drives ReminderService on every tick."""

    def test_reminder_tick_check_fires_due_reminders(self, app, kb):
        from mycelos.scheduler.jobs import reminder_tick_check
        kb.write("Tickable", "x", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Reminder"
        mock_response.total_tokens = 5
        mock_llm.complete.return_value = mock_response
        app._llm = mock_llm

        result = reminder_tick_check(app)
        assert result["tasks_found"] >= 1
        # After the tick, the row is marked as fired
        row = app.storage.fetchone(
            "SELECT reminder_fired_at FROM knowledge_notes WHERE title=?",
            ("Tickable",),
        )
        assert row["reminder_fired_at"] is not None

    def test_reminder_tick_check_no_due_returns_zero(self, app, kb):
        from datetime import timedelta
        from mycelos.scheduler.jobs import reminder_tick_check
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        kb.write("Tomorrow", "x", type="task", status="open",
                 due=tomorrow, reminder=True)
        result = reminder_tick_check(app)
        assert result["tasks_found"] == 0
