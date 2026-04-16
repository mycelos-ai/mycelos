"""Workflow tools — create, schedule, and inspect workflows."""

from __future__ import annotations

import json
import uuid
from typing import Any

from mycelos.tools.registry import ToolPermission

# --- Tool Schemas ---

CREATE_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_workflow",
        "description": (
            "Create a new workflow definition. A workflow is an LLM-powered agent "
            "that executes a plan using scoped tools. Provide a clear plan (instructions "
            "for the LLM), the list of allowed_tools, a model tier, and optional inputs schema."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "Unique ID in kebab-case (e.g., 'daily-news-summary').",
                },
                "name": {
                    "type": "string",
                    "description": "Human-readable name.",
                },
                "description": {
                    "type": "string",
                    "description": "What the workflow does (1-2 sentences).",
                },
                "goal": {
                    "type": "string",
                    "description": "Desired outcome when workflow completes.",
                },
                "plan": {
                    "type": "string",
                    "description": (
                        "Detailed instructions for the LLM agent. This is the system prompt. "
                        "Include: what tools to call, in what order, how to handle errors, "
                        "and what output format to use. Be specific and actionable."
                    ),
                },
                "inputs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Parameter name (e.g., 'topic')"},
                            "type": {"type": "string", "description": "Parameter type: string, number, boolean"},
                            "required": {"type": "boolean", "description": "Whether this input is required"},
                            "description": {"type": "string", "description": "What this parameter is for"},
                        },
                        "required": ["name", "type", "description"],
                    },
                    "description": "Input parameters the workflow expects (e.g., topic, url, query).",
                },
                "model": {
                    "type": "string",
                    "enum": ["haiku", "sonnet", "opus"],
                    "description": "LLM model tier. Use haiku for simple tasks, sonnet for complex reasoning.",
                },
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Tools the workflow agent can use. Use exact tool names "
                        "(e.g., 'search_web', 'http_get', 'note_write') or wildcards "
                        "(e.g., 'playwright.*' for all playwright tools)."
                    ),
                },
                "steps": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Legacy step definitions. Prefer using 'plan' instead.",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "scope": {"type": "array", "items": {"type": "string"}},
                "success_criteria": {
                    "type": "string",
                    "description": (
                        "Natural language definition of when this workflow is successful. "
                        "E.g., 'At least 4 RSS feeds were fetched and a structured summary was produced.' "
                        "The system will verify this after execution and retry or fail if not met."
                    ),
                },
                "notification_mode": {
                    "type": "string",
                    "enum": ["result_only", "progress", "none"],
                    "description": (
                        "When to notify the user. 'result_only' (default): only send the final result. "
                        "'progress': send intermediate updates. 'none': silent background job."
                    ),
                },
            },
            "required": ["workflow_id", "name", "description", "plan", "allowed_tools", "success_criteria"],
        },
    },
}

CREATE_SCHEDULE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_schedule",
        "description": (
            "Create a scheduled task that runs a workflow on a cron schedule. "
            "Use this when the user wants something to happen regularly "
            "(daily, weekly, every morning, etc.). "
            "Results are delivered via Telegram if configured."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "The workflow to schedule (e.g., 'news-summary')",
                },
                "cron": {
                    "type": "string",
                    "description": (
                        "Cron expression: 'minute hour day month weekday'. "
                        "Examples: '0 7 * * *' (daily 7am), '0 9 * * 1-5' (weekdays 9am)"
                    ),
                },
            },
            "required": ["workflow_id", "cron"],
        },
    },
}

DELETE_SCHEDULE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_schedule",
        "description": (
            "Delete a scheduled task. Use list_schedules first to find the "
            "schedule ID. Use this when the user wants to stop a recurring workflow."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "schedule_id": {
                    "type": "string",
                    "description": "The schedule ID to delete (from list_schedules).",
                },
            },
            "required": ["schedule_id"],
        },
    },
}

LIST_SCHEDULES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_schedules",
        "description": (
            "List all scheduled tasks with their IDs, workflow names, cron expressions, "
            "and status. Use this to find schedule IDs before deleting or modifying them."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


def execute_delete_schedule(args: dict, context: dict) -> Any:
    """Delete a scheduled task."""
    app = context["app"]
    schedule_id = args.get("schedule_id", "")

    if not schedule_id:
        return {"error": "Missing schedule_id. Use list_schedules to find it."}

    try:
        tasks = app.schedule_manager.list_tasks()
        match = [t for t in tasks if t["id"].startswith(schedule_id)]
        if not match:
            return {
                "error": f"Schedule '{schedule_id}' not found.",
                "available": [{"id": t["id"][:8], "workflow": t["workflow_id"]} for t in tasks],
            }
        task = match[0]
        app.schedule_manager.delete(task["id"])

        app.config.apply_from_state(
            state_manager=app.state_manager,
            description=f"Schedule deleted: {task['workflow_id']}",
            trigger="schedule_delete",
        )
        app.audit.log("schedule.deleted", details={
            "task_id": task["id"], "workflow_id": task["workflow_id"],
        })

        return {
            "status": "deleted",
            "schedule_id": task["id"][:8],
            "workflow_id": task["workflow_id"],
        }
    except Exception as e:
        return {"error": f"Failed to delete schedule: {e}"}


def execute_list_schedules(args: dict, context: dict) -> Any:
    """List all scheduled tasks."""
    app = context["app"]
    tasks = app.schedule_manager.list_tasks()
    return [
        {
            "id": t["id"][:8],
            "workflow_id": t["workflow_id"],
            "schedule": t.get("schedule", ""),
            "status": t.get("status", ""),
            "next_run": t.get("next_run", ""),
        }
        for t in tasks
    ]


WORKFLOW_INFO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_info",
        "description": (
            "Get details of a workflow -- its plan, inputs, allowed tools, and description. "
            "Use system_status to list workflows first, then this to inspect one."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "The workflow ID to inspect",
                },
            },
            "required": ["workflow_id"],
        },
    },
}


# --- Tool Execution ---

def _ensure_list(value: Any) -> list | None:
    """Ensure a value is a list, parsing JSON strings if needed.

    LLMs sometimes pass '["a","b"]' (string) instead of ["a","b"] (array).
    This prevents double-serialization in the DB.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def execute_create_workflow(args: dict, context: dict) -> Any:
    """Create a new workflow."""
    app = context["app"]
    name = args.get("name", args.get("workflow_id", ""))
    description = args.get("description", "")
    steps = args.get("steps", [])
    workflow_id = args.get("workflow_id") or name
    goal = args.get("goal")
    tags = args.get("tags")
    scope = args.get("scope")
    plan = args.get("plan")
    model = args.get("model")
    allowed_tools = _ensure_list(args.get("allowed_tools"))
    inputs = _ensure_list(args.get("inputs"))
    success_criteria = args.get("success_criteria")
    notification_mode = args.get("notification_mode")

    if not workflow_id:
        return {"error": "Missing workflow name or ID."}
    if not plan and not steps:
        return {"error": "Missing workflow plan. Provide a 'plan' with LLM instructions."}

    try:
        app.workflow_registry.register(
            workflow_id=workflow_id,
            name=name or workflow_id,
            steps=steps,
            description=description,
            goal=goal,
            tags=tags,
            scope=scope,
            created_by="builder",
            plan=plan,
            model=model,
            allowed_tools=allowed_tools,
            inputs=inputs,
            success_criteria=success_criteria,
            notification_mode=notification_mode,
        )

        app.config.apply_from_state(
            state_manager=app.state_manager,
            description=f"Workflow created: {workflow_id}",
            trigger="workflow_create",
        )

        app.audit.log("workflow.created", details={
            "workflow_id": workflow_id,
            "has_plan": bool(plan),
            "allowed_tools": allowed_tools or [],
            "inputs": [i.get("name") for i in (inputs or [])],
        })

        return {
            "status": "success",
            "workflow_id": workflow_id,
            "message": f"Workflow '{name or workflow_id}' created.",
        }
    except Exception as e:
        import logging as _log
        _log.getLogger("mycelos.workflow").error("Failed to create workflow '%s': %s", workflow_id, e)
        return {"error": "Failed to create workflow. Check server logs for details."}


def execute_create_schedule(args: dict, context: dict) -> Any:
    """Create a scheduled task for a workflow."""
    app = context["app"]
    workflow_id = args.get("workflow_id", "")
    cron = args.get("cron", "")

    if not workflow_id:
        return {"error": "Missing workflow_id."}
    if not cron:
        return {"error": "Missing cron expression."}

    # Validate workflow exists
    try:
        workflows = app.workflow_registry.list_workflows(status="active")
        wf_ids = [w["id"] for w in workflows]
        if workflow_id not in wf_ids:
            return {
                "error": f"Workflow '{workflow_id}' not found.",
                "available_workflows": wf_ids,
            }
    except Exception:
        pass

    # Validate cron
    try:
        from mycelos.scheduler.schedule_manager import parse_next_run

        next_run = parse_next_run(cron)
    except Exception as e:
        return {"error": f"Invalid cron expression: {e}"}

    # Create the schedule
    task_id = app.schedule_manager.add(
        workflow_id=workflow_id,
        schedule=cron,
    )

    # Create config generation
    app.config.apply_from_state(
        state_manager=app.state_manager,
        description=f"Schedule added: {workflow_id} ({cron})",
        trigger="schedule_add",
    )

    app.audit.log("schedule.created", details={
        "workflow_id": workflow_id,
        "cron": cron,
        "task_id": task_id,
    })

    return {
        "status": "scheduled",
        "workflow_id": workflow_id,
        "cron": cron,
        "next_run": next_run.strftime("%Y-%m-%d %H:%M UTC"),
        "task_id": task_id[:8],
    }


def execute_workflow_info(args: dict, context: dict) -> Any:
    """Get details of a workflow."""
    app = context["app"]
    workflow_id = args.get("workflow_id", "")

    try:
        wf = app.workflow_registry.get(workflow_id)
        if not wf:
            return {"error": f"Workflow '{workflow_id}' not found."}
        return {
            "id": wf["id"],
            "name": wf["name"],
            "description": wf.get("description", ""),
            "plan": wf.get("plan", ""),
            "model": wf.get("model", "haiku"),
            "allowed_tools": wf.get("allowed_tools", []),
            "inputs": wf.get("inputs", []),
            "tags": wf.get("tags", []),
            "version": wf.get("version", 1),
        }
    except Exception as e:
        import logging as _log
        _log.getLogger("mycelos.workflow").error("workflow_info failed for '%s': %s", workflow_id, e)
        return {"error": "Failed to retrieve workflow info. Check server logs for details."}


UPDATE_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_workflow",
        "description": (
            "Update an existing workflow's plan, description, allowed tools, model, "
            "or inputs. Only provide the fields you want to change — everything else "
            "stays the same. Use workflow_info first to see the current definition."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "ID of the workflow to update.",
                },
                "description": {
                    "type": "string",
                    "description": "New description (1-2 sentences).",
                },
                "plan": {
                    "type": "string",
                    "description": "New LLM instructions (replaces the entire plan).",
                },
                "model": {
                    "type": "string",
                    "enum": ["haiku", "sonnet", "opus"],
                    "description": "New model tier.",
                },
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New list of allowed tools (replaces the entire list).",
                },
                "inputs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "required": {"type": "boolean"},
                            "description": {"type": "string"},
                        },
                        "required": ["name", "type", "description"],
                    },
                    "description": "New input parameters (replaces the entire list).",
                },
                "success_criteria": {
                    "type": "string",
                    "description": "New success criteria (natural language).",
                },
                "notification_mode": {
                    "type": "string",
                    "enum": ["result_only", "progress", "none"],
                    "description": "When to notify the user.",
                },
            },
            "required": ["workflow_id"],
        },
    },
}


def execute_update_workflow(args: dict, context: dict) -> Any:
    """Update an existing workflow."""
    app = context["app"]
    workflow_id = args.get("workflow_id", "")

    if not workflow_id:
        return {"error": "Missing workflow_id."}

    try:
        kwargs: dict[str, Any] = {}
        for field in ("description", "plan", "model", "goal", "success_criteria", "notification_mode"):
            if field in args:
                kwargs[field] = args[field]
        # List fields: guard against LLM passing JSON strings
        for field in ("allowed_tools", "inputs", "tags"):
            if field in args:
                kwargs[field] = _ensure_list(args[field]) or args[field]

        if not kwargs:
            return {"error": "Nothing to update. Provide at least one field to change."}

        app.workflow_registry.update(workflow_id, **kwargs)

        app.config.apply_from_state(
            state_manager=app.state_manager,
            description=f"Workflow updated: {workflow_id}",
            trigger="workflow_update",
        )

        app.audit.log("workflow.updated", details={
            "workflow_id": workflow_id,
            "updated_fields": list(kwargs.keys()),
        })

        return {
            "status": "updated",
            "workflow_id": workflow_id,
            "updated_fields": list(kwargs.keys()),
        }
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        import logging as _log
        _log.getLogger("mycelos.workflow").error("Failed to update workflow '%s': %s", workflow_id, e)
        return {"error": "Failed to update workflow. Check server logs for details."}


RUN_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_workflow",
        "description": (
            "Execute a registered workflow immediately with the given inputs. "
            "Use this when the user wants to run an existing workflow now. "
            "Inputs are key=value pairs matching the workflow's input schema. "
            "Example: run_workflow('research-summary', {query: 'AI news'})"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "ID of the workflow to run (e.g., 'research-summary')",
                },
                "inputs": {
                    "type": "object",
                    "description": (
                        "Input parameters for the workflow as key=value pairs. "
                        "Check workflow_info to see what inputs a workflow expects."
                    ),
                },
            },
            "required": ["workflow_id"],
        },
    },
}


def execute_run_workflow(args: dict, context: dict) -> Any:
    """Execute a workflow immediately via WorkflowAgent."""
    app = context.get("app")
    if not app:
        return {"error": "No app context"}

    workflow_id = args.get("workflow_id", "")
    inputs = args.get("inputs", {})

    if not workflow_id:
        return {"error": "workflow_id is required"}

    # Get workflow definition
    workflow_def = app.workflow_registry.get(workflow_id)
    if not workflow_def:
        all_workflows = app.workflow_registry.list_workflows()
        available = [w["id"] for w in all_workflows]
        return {
            "error": f"Workflow '{workflow_id}' not found",
            "available_workflows": available,
        }

    # Validate required inputs against schema
    input_schema = workflow_def.get("inputs", [])
    if isinstance(input_schema, str):
        import json as _json
        try:
            input_schema = _json.loads(input_schema)
        except (ValueError, TypeError):
            input_schema = []
    missing = []
    for inp in input_schema:
        if inp.get("required") and inp["name"] not in inputs:
            missing.append(inp["name"])
    if missing:
        # Build a helpful usage message
        wf_name = workflow_def.get("name", workflow_id)
        usage_parts = [f'  {inp["name"]}' +
                       (' (required)' if inp.get("required") else ' (optional)') +
                       f' — {inp.get("description", "")}'
                       for inp in input_schema]
        usage = "\n".join(usage_parts)
        example_params = " ".join(f'{inp["name"]}="..."' for inp in input_schema if inp.get("required"))
        return {
            "error": (
                f"**{wf_name}** needs more information:\n\n"
                f"{usage}\n\n"
                f"Usage: `/run {workflow_id} {example_params}`"
            ),
            "expected_inputs": input_schema,
        }

    # Ensure workflow has a plan for WorkflowAgent
    plan = workflow_def.get("plan")
    if not plan:
        return {"error": f"Workflow '{workflow_id}' has no plan. Cannot execute without LLM instructions."}

    # Execute via WorkflowAgent
    import logging
    logger = logging.getLogger("mycelos.workflow")
    logger.info("Running workflow '%s' with inputs: %s", workflow_id, inputs)

    try:
        from mycelos.workflows.agent import WorkflowAgent

        run_id = str(uuid.uuid4())[:16]
        agent = WorkflowAgent(
            app=app,
            workflow_def=workflow_def,
            run_id=run_id,
        )
        on_progress = context.get("on_progress")
        result = agent.execute(inputs=inputs, on_progress=on_progress)

        app.audit.log("workflow.executed", details={
            "workflow_id": workflow_id,
            "run_id": run_id,
            "status": result.status,
            "total_tokens": result.total_tokens,
        })

        if result.status == "completed":
            return {
                "status": "success",
                "workflow_id": workflow_id,
                "result": result.result,
                "cost": result.cost,
            }
        elif result.status == "needs_clarification":
            return {
                "status": "needs_clarification",
                "workflow_id": workflow_id,
                "clarification": result.clarification,
            }
        else:
            return {
                "status": "failed",
                "workflow_id": workflow_id,
                "error": result.error,
            }

    except Exception as e:
        logger.error("Workflow execution failed for '%s': %s", workflow_id, e)
        return {"error": "Workflow execution failed. Check server logs for details."}


# --- Registration ---

def register(registry: type) -> None:
    """Register all workflow tools."""
    registry.register("create_workflow", CREATE_WORKFLOW_SCHEMA, execute_create_workflow, ToolPermission.STANDARD, category="workflows")
    registry.register("update_workflow", UPDATE_WORKFLOW_SCHEMA, execute_update_workflow, ToolPermission.STANDARD, category="workflows")
    registry.register("create_schedule", CREATE_SCHEDULE_SCHEMA, execute_create_schedule, ToolPermission.STANDARD, category="workflows")
    registry.register("delete_schedule", DELETE_SCHEDULE_SCHEMA, execute_delete_schedule, ToolPermission.STANDARD, category="workflows")
    registry.register("list_schedules", LIST_SCHEDULES_SCHEMA, execute_list_schedules, ToolPermission.STANDARD, concurrent_safe=True, category="workflows")
    registry.register("workflow_info", WORKFLOW_INFO_SCHEMA, execute_workflow_info, ToolPermission.STANDARD, concurrent_safe=True, category="workflows")
    registry.register("run_workflow", RUN_WORKFLOW_SCHEMA, execute_run_workflow, ToolPermission.STANDARD, category="workflows")
