"""WorkflowRunManager — tracks execution state of workflow runs."""

from __future__ import annotations

import json
import uuid
from typing import Any

from mycelos.protocols import StorageBackend

VALID_STATUSES = {"running", "paused", "waiting_input", "completed", "failed", "aborted"}

VALID_TRANSITIONS: dict[str, set[str]] = {
    "running": {"paused", "waiting_input", "completed", "failed"},
    "paused": {"running", "aborted"},
    "waiting_input": {"running", "aborted"},
}


class WorkflowRunManager:
    """Manages workflow execution runs with state tracking.

    Handles lifecycle of workflow runs including starting, pausing,
    resuming, completing, failing, and aborting. Tracks step progress,
    artifacts, costs, and budget limits.
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def start(
        self,
        workflow_id: str,
        task_id: str | None = None,
        user_id: str = "default",
        budget_limit: float | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """Start a new workflow run.

        Args:
            workflow_id: ID of the workflow to run.
            task_id: Optional associated task ID.
            user_id: Owner of the run.
            budget_limit: Optional maximum cost allowed.
            run_id: Optional pre-assigned run ID (auto-generated if not provided).
            session_id: Optional chat session that initiated this run. Used to
                link runs back to the originating session in the admin UI. Stays
                null for headless/scheduled runs.

        Returns:
            The new run ID.
        """
        run_id = run_id or str(uuid.uuid4())
        self._storage.execute(
            """INSERT INTO workflow_runs
               (id, workflow_id, task_id, user_id, budget_limit, completed_steps,
                artifacts, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, workflow_id, task_id, user_id, budget_limit, "[]", "{}", session_id),
        )
        return run_id

    def get(self, run_id: str) -> dict | None:
        """Get a run with parsed JSON fields.

        Args:
            run_id: The run to retrieve.

        Returns:
            Run dict with parsed JSON fields, or None if not found.
        """
        row = self._storage.fetchone(
            "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)
        )
        if row is None:
            return None
        return self._parse_row(row)

    def update_step(
        self,
        run_id: str,
        step_id: str,
        status: str = "done",
        artifacts: dict | None = None,
        cost: float = 0.0,
    ) -> None:
        """Update progress after a step completes or starts.

        Args:
            run_id: The run to update.
            step_id: The step that was executed.
            status: Step status — 'done' appends to completed_steps.
            artifacts: Optional artifacts to merge into the run.
            cost: Cost incurred by this step.

        Raises:
            ValueError: If the run does not exist.
        """
        run = self.get(run_id)
        if run is None:
            raise ValueError(f"Run '{run_id}' not found")

        completed: list[str] = run.get("completed_steps", [])
        if status == "done" and step_id not in completed:
            completed.append(step_id)

        current_artifacts: dict[str, Any] = run.get("artifacts", {})
        if artifacts:
            current_artifacts.update(artifacts)

        new_cost = run["cost"] + cost

        self._storage.execute(
            """UPDATE workflow_runs SET
               current_step = ?, completed_steps = ?, artifacts = ?,
               cost = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE id = ?""",
            (step_id, json.dumps(completed), json.dumps(current_artifacts),
             new_cost, run_id),
        )

    def pause(self, run_id: str, reason: str = "user") -> None:
        """Pause a running workflow.

        Args:
            run_id: The run to pause.
            reason: Why the run was paused.

        Raises:
            ValueError: If the run does not exist or transition is invalid.
        """
        self._transition(run_id, "paused", error=f"paused: {reason}")

    def resume(self, run_id: str) -> dict:
        """Resume a paused or waiting workflow.

        Args:
            run_id: The run to resume.

        Returns:
            The updated run state.

        Raises:
            ValueError: If the run does not exist or transition is invalid.
        """
        self._transition(run_id, "running")
        self._storage.execute(
            "UPDATE workflow_runs SET error = NULL WHERE id = ?", (run_id,)
        )
        result = self.get(run_id)
        assert result is not None  # Just transitioned, must exist
        return result

    def wait_for_input(self, run_id: str, prompt: str = "") -> None:
        """Pause workflow waiting for user input.

        Args:
            run_id: The run to pause.
            prompt: Description of what input is needed.

        Raises:
            ValueError: If the run does not exist or transition is invalid.
        """
        self._transition(run_id, "waiting_input", error=f"waiting: {prompt}")

    def complete(self, run_id: str) -> None:
        """Mark a run as completed.

        Args:
            run_id: The run to complete.

        Raises:
            ValueError: If the run does not exist or transition is invalid.
        """
        self._transition(run_id, "completed")

    def fail(self, run_id: str, error: str) -> None:
        """Mark a run as failed.

        Args:
            run_id: The run to mark as failed.
            error: Error message describing the failure.

        Raises:
            ValueError: If the run does not exist or transition is invalid.
        """
        self._transition(run_id, "failed", error=error)

    def abort(self, run_id: str) -> None:
        """User-initiated abort. Artifacts are preserved.

        Args:
            run_id: The run to abort.

        Raises:
            ValueError: If the run does not exist or cannot be aborted.
        """
        run = self.get(run_id)
        if run is None:
            raise ValueError(f"Run '{run_id}' not found")
        if run["status"] not in ("paused", "waiting_input"):
            raise ValueError(
                f"Cannot abort run in status '{run['status']}'. "
                f"Allowed from: paused, waiting_input"
            )
        self._storage.execute(
            """UPDATE workflow_runs SET status = 'aborted',
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE id = ?""",
            (run_id,),
        )

    def increment_retry(self, run_id: str) -> int:
        """Increment retry counter and return new count.

        Args:
            run_id: The run to increment.

        Returns:
            The new retry count.
        """
        self._storage.execute(
            """UPDATE workflow_runs SET retry_count = retry_count + 1,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE id = ?""",
            (run_id,),
        )
        run = self.get(run_id)
        return run["retry_count"] if run else 0

    def check_budget(self, run_id: str) -> bool:
        """Check if run has exceeded its budget.

        Args:
            run_id: The run to check.

        Returns:
            True if within budget (or no limit set), False if exceeded.
        """
        run = self.get(run_id)
        if run is None:
            return False
        if run["budget_limit"] is None:
            return True
        return run["cost"] <= run["budget_limit"]

    def list_runs(
        self,
        status: str | None = None,
        user_id: str | None = None,
        workflow_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """List runs with optional filters.

        Args:
            status: Filter by status.
            user_id: Filter by user.
            workflow_id: Filter by workflow.
            limit: Maximum number of rows to return (newest first).

        Returns:
            List of matching runs, newest first.
        """
        conditions: list[str] = []
        params: list[Any] = []
        if status:
            conditions.append("wr.status = ?")
            params.append(status)
        if user_id:
            conditions.append("wr.user_id = ?")
            params.append(user_id)
        if workflow_id:
            conditions.append("wr.workflow_id = ?")
            params.append(workflow_id)

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        limit_clause = " LIMIT ?" if limit is not None else ""
        if limit is not None:
            params.append(limit)
        rows = self._storage.fetchall(
            f"""SELECT wr.*, w.name as workflow_name
                FROM workflow_runs wr
                LEFT JOIN workflows w ON wr.workflow_id = w.id
                {where}
                ORDER BY wr.created_at DESC{limit_clause}""",
            tuple(params),
        )
        return [self._parse_row(r) for r in rows]

    def list_runs_by_session(self, session_id: str) -> list[dict]:
        """Return all runs that were started from a given chat session.

        Args:
            session_id: The session id to look up.

        Returns:
            List of runs joined with workflow name, newest first. Empty if no
            runs reference this session.
        """
        rows = self._storage.fetchall(
            """SELECT wr.*, w.name as workflow_name FROM workflow_runs wr
               LEFT JOIN workflows w ON wr.workflow_id = w.id
               WHERE wr.session_id = ?
               ORDER BY wr.created_at DESC""",
            (session_id,),
        )
        return [self._parse_row(r) for r in rows]

    def get_pending_runs(self, user_id: str = "default") -> list[dict]:
        """Get paused/waiting runs that need user attention.

        Args:
            user_id: The user whose pending runs to retrieve.

        Returns:
            List of runs in paused or waiting_input status.
        """
        rows = self._storage.fetchall(
            """SELECT wr.*, w.name as workflow_name FROM workflow_runs wr
               JOIN workflows w ON wr.workflow_id = w.id
               WHERE wr.user_id = ? AND wr.status IN ('paused', 'waiting_input')
               ORDER BY wr.updated_at DESC""",
            (user_id,),
        )
        return [self._parse_row(r) for r in rows]

    def list_scheduled(self) -> list[dict]:
        """List active scheduled tasks joined with their workflow name.

        Returns only tasks with status='active' — paused/disabled cron
        entries are hidden. Ordered by next_run ascending so the sidebar
        shows the next upcoming run first.
        """
        rows = self._storage.fetchall(
            """SELECT st.id, st.workflow_id, st.schedule, st.next_run, st.status,
                      w.name as workflow_name
               FROM scheduled_tasks st
               LEFT JOIN workflows w ON st.workflow_id = w.id
               WHERE st.status = 'active'
               ORDER BY st.next_run ASC"""
        )
        return [dict(r) for r in rows]

    def get_completed_unnotified(self, user_id: str = "default") -> list[dict]:
        """Get completed/failed runs that haven't been notified yet."""
        rows = self._storage.fetchall(
            """SELECT wr.*, w.name as workflow_name FROM workflow_runs wr
               LEFT JOIN workflows w ON wr.workflow_id = w.id
               WHERE wr.user_id = ? AND wr.status IN ('completed', 'failed')
               AND wr.notified_at IS NULL
               ORDER BY wr.created_at ASC""",
            (user_id,),
        )
        return [self._parse_row(r) for r in rows]

    def mark_notified(self, run_id: str) -> None:
        """Record notification timestamp."""
        self._storage.execute(
            "UPDATE workflow_runs SET notified_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (run_id,),
        )

    def _transition(
        self, run_id: str, new_status: str, error: str | None = None
    ) -> None:
        """Transition a run to a new status atomically.

        Uses a conditional UPDATE (WHERE status IN ...) to avoid the
        read-modify-write race of SELECT-then-UPDATE.

        Args:
            run_id: The run to transition.
            new_status: Target status.
            error: Optional error message to store.

        Raises:
            ValueError: If the run does not exist or transition is invalid.
        """
        allowed_from = [k for k, v in VALID_TRANSITIONS.items() if new_status in v]
        if not allowed_from:
            raise ValueError(f"No valid transitions to '{new_status}'")

        placeholders = ",".join("?" for _ in allowed_from)
        params: list[Any] = [new_status]
        if error is not None:
            sql = f"""UPDATE workflow_runs SET status = ?, error = ?,
                      updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                      WHERE id = ? AND status IN ({placeholders})"""
            params.extend([error, run_id] + allowed_from)
        else:
            sql = f"""UPDATE workflow_runs SET status = ?,
                      updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                      WHERE id = ? AND status IN ({placeholders})"""
            params.extend([run_id] + allowed_from)

        cursor = self._storage.execute(sql, tuple(params))
        if cursor.rowcount == 0:
            # Either run doesn't exist or status doesn't allow this transition
            run = self.get(run_id)
            if run is None:
                raise ValueError(f"Run '{run_id}' not found")
            raise ValueError(
                f"Cannot transition run '{run_id}' from '{run['status']}' to '{new_status}'"
            )

    def _parse_row(self, row: dict) -> dict:
        """Parse JSON fields from a DB row.

        Args:
            row: Raw database row dict.

        Returns:
            Row with completed_steps and artifacts parsed from JSON.
        """
        result = dict(row)
        if result.get("completed_steps"):
            try:
                result["completed_steps"] = json.loads(result["completed_steps"])
            except (json.JSONDecodeError, TypeError):
                result["completed_steps"] = []
        else:
            result["completed_steps"] = []
        if result.get("artifacts"):
            try:
                result["artifacts"] = json.loads(result["artifacts"])
            except (json.JSONDecodeError, TypeError):
                result["artifacts"] = {}
        else:
            result["artifacts"] = {}
        return result
