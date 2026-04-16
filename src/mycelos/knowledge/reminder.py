"""ReminderService — checks due tasks and dispatches notifications.

Checks knowledge_notes for tasks with reminder=True and due <= today.
Generates a message via LLM (Haiku) and dispatches to each channel
in the remind_via array (chat, telegram, email).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mycelos.app import App

logger = logging.getLogger("mycelos.reminder")


class ReminderService:
    """Checks for due reminders and sends notifications."""

    def __init__(self, app: "App") -> None:
        self._app = app

    def get_due_reminders(self) -> list[dict]:
        """Legacy entry: tasks that are due today or overdue, ignoring
        remind_at precision and the fired flag. Kept for compatibility;
        prefer :meth:`get_due_reminders_now` for anything new.
        """
        today = date.today().isoformat()
        rows = self._app.storage.fetchall(
            """SELECT path, title, type, status, due, priority, remind_via, reminder
               FROM knowledge_notes
               WHERE reminder = 1
                 AND status IN ('open', 'in-progress', 'active')
                 AND due IS NOT NULL
                 AND due <= ?
               ORDER BY due, priority DESC""",
            (today,),
        )
        return [dict(r) for r in rows]

    def get_due_reminders_now(self) -> list[dict]:
        """Unified "ripe right now" query used by both the scheduler tick
        and the Inbox bell.

        A row is returned when it satisfies all of:

        * ``reminder = 1`` (user asked to be reminded)
        * ``status IN ('open', 'in-progress', 'active')``
        * ``reminder_fired_at IS NULL`` (not already dispatched / dismissed)
        * Either ``remind_at <= now`` (exact datetime), or
          ``remind_at IS NULL AND due <= today`` (classic date-only fallback)

        ``remind_at`` *always wins* when it is set: a row with ``remind_at``
        in the future is not due yet, even if ``due`` is in the past.
        """
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        today = date.today().isoformat()
        rows = self._app.storage.fetchall(
            """SELECT path, title, type, status, due, remind_at, priority,
                      remind_via, reminder
               FROM knowledge_notes
               WHERE reminder = 1
                 AND status IN ('open', 'in-progress', 'active')
                 AND reminder_fired_at IS NULL
                 AND (
                       (remind_at IS NOT NULL AND remind_at <= ?)
                    OR (remind_at IS NULL AND due IS NOT NULL AND due <= ?)
                 )
               ORDER BY COALESCE(remind_at, due), priority DESC""",
            (now_iso, today),
        )
        return [dict(r) for r in rows]

    def next_check_at(self) -> str | None:
        """Return the earliest due date of pending reminders, or None."""
        row = self._app.storage.fetchone(
            """SELECT MIN(due) as next_due
               FROM knowledge_notes
               WHERE reminder = 1
                 AND status IN ('open', 'in-progress', 'active')
                 AND due IS NOT NULL
                 AND due > ?""",
            (date.today().isoformat(),),
        )
        if row and row.get("next_due"):
            return row["next_due"]
        return None

    def generate_message(self, tasks: list[dict]) -> str:
        """Generate a natural reminder message via LLM (Haiku)."""
        llm = getattr(self._app, "_llm", None) or getattr(self._app, "llm", None)
        if not llm:
            return self._fallback_message(tasks)

        task_lines = []
        for t in tasks:
            overdue = t.get("due", "") < date.today().isoformat()
            urgency = "OVERDUE" if overdue else "due today"
            prio = f" [priority {t.get('priority', 0)}]" if t.get("priority", 0) > 0 else ""
            task_lines.append(f"- {t['title']} ({urgency}: {t.get('due', '?')}){prio}")

        prompt = (
            "Write a brief, friendly reminder message for these tasks. "
            "Keep it short (2-3 sentences max). Be warm but concise. "
            "Respond in the user's language.\n\n"
            "Tasks:\n" + "\n".join(task_lines)
        )

        try:
            model = self._app.resolve_cheapest_model()

            response = llm.complete(
                [
                    {"role": "system", "content": "You are a helpful reminder assistant. Be brief and friendly."},
                    {"role": "user", "content": prompt},
                ],
                model=model,
            )
            if response.content and "error" not in response.content.lower()[:20]:
                return response.content
            return self._fallback_message(tasks)
        except Exception as e:
            logger.warning("LLM reminder generation failed: %s", e)
            return self._fallback_message(tasks)

    @staticmethod
    def _fallback_message(tasks: list[dict]) -> str:
        """Simple fallback if LLM is unavailable."""
        lines = ["Reminder:"]
        for t in tasks:
            lines.append(f"- {t['title']} (due: {t.get('due', '?')})")
        return "\n".join(lines)

    def dispatch(self, channel: str, message: str) -> bool:
        """Send a reminder to a specific channel."""
        if channel == "chat":
            return self._dispatch_chat(message)
        elif channel == "telegram":
            return self._send_telegram(message)
        else:
            logger.warning("Unknown reminder channel: %s", channel)
            return False

    def _dispatch_chat(self, message: str) -> bool:
        """Store reminder for chat injection on next session message."""
        try:
            self._app.memory.set(
                "default", "system", "pending_reminder", message,
                created_by="reminder-service",
            )
            return True
        except Exception as e:
            logger.warning("Chat reminder dispatch failed: %s", e)
            return False

    def _send_telegram(self, message: str) -> bool:
        """Send reminder via Telegram bot."""
        try:
            # Get Telegram config
            channel = self._app.storage.fetchone(
                "SELECT config FROM channels WHERE id = 'telegram' AND status = 'active'"
            )
            if not channel:
                logger.debug("Telegram not configured")
                return False

            config = json.loads(channel.get("config", "{}"))
            chat_id = config.get("chat_id")
            if not chat_id:
                # Try to get from memory (set during first Telegram interaction)
                chat_id = self._app.memory.get("default", "system", "telegram_chat_id")

            if not chat_id:
                logger.debug("No Telegram chat_id configured")
                return False

            # Get bot token from credentials
            cred = self._app.credentials.get_credential("telegram")
            if not cred or not cred.get("api_key"):
                logger.debug("No Telegram bot token")
                return False

            token = cred["api_key"]

            # Send via Telegram API
            import urllib.request
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                if result.get("ok"):
                    logger.info("Telegram reminder sent to chat_id=%s", chat_id)
                    return True
                else:
                    logger.warning("Telegram API error: %s", result.get("description"))
                    return False
        except Exception as e:
            logger.warning("Telegram reminder failed: %s", e)
            return False

    def check_and_notify(self) -> dict[str, int]:
        """Main entry point: find ripe reminders, generate message, dispatch.

        Uses :meth:`get_due_reminders_now`, so this honors both
        ``remind_at`` precision and the ``reminder_fired_at`` guard that
        prevents re-firing. After dispatch, ``reminder_fired_at`` is set
        on every row that was part of this batch (partial channel
        failures still count — we don't want to re-fire the whole thing
        because Telegram hiccuped).

        Returns dict with tasks_found, notifications_sent counts.
        """
        from datetime import datetime, timezone
        tasks = self.get_due_reminders_now()
        if not tasks:
            return {"tasks_found": 0, "notifications_sent": 0}

        message = self.generate_message(tasks)

        # Collect all unique channels from all due tasks
        channels: set[str] = set()
        for t in tasks:
            try:
                rv = json.loads(t.get("remind_via", '["chat"]'))
                channels.update(rv)
            except (json.JSONDecodeError, TypeError):
                channels.add("chat")

        channels_succeeded: list[str] = []
        channels_failed: list[str] = []
        for channel in channels:
            if self.dispatch(channel, message):
                channels_succeeded.append(channel)
            else:
                channels_failed.append(channel)

        fired_at = datetime.now(timezone.utc).isoformat()
        # Mark each row as fired so it won't re-fire on the next tick.
        # We mark even on zero-success dispatch *if* the configured channels
        # are all failing — otherwise the inbox would keep the row visible
        # indefinitely. But a complete shutout means something systemic is
        # broken; we'd rather let the doctor pick that up than spam the
        # user on every tick. So: mark when at least one channel worked,
        # OR when there were no channels at all (defensive guard).
        if channels_succeeded or not channels:
            for t in tasks:
                try:
                    self._app.storage.execute(
                        "UPDATE knowledge_notes SET reminder_fired_at = ? WHERE path = ?",
                        (fired_at, t["path"]),
                    )
                except Exception:
                    logger.warning("Failed to set reminder_fired_at for %s", t["path"], exc_info=True)

        self._app.audit.log(
            "reminder.fired",
            details={
                "tasks": len(tasks),
                "paths": [t["path"] for t in tasks],
                "channels_succeeded": channels_succeeded,
                "channels_failed": channels_failed,
            },
        )

        return {
            "tasks_found": len(tasks),
            "notifications_sent": len(channels_succeeded),
        }

    def mark_dismissed(self, path: str, *, trigger: str = "user") -> bool:
        """Mark a reminder as handled without firing it.

        Used when the user clicks an inbox entry: we record
        ``reminder_fired_at = now`` so the bell and the scheduler both
        stop showing it, and emit a ``reminder.dismissed`` audit event
        so history can distinguish user-dismissed from scheduler-fired.
        Returns True if a row was updated.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._app.storage.execute(
            """UPDATE knowledge_notes
               SET reminder_fired_at = ?
               WHERE path = ? AND reminder_fired_at IS NULL""",
            (now, path),
        )
        updated = getattr(cursor, "rowcount", 0) > 0
        if updated:
            try:
                self._app.audit.log(
                    "reminder.dismissed",
                    details={"path": path, "trigger": trigger},
                )
            except Exception:
                logger.debug("audit log failed for reminder.dismissed", exc_info=True)
        return updated
