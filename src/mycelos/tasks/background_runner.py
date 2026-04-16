"""BackgroundTaskRunner — dispatch, lifecycle, and notification tracking for background tasks."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mycelos.app import App


def _now() -> str:
    """Return current UTC timestamp in the schema's ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class BackgroundTaskRunner:
    """Manages background task records in SQLite.

    Does NOT execute tasks — only tracks dispatch, status, steps, and notifications.
    Actual execution is handled by Huey workers or inline callers.
    """

    def __init__(self, app: App) -> None:
        self._app = app

    @property
    def _storage(self):
        return self._app.storage

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def dispatch(
        self,
        task_type: str,
        payload: dict,
        *,
        user_id: str = "default",
        session_id: str | None = None,
        agent_id: str | None = None,
        cost_limit: float | None = None,
        notify_policy: str = "on_completion",
        timeout_seconds: int = 600,
    ) -> str:
        """Insert a new background task record and return its task_id."""
        task_id = uuid.uuid4().hex
        self._storage.execute(
            """
            INSERT INTO background_tasks
                (id, task_type, status, payload, user_id, session_id, agent_id,
                 cost_limit, notify_policy, timeout_seconds, created_at)
            VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                task_type,
                json.dumps(payload),
                user_id,
                session_id,
                agent_id,
                cost_limit,
                notify_policy,
                timeout_seconds,
                _now(),
            ),
        )
        return task_id

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def get_status(self, task_id: str) -> dict:
        """Return the full task record as a dict, or empty dict if not found."""
        row = self._storage.fetchone(
            "SELECT * FROM background_tasks WHERE id = ?",
            (task_id,),
        )
        return row or {}

    def get_tasks_for_user(
        self, user_id: str, status: str | None = None
    ) -> list[dict]:
        """Return all tasks for a user, optionally filtered by status."""
        if status is not None:
            return self._storage.fetchall(
                "SELECT * FROM background_tasks WHERE user_id = ? AND status = ? ORDER BY created_at DESC",
                (user_id, status),
            )
        return self._storage.fetchall(
            "SELECT * FROM background_tasks WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )

    def get_completed_unnotified(self, user_id: str) -> list[dict]:
        """Return completed or failed tasks that have not yet been notified."""
        return self._storage.fetchall(
            """
            SELECT * FROM background_tasks
            WHERE user_id = ?
              AND status IN ('completed', 'failed')
              AND notified_at IS NULL
            ORDER BY completed_at ASC
            """,
            (user_id,),
        )

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def mark_notified(self, task_id: str) -> None:
        """Record the notification timestamp on a task."""
        self._storage.execute(
            "UPDATE background_tasks SET notified_at = ? WHERE id = ?",
            (_now(), task_id),
        )

    # ------------------------------------------------------------------
    # Lifecycle mutations
    # ------------------------------------------------------------------

    def cancel(self, task_id: str) -> bool:
        """Cancel a task. Returns True if the record existed."""
        cursor = self._storage.execute(
            "UPDATE background_tasks SET status = 'cancelled' WHERE id = ?",
            (task_id,),
        )
        return cursor.rowcount > 0

    def approve_waiting(self, task_id: str) -> bool:
        """Approve a waiting task, clearing its waiting_reason. Returns True if found."""
        cursor = self._storage.execute(
            "UPDATE background_tasks SET status = 'running', waiting_reason = NULL WHERE id = ?",
            (task_id,),
        )
        return cursor.rowcount > 0

    def start_task(self, task_id: str, total_steps: int) -> None:
        """Mark a task as running and record total_steps and started_at."""
        self._storage.execute(
            """
            UPDATE background_tasks
               SET status = 'running', started_at = ?, total_steps = ?
             WHERE id = ?
            """,
            (_now(), total_steps, task_id),
        )

    def update_step(
        self,
        task_id: str,
        step_name: str,
        status: str,
        cost: float = 0,
    ) -> None:
        """Update current_step on the task and upsert a step record."""
        self._storage.execute(
            "UPDATE background_tasks SET current_step = ? WHERE id = ?",
            (step_name, task_id),
        )

        # Determine the next step number for this task
        existing = self._storage.fetchone(
            "SELECT id, step_number FROM background_task_steps WHERE task_id = ? AND step_name = ?",
            (task_id, step_name),
        )
        if existing:
            # Update existing step row
            self._storage.execute(
                "UPDATE background_task_steps SET status = ?, cost = ? WHERE id = ?",
                (status, cost, existing["id"]),
            )
        else:
            # Determine next step_number
            row = self._storage.fetchone(
                "SELECT COALESCE(MAX(step_number), 0) AS max_n FROM background_task_steps WHERE task_id = ?",
                (task_id,),
            )
            next_number = (row["max_n"] if row else 0) + 1
            self._storage.execute(
                """
                INSERT INTO background_task_steps (task_id, step_number, step_name, status, cost, started_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, next_number, step_name, status, cost, _now()),
            )

    def complete_task(
        self,
        task_id: str,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        """Mark a task as completed, storing an optional result payload."""
        self._storage.execute(
            """
            UPDATE background_tasks
               SET status = 'completed', completed_at = ?, result = ?, error = ?
             WHERE id = ?
            """,
            (_now(), json.dumps(result) if result is not None else None, error, task_id),
        )

    def fail_task(self, task_id: str, error: str) -> None:
        """Mark a task as failed with an error message."""
        self._storage.execute(
            """
            UPDATE background_tasks
               SET status = 'failed', error = ?, completed_at = ?
             WHERE id = ?
            """,
            (error, _now(), task_id),
        )
