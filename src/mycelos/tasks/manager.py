"""TaskManager -- CRUD for tasks in SQLite.

Tasks track user requests through their full lifecycle.
Status flow: pending -> planning -> awaiting -> running -> completed/failed/aborted/timeout/partial
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from mycelos.protocols import StorageBackend

VALID_STATUSES = {
    "pending",
    "planning",
    "awaiting",
    "running",
    "paused",
    "completed",
    "failed",
    "aborted",
    "timeout",
    "partial",
}


class TaskManager:
    """Manages task records in the tasks table."""

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def create(
        self,
        goal: str,
        user_id: str = "default",
        inputs: dict | None = None,
        budget: float | None = None,
    ) -> str:
        """Create a new task with status 'pending'. Returns task ID."""
        task_id = str(uuid.uuid4())
        self._storage.execute(
            "INSERT INTO tasks (id, user_id, goal, inputs, budget) VALUES (?, ?, ?, ?, ?)",
            (task_id, user_id, goal, json.dumps(inputs) if inputs else None, budget),
        )
        return task_id

    def get(self, task_id: str) -> dict | None:
        """Get a task by ID."""
        return self._storage.fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )

    def update_status(self, task_id: str, status: str) -> None:
        """Update task status. Validates against allowed statuses."""
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. Must be one of: {VALID_STATUSES}"
            )
        self._storage.execute(
            "UPDATE tasks SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (status, task_id),
        )

    def set_result(
        self,
        task_id: str,
        result: Any = None,
        cost: float | None = None,
        status: str = "completed",
        agent_id: str | None = None,
        model_used: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Complete a task: update status + create an attempt record in the attempts table."""
        self.update_status(task_id, status)
        attempt_id = str(uuid.uuid4())
        self._storage.execute(
            """INSERT INTO attempts (id, task_id, agent_id, model_used, cost,
               duration_ms, result, success) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                attempt_id,
                task_id,
                agent_id,
                model_used,
                cost,
                duration_ms,
                json.dumps(result) if result else None,
                1 if status == "completed" else 0,
            ),
        )

    def list_tasks(
        self,
        status: str | None = None,
        user_id: str = "default",
        limit: int = 20,
    ) -> list[dict]:
        """List tasks, optionally filtered by status."""
        if status:
            return self._storage.fetchall(
                "SELECT * FROM tasks WHERE user_id = ? AND status = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, status, limit),
            )
        return self._storage.fetchall(
            "SELECT * FROM tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )

    def get_attempts(self, task_id: str) -> list[dict]:
        """Get all attempts for a task."""
        return self._storage.fetchall(
            "SELECT * FROM attempts WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        )
