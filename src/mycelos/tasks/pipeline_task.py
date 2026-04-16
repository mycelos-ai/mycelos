"""Background pipeline execution with step-level tracking."""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

logger = logging.getLogger("mycelos.pipeline")


def run_creator_pipeline_bg(app: Any, task_id: str, spec_dict: dict) -> str:
    """Run Creator Pipeline with step-level progress tracking.

    Called from Huey worker thread. Updates BackgroundTaskRunner
    with step progress and completion status.
    """
    from mycelos.agents.agent_spec import AgentSpec
    from mycelos.agents.creator_pipeline import CreatorPipeline

    runner = app.task_runner

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

    runner.start_task(task_id, total_steps=7)

    app.audit.log("bg_task.started", details={
        "task_id": task_id,
        "agent_name": spec.name,
    })

    try:
        pipeline = CreatorPipeline(app)

        # The pipeline.run() handles all steps internally.
        # We track at the pipeline level (not individual sub-steps).
        runner.update_step(task_id, "pipeline", "running")
        result = pipeline.run(spec, budget_limit=3.0)
        runner.update_step(task_id, "pipeline", "completed", cost=result.cost)

        if result.success:
            scenarios = len(result.gherkin.split("Scenario:")) - 1
            summary = (
                f"Agent '{result.agent_name}' created successfully.\n"
                f"Scenarios: {scenarios}, Tests: passed, Audit: passed"
            )
            runner.complete_task(task_id, result={
                "success": True,
                "agent_name": result.agent_name,
                "scenarios": scenarios,
                "cost": result.cost,
                "summary": summary,
            })
            app.audit.log("bg_task.completed", details={
                "task_id": task_id,
                "agent_name": result.agent_name,
                "cost": result.cost,
            })
            return summary
        else:
            runner.fail_task(task_id, error=result.error or "Pipeline failed")
            app.audit.log("bg_task.failed", details={
                "task_id": task_id,
                "error": result.error,
            })
            return f"Pipeline failed: {result.error}"

    except Exception as e:
        runner.fail_task(task_id, error=str(e))
        app.audit.log("bg_task.failed", details={
            "task_id": task_id,
            "error": str(e),
        })
        logger.error("Pipeline crashed for task %s: %s", task_id, e, exc_info=True)
        return f"Pipeline crashed: {e}"


def register_pipeline_tasks(huey: Any, app: Any) -> Any:
    """Register the background pipeline task with Huey.

    Returns the task function for dispatching.
    """
    @huey.task()
    def creator_pipeline_task(task_id: str, spec_dict: dict) -> str:
        return run_creator_pipeline_bg(app, task_id, spec_dict)

    return creator_pipeline_task
