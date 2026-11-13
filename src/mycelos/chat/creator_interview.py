"""Creator-Agent interview sessions and the Creator pipeline.

Mixin for :class:`mycelos.chat.service.ChatService`; split out of
``chat/service.py`` without behavior changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mycelos.chat.events import (
    ChatEvent,
    agent_event,
    done_event,
    error_event,
    step_progress_event,
    text_event,
)

if TYPE_CHECKING:
    from mycelos.agents.agent_spec import AgentSpec
    from mycelos.agents.handoff import HandoffResult


class CreatorInterviewMixin:
    """Creator-Agent interview sessions and the Creator pipeline."""

    def _handle_interview_message(
        self, message: str, session_id: str, conversation: list[dict]
    ) -> list[ChatEvent]:
        """Continue an active Creator interview session.

        Shows [Creator-Agent] attribution. On completion, runs the
        Creator Pipeline and returns a HandoffResult to Mycelos.
        """
        from mycelos.agents.handoff import HandoffResult

        interview_data = self._interviews[session_id]
        engine = interview_data["engine"] if isinstance(interview_data, dict) else interview_data

        events: list[ChatEvent] = [agent_event("Creator-Agent")]
        result = engine.process_message(message)

        # Interview cancelled — return to Mycelos
        if result.cancelled or result.scope_exceeded:
            del self._interviews[session_id]

            handoff_result = HandoffResult(
                source_agent="creator",
                success=False,
                result_summary="Interview cancelled by user.",
                suggested_response=result.response,
            )

            self._app.audit.log("agent.handoff_return", details={
                "from": "creator", "to": "mycelos", "success": False,
            })

            # Mycelos announces return
            events.append(text_event(result.response))
            events.append(agent_event("Mycelos"))
            events.append(done_event())
            return events

        # Interview confirmed — run Creator Pipeline
        if result.confirmed and result.spec:
            del self._interviews[session_id]

            conversation.append({"role": "assistant", "content": result.response})
            self._app.session_store.append_message(
                session_id, role="assistant", content=result.response,
            )
            events.append(text_event(result.response))

            # Dispatch pipeline in background
            import dataclasses as _dc
            spec_dict = _dc.asdict(result.spec)
            task_id = self._app.task_runner.dispatch(
                task_type="creator_pipeline",
                payload=spec_dict,
                session_id=session_id,
                agent_id="creator",
            )

            # Queue Huey task if available
            if self._pipeline_task is not None:
                self._pipeline_task(task_id, spec_dict)

            self._app.audit.log("agent.handoff_return", details={
                "from": "creator", "to": "mycelos",
                "success": True,
                "background_task_id": task_id,
            })

            events.append(agent_event("Mycelos"))
            events.append(text_event(
                f"Building your agent in the background (Task {task_id[:8]})...\n"
                f"You can keep chatting. I'll notify you when it's done."
            ))
            events.append(done_event())
            return events

        # Interview continues — show next question
        conversation.append({"role": "assistant", "content": result.response})
        self._app.session_store.append_message(
            session_id, role="assistant", content=result.response,
        )
        events.append(text_event(result.response))
        events.append(done_event())
        return events

    def _run_creator_pipeline(self, spec: "AgentSpec") -> tuple[list[ChatEvent], "HandoffResult"]:
        """Run the Creator Pipeline and return events + HandoffResult."""
        from mycelos.agents.creator_pipeline import CreatorPipeline
        from mycelos.agents.handoff import HandoffResult

        events: list[ChatEvent] = []

        events.append(step_progress_event("creator-pipeline", "running"))
        pipeline = CreatorPipeline(self._app)
        result = pipeline.run(spec)
        events.append(step_progress_event("creator-pipeline", "done"))

        if result.success:
            scenarios = len(result.gherkin.split("Scenario:")) - 1
            handoff_result = HandoffResult(
                source_agent="creator",
                success=True,
                result_summary=f"Agent {result.agent_name} created with {scenarios} scenarios.",
                result_data={
                    "agent_name": result.agent_name,
                    "scenarios": scenarios,
                    "cost": result.cost,
                },
                suggested_response=(
                    f"Agent **{result.agent_name}** has been created and registered.\n"
                    f"- {scenarios} acceptance scenarios\n"
                    f"- Tests: passed\n"
                    f"- Audit: passed\n"
                    f"- Status: active"
                ),
            )
            events.append(done_event(cost=result.cost))
        elif result.paused:
            handoff_result = HandoffResult(
                source_agent="creator",
                success=False,
                needs_user_action=True,
                result_summary=f"Pipeline paused: {result.error}",
                suggested_response=f"The agent creation was paused: {result.error}\nShould I continue?",
            )
            events.append(done_event())
        else:
            handoff_result = HandoffResult(
                source_agent="creator",
                success=False,
                error=result.error,
                suggested_response=f"Agent creation failed: {result.error}",
            )
            events.append(error_event(f"Creator pipeline failed: {result.error}"))
            events.append(done_event())

        return events, handoff_result

    def _suggest_agent_name(self, description: str) -> str:
        """Suggest an agent name from the description."""
        import re

        words = re.findall(r'[a-zA-Z]+', description.lower())
        # Filter common stop words
        stops = {
            "ich", "will", "einen", "agent", "der", "die", "das", "ein", "eine",
            "create", "build", "make", "i", "want", "a", "an", "the", "that",
            "for", "about", "with", "from", "and", "oder", "und", "nach",
            "bitte", "kannst", "du", "mir", "mach", "erstell", "brauche",
        }
        meaningful = [w for w in words if w not in stops and len(w) > 2][:3]
        if not meaningful:
            meaningful = ["custom"]
        return "-".join(meaningful) + "-agent"
