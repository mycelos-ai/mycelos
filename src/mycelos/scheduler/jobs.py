"""Scheduled jobs -- workflow execution, session summaries, cleanup.

These functions are registered as Huey periodic tasks in the Gateway.
They can also be called directly for testing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("mycelos.scheduler")


def reminder_tick_check(app: Any) -> dict[str, int]:
    """Run one pass of the reminder scheduler.

    Drives :meth:`mycelos.knowledge.reminder.ReminderService.check_and_notify`
    so every reminder with ``remind_at`` in the past (or ``due`` in the
    past when ``remind_at`` is null) gets dispatched exactly once. Also
    emits a ``reminder.tick`` audit event so the doctor can tell whether
    the scheduler is actually running — the absence of recent ticks is
    the clearest signal that the Huey consumer is dead.
    """
    from mycelos.knowledge.reminder import ReminderService
    try:
        result = ReminderService(app).check_and_notify()
    except Exception as e:
        logger.error("reminder_tick_check failed: %s", e, exc_info=True)
        result = {"tasks_found": 0, "notifications_sent": 0, "error": str(e)}
    try:
        app.audit.log("reminder.tick", details=result)
    except Exception:
        pass
    return result


def auto_ingest_check(app: Any, user_id: str = "default") -> dict[str, Any]:
    """Run one pass of the living-knowledge auto-ingest.

    Runs every registered INGEST_SOURCES connector IF the user opted in
    (memory key ``auto_ingest_enabled``, default OFF) and the connector
    is active in the registry. Errors in one connector never crash the
    loop — they are recorded and the next connector still runs.
    """
    from mycelos.knowledge.connector_ingest import INGEST_SOURCES

    summary: dict[str, Any] = {
        "enabled": False, "ran": {}, "skipped": [], "errors": {},
    }
    try:
        enabled = app.memory.get(user_id, "system", "auto_ingest_enabled")
    except Exception as e:
        logger.error("auto_ingest_check: memory read failed: %s", e)
        return summary
    if not enabled:
        return summary
    summary["enabled"] = True

    for name, ingest_fn in INGEST_SOURCES.items():
        try:
            connector = app.connector_registry.get(name)
        except Exception as e:
            logger.warning("auto-ingest: registry lookup for %s failed: %s", name, e)
            connector = None
        if not connector or connector.get("status") != "active":
            summary["skipped"].append(name)
            continue
        try:
            result = ingest_fn(app, user_id=user_id)
            if result.get("error"):
                summary["errors"][name] = str(result["error"])
            else:
                summary["ran"][name] = {
                    "created": result.get("created", 0),
                    "skipped_existing": result.get("skipped_existing", 0),
                }
        except Exception as e:
            logger.error("auto-ingest for %s FAILED: %s", name, e, exc_info=True)
            summary["errors"][name] = str(e)

    try:
        app.audit.log("knowledge.auto_ingest.run", user_id=user_id,
                      details=summary)
    except Exception:
        pass
    return summary


def briefing_tick(
    app: Any,
    user_id: str = "default",
    now: "datetime | None" = None,
    reminder_service: Any = None,
) -> dict[str, Any]:
    """Deliver the morning briefing once per day at the configured time.

    Called every 5 minutes by the scheduler. Sends when all of:
    ``briefing_enabled`` is set (default OFF), the local time has passed
    ``briefing_time`` (default 07:30), and ``briefing_last_sent`` is not
    today. Must never raise — the scheduler loop stays alive.
    """
    from mycelos.knowledge.briefing import (
        DEFAULT_BRIEFING_TIME,
        deliver_briefing,
        is_briefing_due,
    )

    try:
        if not app.memory.get(user_id, "system", "briefing_enabled"):
            return {"enabled": False, "sent": False}

        now = now or datetime.now()  # local time — briefing_time is local
        briefing_time = (
            app.memory.get(user_id, "system", "briefing_time")
            or DEFAULT_BRIEFING_TIME
        )
        last_sent = app.memory.get(user_id, "system", "briefing_last_sent")
        if not is_briefing_due(now, briefing_time, last_sent):
            return {"enabled": True, "sent": False, "reason": "not_due"}

        result = deliver_briefing(app, user_id, reminder_service=reminder_service, now=now)
        return {"enabled": True, **result}
    except Exception as e:
        logger.error("briefing_tick FAILED: %s", e, exc_info=True)
        return {"sent": False, "error": str(e)}


ORPHANED_RUN_CAUSE = (
    "The run did not finish and its outcome is unknown. It was still marked "
    "running when the system next started."
)


def sweep_orphaned_workflow_runs(app: Any) -> int:
    """Mark runs stuck in 'running' as failed, with an honest cause.

    A row still 'running' at startup tells us one thing only: the run did
    not finish. A crash, a kill and a clean restart are indistinguishable
    here. The old message claimed a restart, which sent the reader looking
    at infrastructure when the real cause was usually a crash. A wrong cause
    is worse than no cause, so this states what is actually known.

    Runs that recorded their own cause are already 'failed' and untouched.
    """
    try:
        cursor = app.storage.execute(
            """UPDATE workflow_runs SET status = 'failed', error = ?
            WHERE status = 'running'""",
            (ORPHANED_RUN_CAUSE,),
        )
        count = cursor.rowcount
        if count > 0:
            logger.warning("Recovered %d orphaned workflow run(s)", count)
        return count
    except Exception:
        return 0


def register_periodic_jobs(huey: Any, app: Any) -> None:
    """Register all periodic tasks with the Huey instance.

    Must be called before the consumer starts.
    """
    sweep_orphaned_workflow_runs(app)

    from huey import crontab

    @huey.periodic_task(crontab(minute="*/5"))
    def scheduled_workflow_check() -> None:
        check_scheduled_workflows(app)

    @huey.periodic_task(crontab(minute="*/5"))
    def session_summary_check() -> None:
        from mycelos.scheduler.session_summary import process_stale_sessions
        process_stale_sessions(app)

    @huey.periodic_task(crontab(minute="0", hour="3"))  # Daily at 3 AM
    def memory_cleanup() -> None:
        """Clean up expired memory entries."""
        count = app.memory.cleanup_expired()
        if count > 0:
            logger.info("Cleaned up %d expired memory entries", count)

    @huey.periodic_task(crontab(minute="*/5"))
    def sweep_stale_bg_tasks() -> None:
        """Cancel background tasks that exceeded their timeout."""
        sweep_stale_background_tasks(app)

    @huey.periodic_task(crontab(minute="*/1"))
    def deliver_workflow_notifications() -> None:
        notify_completed_workflows(app)

    @huey.periodic_task(crontab(minute="*/1"))  # Every minute
    def reminder_tick() -> None:
        """Dispatch any reminders that have become due since the last tick."""
        reminder_tick_check(app)

    @huey.periodic_task(crontab(hour="3", minute="0"))  # Daily at 03:00 UTC
    def model_update_check() -> None:
        """Refresh the LLM model registry once per day.

        Deterministic handler — no LLM calls. Fetches LiteLLM's remote
        model-cost JSON so newly-released provider models appear in
        Settings without a litellm pip upgrade.
        """
        try:
            result = app.model_updater.run("default")
            if result.get("added"):
                logger.info(
                    "Model update: %d new models discovered (%s)",
                    len(result["added"]),
                    ", ".join(result["added"][:5]),
                )
        except Exception as e:
            logger.error("Model update check FAILED: %s", e, exc_info=True)

    @huey.periodic_task(crontab(minute="0"))  # Hourly
    def auto_ingest_periodic() -> None:
        """Living knowledge: pull fresh content from connected services.

        No-op unless the user enabled auto-ingest (memory key
        ``auto_ingest_enabled``) — the check is a single memory read.
        """
        auto_ingest_check(app)

    @huey.periodic_task(crontab(minute="*/5"))  # Every 5 minutes
    def briefing_periodic() -> None:
        """Deliver the morning briefing when its time window opens."""
        briefing_tick(app)

    @huey.periodic_task(crontab(minute="*/5"))  # Every 5 minutes
    def knowledge_organizer_periodic() -> None:
        """Run the lazy organizer if there are pending notes.

        Checks every 5 minutes. Only calls the LLM if there are actually
        pending notes, so the cost is zero when the queue is empty.
        """
        try:
            # Quick check: skip if nothing pending (no LLM cost)
            pending = app.storage.fetchone(
                "SELECT COUNT(*) AS c FROM knowledge_notes "
                "WHERE organizer_state='pending'"
            )
            if not pending or pending["c"] == 0:
                return

            logger.info("Organizer: %d pending notes, starting run", pending["c"])
            result = app.knowledge_organizer.run("default")
            logger.info(
                "Organizer run complete: %s",
                ", ".join(f"{k}={v}" for k, v in result.items()),
            )
        except Exception as e:
            logger.error("Organizer periodic run FAILED: %s", e, exc_info=True)


def execute_background_workflow(
    app: Any,
    workflow_id: str,
    inputs: dict[str, Any] | None = None,
    user_id: str = "default",
) -> str:
    """Execute a workflow in a background thread. Returns run_id immediately."""
    import threading
    import uuid

    from mycelos.workflows.agent import WorkflowAgent

    workflow = app.workflow_registry.get(workflow_id)
    if not workflow or not workflow.get("plan"):
        raise ValueError(f"Workflow '{workflow_id}' not found or has no plan")

    run_id = str(uuid.uuid4())[:16]

    def _run():
        try:
            agent = WorkflowAgent(app=app, workflow_def=workflow, run_id=run_id)
            result = agent.execute(inputs=inputs)
            app.audit.log("background_workflow.completed", details={
                "workflow_id": workflow_id,
                "run_id": run_id,
                "status": result.status,
            })
        except Exception as e:
            logger.error("Background workflow '%s' failed: %s", workflow_id, e)
            app.audit.log("background_workflow.failed", details={
                "workflow_id": workflow_id,
                "run_id": run_id,
                "error": str(e),
            })

    thread = threading.Thread(target=_run, daemon=True, name=f"wf-{run_id}")
    thread.start()
    return run_id


def _record_scheduled_failure(
    app: Any,
    run_id: str,
    workflow_id: str,
    task_id: str,
    cause: str,
) -> None:
    """Make sure a failed scheduled run leaves a row that says so.

    `scheduled_tasks` has no outcome column — `run_count` increments the same
    way for success and failure — so the run row is the only durable trace.
    The agent records its own failures, but three paths never reach it: the
    workflow has no plan, the agent could not be constructed, and the agent
    returned a non-completed status without writing one.

    Never raises: bookkeeping must not break the scheduler loop, which still
    has to mark the task executed to avoid a retry storm.

    Note: `task_id` is not stored on the row. `workflow_runs.task_id`
    references `tasks(id)`, and a scheduled task lives in `scheduled_tasks` —
    passing it there fails the foreign key. It is used for logging only.

    Args:
        app: The application.
        run_id: The run this attempt used.
        workflow_id: The scheduled workflow.
        task_id: The scheduled task, for the log line only.
        cause: User-facing cause. No traceback, no paths, no content.
    """
    try:
        existing = app.workflow_run_manager.get(run_id)
        if existing is None:
            app.workflow_run_manager.start(
                workflow_id=workflow_id, run_id=run_id,
            )
            existing = app.workflow_run_manager.get(run_id)
        if existing is not None and existing.get("status") == "running":
            app.workflow_run_manager.fail(run_id, error=cause)
        app.storage.execute(
            "UPDATE workflow_runs SET kind = 'scheduled_task' WHERE id = ?",
            (run_id,),
        )
    except Exception:
        logger.warning(
            "Could not record failure for scheduled run %s (task %s)",
            run_id, task_id[:8], exc_info=True,
        )


def check_scheduled_workflows(app: Any) -> list[str]:
    """Check for due scheduled workflows and execute them.

    Every attempt that ends leaves a run row. A failure records a cause;
    the task is still marked executed so a broken workflow cannot spin.

    Returns list of executed task IDs.
    """
    import uuid

    from mycelos.workflows.run_cause import describe_exception

    due = app.schedule_manager.get_due_tasks()
    executed: list[str] = []

    for task in due:
        workflow_id: str = task["workflow_id"]
        task_id: str = task["id"]
        inputs: dict[str, Any] = task.get("inputs") or {}
        budget: float | None = task.get("budget_per_run")

        logger.info(
            "Executing scheduled workflow: %s (task: %s)",
            workflow_id,
            task_id[:8],
        )

        run_id = str(uuid.uuid4())[:16]

        try:
            workflow = app.workflow_registry.get(workflow_id)
            if workflow is None:
                # No workflow means no run row can reference it — the task
                # itself is the broken thing, and it is not executed.
                logger.warning(
                    "Workflow '%s' not found for scheduled task %s",
                    workflow_id,
                    task_id[:8],
                )
                continue

            # Execute via WorkflowAgent
            from mycelos.workflows.agent import WorkflowAgent

            plan = workflow.get("plan")
            if not plan:
                # Used to `continue` silently: no row, no error, no trace.
                logger.warning(
                    "Workflow '%s' has no plan, skipping", workflow_id
                )
                _record_scheduled_failure(
                    app, run_id, workflow_id, task_id,
                    "The workflow has no plan, so there was nothing to run. "
                    "Re-register the workflow with a plan.",
                )
                app.schedule_manager.mark_executed(task_id)
                executed.append(task_id)
                continue

            agent = WorkflowAgent(
                app=app,
                workflow_def=workflow,
                run_id=run_id,
            )
            result = agent.execute(inputs=inputs)

            # Mark as executed (updates next_run)
            app.schedule_manager.mark_executed(task_id)
            executed.append(task_id)

            if result.status == "completed":
                logger.info(
                    "Scheduled workflow '%s' completed successfully", workflow_id
                )
            elif result.status == "needs_clarification":
                # A cron run is headless. Nobody is there to answer, so a
                # question is not a pause — it is the end of the run, and the
                # cause is that it needed an answer it could not get. The
                # question text is the LLM's, built from the run's data, so it
                # never reaches the row.
                logger.warning(
                    "Scheduled workflow '%s' stopped to ask a question", workflow_id
                )
                _record_scheduled_failure(
                    app, run_id, workflow_id, task_id,
                    "The workflow stopped to ask a question, but a scheduled "
                    "run has nobody to answer it. Give the workflow the "
                    "missing input, or run it from chat.",
                )
            else:
                # 'failed' and any status a future version of execute() adds.
                # An unrecognised ending is still an ending; recording it with
                # a generic cause beats leaving no row at all.
                logger.warning(
                    "Scheduled workflow '%s' failed: %s", workflow_id, result.error
                )
                _record_scheduled_failure(
                    app, run_id, workflow_id, task_id,
                    result.error or "The workflow run failed without a cause.",
                )

            # Audit log
            app.audit.log(
                "scheduled.executed",
                details={
                    "task_id": task_id,
                    "workflow_id": workflow_id,
                    "success": result.status == "completed",
                },
            )

        except Exception as e:
            logger.error("Error executing scheduled task %s: %s", task_id[:8], e)
            _record_scheduled_failure(
                app, run_id, workflow_id, task_id, describe_exception(e),
            )
            # Still mark as executed to prevent infinite retry
            app.schedule_manager.mark_executed(task_id)
            executed.append(task_id)

    return executed


def notify_completed_workflows(app: Any) -> int:
    """Send notifications for completed workflow runs. Returns count notified."""
    try:
        unnotified = app.workflow_run_manager.get_completed_unnotified()
    except Exception:
        return 0

    count = 0
    for run in unnotified:
        wf_name = run.get("workflow_name") or run.get("workflow_id", "workflow")
        status = run["status"]

        # The full result lives in artifacts["result"], not in a top-level field
        artifacts = run.get("artifacts") or {}
        full_result = artifacts.get("result", "") if isinstance(artifacts, dict) else ""
        error_text = run.get("error", "") or ""

        if status == "completed" and full_result:
            # Send the actual result — that's what the user wants to see
            message = full_result
        elif status == "failed" and error_text:
            message = f"Workflow '{wf_name}' failed:\n{error_text[:500]}"
        else:
            message = f"Workflow '{wf_name}' {status}."

        # Try Telegram notification — only mark as notified on success
        sent = False
        try:
            from mycelos.channels.telegram import send_notification
            sent = send_notification(app, message)
        except Exception as exc:
            logger.warning("Telegram notification error for run %s: %s", run["id"], exc)

        if sent:
            app.workflow_run_manager.mark_notified(run["id"])
            app.audit.log("workflow.notified", details={
                "run_id": run["id"],
                "workflow_id": run.get("workflow_id"),
                "status": status,
            })
            count += 1
        else:
            logger.warning(
                "Notification not delivered for run %s — will retry next cycle",
                run["id"],
            )

    if count:
        logger.info("Notified %d completed workflow run(s)", count)
    return count


def sweep_stale_background_tasks(app: Any) -> int:
    """Find and fail background tasks that have exceeded their timeout.

    Returns number of tasks failed.
    """
    now = datetime.now(timezone.utc)

    # Get all running tasks
    running = app.task_runner.get_tasks_for_user("default", status="running")
    failed_count = 0

    for task in running:
        started = task.get("started_at")
        timeout = task.get("timeout_seconds", 600) or 600

        if not started:
            continue

        try:
            # Parse ISO timestamp
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            elapsed = (now - started_dt).total_seconds()

            if elapsed > timeout:
                app.task_runner.fail_task(
                    task["id"],
                    error=f"Task timed out after {int(elapsed)}s (limit: {timeout}s)",
                )
                app.audit.log("bg_task.timeout", details={
                    "task_id": task["id"],
                    "elapsed": int(elapsed),
                    "timeout": timeout,
                })
                logger.warning(
                    "Background task %s timed out (%ds > %ds)",
                    task["id"][:8], int(elapsed), timeout,
                )
                failed_count += 1
        except (ValueError, TypeError):
            continue

    # Also check waiting_for_user tasks — auto-cancel after 24 hours
    waiting = app.task_runner.get_tasks_for_user("default", status="waiting_for_user")
    for task in waiting:
        created = task.get("created_at")
        if not created:
            continue
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            elapsed = (now - created_dt).total_seconds()
            if elapsed > 86400:  # 24 hours
                app.task_runner.cancel(task["id"])
                logger.info("Auto-cancelled waiting task %s after 24h", task["id"][:8])
                failed_count += 1
        except (ValueError, TypeError):
            continue

    if failed_count > 0:
        logger.info("Swept %d stale background tasks", failed_count)

    return failed_count
