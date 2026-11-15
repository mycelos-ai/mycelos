"""/run, resuming paused workflow runs and pending-run context.

Mixin for :class:`mycelos.chat.service.ChatService`; split out of
``chat/service.py`` without behavior changes.
"""

from __future__ import annotations

import logging

from mycelos.chat.events import (
    ChatEvent,
    agent_event,
    done_event,
    error_event,
    step_progress_event,
    text_event,
)

_log = logging.getLogger("mycelos.chat.service")


class WorkflowCommandsMixin:
    """/run, resuming paused workflow runs and pending-run context."""

    def _handle_run_command(
        self, message: str, session_id: str, events: list[ChatEvent],
    ) -> list[ChatEvent]:
        """Handle /run <workflow> deterministically with progress + session persistence."""
        import uuid

        from mycelos.chat.events import (
            done_event,
            step_progress_event,
            system_response_event,
            text_event,
        )

        parts = message.strip().split(None, 2)  # ["/run", workflow_id, ...]
        if len(parts) < 2:
            # List available workflows
            workflows = self._app.workflow_registry.list_workflows()
            if not workflows:
                resp = "No workflows registered. Create one first."
            else:
                lines = ["**Available workflows:**\n"]
                for wf in workflows:
                    lines.append(f"  `/run {wf['id']}` -- {wf.get('description', wf.get('name', wf['id']))}")
                resp = "\n".join(lines)
            events.append(system_response_event(resp))
            self._app.session_store.append_message(
                session_id, role="assistant", content=resp,
                metadata={"agent": "System"},
            )
            events.append(done_event())
            return events

        workflow_id = parts[1]
        workflow_def = self._app.workflow_registry.get(workflow_id)
        if not workflow_def:
            resp = f"Workflow `{workflow_id}` not found. Use `/run` to see available workflows."
            events.append(system_response_event(resp))
            self._app.session_store.append_message(
                session_id, role="assistant", content=resp,
                metadata={"agent": "System"},
            )
            events.append(done_event())
            return events

        # Parse key=value inputs from remaining args
        from mycelos.chat.slash_commands import _parse_run_inputs
        raw_args = parts[2].split() if len(parts) > 2 else []
        inputs = _parse_run_inputs(raw_args)

        # Execute workflow with progress callback
        from mycelos.workflows.agent import WorkflowAgent

        run_id = str(uuid.uuid4())[:16]
        try:
            agent = WorkflowAgent(
                app=self._app, workflow_def=workflow_def, run_id=run_id,
                session_id=session_id,
            )
        except ValueError as exc:
            resp = f"Cannot run workflow `{workflow_id}`: {exc}"
            events.append(system_response_event(resp))
            self._app.session_store.append_message(
                session_id, role="assistant", content=resp,
                metadata={"agent": "System"},
            )
            events.append(done_event())
            return events

        wf_name = workflow_def.get("name", workflow_id)
        events.append(step_progress_event(wf_name, "running"))

        def _on_progress(step_id: str, status: str) -> None:
            events.append(step_progress_event(step_id, status))

        exec_result = agent.execute(inputs=inputs, on_progress=_on_progress)

        resp_parts = [f"**Workflow:** {wf_name}"]
        if exec_result.status == "completed":
            if exec_result.result:
                resp_parts.append(f"\n{exec_result.result}")
            else:
                resp_parts.append("Completed successfully.")
        elif exec_result.status == "needs_clarification":
            resp_parts.append(f"\n**Question:** {exec_result.clarification}")
        else:
            resp_parts.append(f"**Status:** {exec_result.status}")
            if exec_result.error:
                resp_parts.append(f"**Error:** {exec_result.error}")

        if exec_result.cost and exec_result.cost > 0:
            resp_parts.append(f"\n_Cost: ${exec_result.cost:.4f}_")

        resp = "\n".join(resp_parts)
        events.append(text_event(resp))
        self._app.session_store.append_message(
            session_id, role="assistant", content=resp,
        )
        events.append(done_event(cost=exec_result.cost))
        return events

    def _resume_workflow(self, run: dict, user_answer: str, session_id: str) -> list[ChatEvent]:
        """Resume a paused workflow with the user's answer."""
        from mycelos.workflows.agent import WorkflowAgent

        events: list[ChatEvent] = [agent_event("Mycelos")]
        run_id = run["id"]
        wf_name = run.get("workflow_name", run.get("workflow_id", "workflow"))
        events.append(step_progress_event(wf_name, "running"))

        try:
            agent = WorkflowAgent.from_run(self._app, run_id)
            if agent is None:
                events.append(error_event(f"Could not restore workflow run {run_id[:8]}"))
                events.append(done_event())
                return events

            # Transition run status back to running
            try:
                self._app.workflow_run_manager.resume(run_id)
            except Exception:
                _log.warning("Failed to transition run %s to running", run_id, exc_info=True)

            result = agent.resume(user_answer)

            if result.status == "completed":
                self._app.session_store.append_message(
                    session_id, role="assistant", content=result.result
                )
                events.append(text_event(result.result))
                events.append(done_event(cost=result.cost))
            elif result.status == "needs_clarification":
                events.append(text_event(
                    f"The workflow needs more input:\n\n**{result.clarification}**"
                ))
                events.append(done_event(cost=result.cost))
            else:
                events.append(error_event(f"Workflow failed: {result.error}"))
                events.append(done_event())

        except Exception as exc:
            _log.error("Workflow resume error: %s", exc, exc_info=True)
            events.append(error_event("Failed to resume workflow. Check server logs for details."))
            events.append(done_event())

        return events

    def _get_pending_runs_context(self) -> str:
        """Check for paused/waiting workflow runs and build context string.

        Returns:
            Markdown-formatted context about pending workflows, or empty
            string if none exist or an error occurs.
        """
        try:
            pending = self._app.workflow_run_manager.get_pending_runs()
            if not pending:
                return ""
            lines = ["## Pending Workflows"]
            for run in pending:
                wf_name = run.get("workflow_name", run.get("workflow_id", "?"))
                status = run["status"]
                step = run.get("current_step", "?")
                cost = run.get("cost", 0.0)
                error = run.get("error", "")
                reason = ""
                if "budget" in error:
                    reason = " (budget exceeded)"
                elif "failed" in error:
                    reason = " (step failed)"
                lines.append(
                    f"- **{wf_name}**: {status}{reason} — letzter Step: {step}, Kosten: ${cost:.4f}"
                )
            lines.append(
                "\nMention these pending workflows to the user and ask whether they "
                "want to resume or abort them."
            )
            return "\n".join(lines)
        except Exception:
            return ""
