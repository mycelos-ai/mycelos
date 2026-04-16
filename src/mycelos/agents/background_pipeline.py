"""Background Pipeline Execution — runs Creator Pipeline as async task.

When the Creator interview is confirmed, the pipeline runs in a
background thread via Huey. Progress is tracked via audit events
and the result is stored for retrieval.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("mycelos.creator")


def run_pipeline_background(app: Any, spec_dict: dict, session_id: str) -> str:
    """Run the Creator Pipeline in the background.

    Called from Huey worker thread. Writes progress to audit log
    and stores the result for the user to see.

    Args:
        app: Mycelos App instance.
        spec_dict: AgentSpec as dict (serializable for Huey).
        session_id: The chat session that requested this.

    Returns:
        Result summary string.
    """
    from mycelos.agents.agent_spec import AgentSpec
    from mycelos.agents.creator_pipeline import CreatorPipeline

    # Reconstruct AgentSpec from dict
    spec = AgentSpec(
        name=spec_dict.get("name", "custom-agent"),
        description=spec_dict.get("description", ""),
        use_case=spec_dict.get("use_case", ""),
        capabilities_needed=spec_dict.get("capabilities_needed", []),
        trigger=spec_dict.get("trigger", "on_demand"),
        model_tier=spec_dict.get("model_tier", "sonnet"),
        gherkin_scenarios=spec_dict.get("gherkin_scenarios", ""),
        user_language=spec_dict.get("user_language", "en"),
        effort=spec_dict.get("effort", "trivial"),
    )

    app.audit.log("pipeline.started", details={
        "agent_name": spec.name,
        "session_id": session_id[:8],
    })

    logger.info("Pipeline started for agent '%s' (session %s)", spec.name, session_id[:8])

    try:
        pipeline = CreatorPipeline(app)
        result = pipeline.run(spec, budget_limit=3.0)

        if result.success:
            summary = (
                f"Agent '{result.agent_name}' created successfully.\n"
                f"Gherkin scenarios: {len(result.gherkin.split('Scenario:')) - 1}\n"
                f"Tests: passed\nAudit: passed\nStatus: active"
            )
            app.audit.log("pipeline.completed", details={
                "agent_name": result.agent_name,
                "session_id": session_id[:8],
                "success": True,
                "cost": result.cost,
            })
            logger.info("Pipeline completed: agent '%s' registered", result.agent_name)

            # Store result for the user to see
            app.memory.set(
                "default", "system",
                f"pipeline.result.{session_id[:8]}",
                json.dumps({
                    "success": True,
                    "agent_name": result.agent_name,
                    "summary": summary,
                }),
                created_by="pipeline",
            )

            return summary
        else:
            error_msg = f"Pipeline failed: {result.error}"
            app.audit.log("pipeline.failed", details={
                "agent_name": spec.name,
                "session_id": session_id[:8],
                "error": result.error,
            })
            logger.error("Pipeline failed for '%s': %s", spec.name, result.error)

            app.memory.set(
                "default", "system",
                f"pipeline.result.{session_id[:8]}",
                json.dumps({
                    "success": False,
                    "agent_name": spec.name,
                    "error": result.error,
                }),
                created_by="pipeline",
            )

            return error_msg

    except Exception as e:
        error_msg = f"Pipeline crashed: {e}"
        app.audit.log("pipeline.crashed", details={
            "agent_name": spec.name,
            "session_id": session_id[:8],
            "error": str(e),
        })
        logger.error("Pipeline crashed for '%s': %s", spec.name, e, exc_info=True)
        return error_msg


def register_pipeline_task(huey: Any, app: Any) -> Any:
    """Register the pipeline task with Huey.

    Returns the task function so it can be called with .schedule() or direct invocation.
    """
    @huey.task()
    def creator_pipeline_task(spec_dict: dict, session_id: str) -> str:
        return run_pipeline_background(app, spec_dict, session_id)

    return creator_pipeline_task
