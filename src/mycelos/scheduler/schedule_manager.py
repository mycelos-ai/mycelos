"""ScheduleManager -- CRUD for scheduled tasks with cron expression support."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from mycelos.protocols import StorageBackend


# ---------------------------------------------------------------------------
# Cron helpers
# ---------------------------------------------------------------------------


def _matches(value: int, spec: str) -> bool:
    """Check if a value matches a cron field spec.

    Supports: *, */N, N, N-M, N,M,...
    """
    if spec == "*":
        return True
    if spec.startswith("*/"):
        interval = int(spec[2:])
        return value % interval == 0
    if "-" in spec:
        low, high = spec.split("-", 1)
        return int(low) <= value <= int(high)
    if "," in spec:
        return value in [int(v) for v in spec.split(",")]
    return value == int(spec)


def parse_next_run(cron_expr: str, after: datetime | None = None) -> datetime:
    """Calculate the next run time from a cron expression.

    Simplified cron: "minute hour day month weekday"
    Examples:
      "0 8 * * *"     -- daily at 8:00
      "*/5 * * * *"   -- every 5 minutes
      "0 */2 * * *"   -- every 2 hours
      "30 9 * * 1-5"  -- weekdays at 9:30

    Returns the next datetime after *after* (default: now UTC).
    """
    if after is None:
        after = datetime.now(timezone.utc)

    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: '{cron_expr}' (need 5 fields)")

    minute_spec, hour_spec, day_spec, month_spec, weekday_spec = parts

    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    max_iterations = 48 * 60  # 48 hours of minutes

    for _ in range(max_iterations):
        if (
            _matches(candidate.minute, minute_spec)
            and _matches(candidate.hour, hour_spec)
            and _matches(candidate.day, day_spec)
            and _matches(candidate.month, month_spec)
            and _matches(candidate.isoweekday() % 7, weekday_spec)  # 0=Sun
        ):
            return candidate
        candidate += timedelta(minutes=1)

    # Fallback: next day same time
    return after + timedelta(days=1)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ScheduleManager:
    """Manages scheduled task records."""

    def __init__(self, storage: StorageBackend, notifier=None) -> None:
        self._storage = storage
        self._notifier = notifier

    def add(
        self,
        workflow_id: str,
        schedule: str,
        inputs: dict[str, Any] | None = None,
        user_id: str = "default",
        budget_per_run: float | None = None,
    ) -> str:
        """Add a new scheduled task. Returns task ID."""
        task_id = str(uuid.uuid4())
        next_run = parse_next_run(schedule)
        self._storage.execute(
            """INSERT INTO scheduled_tasks
               (id, workflow_id, user_id, schedule, inputs, next_run, budget_per_run)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                workflow_id,
                user_id,
                schedule,
                json.dumps(inputs) if inputs else None,
                next_run.isoformat(),
                budget_per_run,
            ),
        )
        if self._notifier:
            self._notifier.notify_change(f"Schedule added: {workflow_id} ({schedule})", "schedule_add")
        return task_id

    def get(self, task_id: str) -> dict[str, Any] | None:
        """Get a single scheduled task by ID."""
        row = self._storage.fetchone(
            "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
        )
        if row is None:
            return None
        return self._parse_row(row)

    def list_tasks(
        self,
        status: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List scheduled tasks, optionally filtered by status and/or user_id."""
        conditions: list[str] = []
        params: list[Any] = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._storage.fetchall(
            f"SELECT * FROM scheduled_tasks{where} ORDER BY next_run",
            tuple(params),
        )
        return [self._parse_row(r) for r in rows]

    def get_due_tasks(self) -> list[dict[str, Any]]:
        """Get tasks that are due for execution (next_run <= now)."""
        now = datetime.now(timezone.utc).isoformat()
        rows = self._storage.fetchall(
            "SELECT * FROM scheduled_tasks WHERE status = 'active' AND next_run <= ? ORDER BY next_run",
            (now,),
        )
        return [self._parse_row(r) for r in rows]

    def mark_executed(self, task_id: str) -> None:
        """Update last_run, increment run_count, calculate next_run."""
        task = self.get(task_id)
        if task is None:
            return
        now = datetime.now(timezone.utc)
        next_run = parse_next_run(task["schedule"], after=now)
        self._storage.execute(
            """UPDATE scheduled_tasks SET
               last_run = ?, next_run = ?, run_count = run_count + 1
               WHERE id = ?""",
            (now.isoformat(), next_run.isoformat(), task_id),
        )

    def pause(self, task_id: str) -> None:
        """Pause a scheduled task."""
        self._storage.execute(
            "UPDATE scheduled_tasks SET status = 'paused' WHERE id = ?",
            (task_id,),
        )
        if self._notifier:
            self._notifier.notify_change(f"Schedule paused: {task_id}", "schedule_pause")

    def resume(self, task_id: str) -> None:
        """Resume a paused task and recalculate next_run from now."""
        task = self.get(task_id)
        schedule = task["schedule"] if task else "* * * * *"
        next_run = parse_next_run(schedule)
        self._storage.execute(
            "UPDATE scheduled_tasks SET status = 'active', next_run = ? WHERE id = ?",
            (next_run.isoformat(), task_id),
        )
        if self._notifier:
            self._notifier.notify_change(f"Schedule resumed: {task_id}", "schedule_resume")

    def delete(self, task_id: str) -> None:
        """Delete a scheduled task."""
        self._storage.execute(
            "DELETE FROM scheduled_tasks WHERE id = ?",
            (task_id,),
        )
        if self._notifier:
            self._notifier.notify_change(f"Schedule deleted: {task_id}", "schedule_delete")

    def _parse_row(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        if result.get("inputs"):
            try:
                result["inputs"] = json.loads(result["inputs"])
            except (json.JSONDecodeError, TypeError):
                pass
        return result
