"""Agent tools — create agents and handoff between agents."""

from __future__ import annotations

from typing import Any

from mycelos.tools.registry import ToolPermission

# --- Tool Schemas ---

CREATE_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_agent",
        "description": (
            "Create a new agent. Two modes:\n"
            "1. Persona agent (lightweight): provide system_prompt + allowed_tools. "
            "Runs on a cheap model, can chat with the user.\n"
            "2. Code agent (full pipeline): provide name + description. "
            "The pipeline runs: gherkin -> tests -> code -> audit -> register."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Agent name in kebab-case (e.g., 'researcher', 'tech-writer').",
                },
                "description": {
                    "type": "string",
                    "description": "What the agent does.",
                },
                "system_prompt": {
                    "type": "string",
                    "description": (
                        "The agent's system prompt. If provided, creates a PERSONA agent "
                        "(no code pipeline). The prompt defines the agent's identity and behavior."
                    ),
                },
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Tools this agent can use (e.g., ['search_web', 'note_write']). "
                        "Empty = all tools. Only for persona agents."
                    ),
                },
                "model": {
                    "type": "string",
                    "description": "Model for this agent (e.g., 'haiku', 'sonnet'). Default: haiku for personas.",
                },
                "can_chat": {
                    "type": "boolean",
                    "description": "Whether the user can chat directly with this agent. Default: true for personas.",
                },
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Required capabilities for code agents (e.g., 'filesystem.read').",
                },
                "input_format": {"type": "string"},
                "output_format": {"type": "string"},
                "trigger": {
                    "type": "string",
                    "enum": ["on_demand", "scheduled"],
                },
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Python packages the agent requires, "
                        "e.g. ['pdfplumber', 'pandas']"
                    ),
                },
            },
            "required": ["name", "description"],
        },
    },
}

HANDOFF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "handoff",
        "description": (
            "Transfer the conversation to another agent. "
            "Use when the user wants to build something (agents, workflows, "
            "automations, integrations) or when done and returning control."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_agent": {
                    "type": "string",
                    "description": "The agent ID to hand off to.",
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Why the handoff is needed. Shown to the receiving agent "
                        "as context for the request."
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": (
                        "Short summary of the conversation so far (1-3 sentences). "
                        "Helps the receiving agent quickly understand the context."
                    ),
                },
            },
            "required": ["target_agent", "reason"],
        },
    },
}


# --- Dependency Checking ---

def _check_missing_packages(packages: list[str]) -> list[str]:
    """Check which packages are not installed.

    Uses importlib.util.find_spec to probe for each package.
    Hyphens in package names are replaced with underscores to match
    the common PyPI-to-import-name convention.

    Returns:
        List of package names that are not importable.
    """
    import importlib.util

    missing: list[str] = []
    for pkg in packages:
        # Package name may differ from import name (e.g., Pillow -> PIL)
        # For the common case, replace hyphens with underscores.
        import_name = pkg.replace("-", "_")
        spec = importlib.util.find_spec(import_name)
        if spec is None:
            missing.append(pkg)
    return missing


# --- Tool Execution ---

def _create_persona_agent(args: dict, app: Any) -> dict:
    """Create a lightweight persona agent from prompt + tools. No code pipeline."""
    name = args.get("name", "persona")
    agent_id = name.lower().replace(" ", "-")
    description = args.get("description", "")
    system_prompt = args["system_prompt"]
    allowed_tools = args.get("allowed_tools", [])
    model = args.get("model")
    can_chat = args.get("can_chat", True)

    # Resolve short tier names (haiku/sonnet/opus) to the cheapest registered
    # model of that tier. Unknown / missing → cheapest background model.
    def _tier_model(tier: str) -> str | None:
        candidates = app.model_registry.list_models(tier=tier)
        return candidates[0]["id"] if candidates else None

    if model in ("haiku", "claude-haiku"):
        model = _tier_model("haiku") or app.resolve_cheapest_model()
    elif model in ("sonnet", "claude-sonnet"):
        model = _tier_model("sonnet") or app.resolve_cheapest_model()
    elif model in ("opus", "claude-opus"):
        model = _tier_model("opus") or app.resolve_strongest_model()
    elif not model:
        model = app.resolve_cheapest_model()

    # Register agent
    existing = app.agent_registry.get(agent_id)
    if existing is None:
        app.agent_registry.register(
            agent_id, name, "persona", [], "user",
        )

    # Set persona config
    app.agent_registry.set_persona(
        agent_id,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools if allowed_tools else None,
        model=model,
        user_facing=can_chat,
        display_name=name,
    )
    app.agent_registry.set_status(agent_id, "active")

    app.audit.log("agent.persona_created", details={
        "agent_id": agent_id,
        "name": name,
        "model": model,
        "tools": len(allowed_tools),
        "can_chat": can_chat,
    })

    return {
        "status": "created",
        "agent_id": agent_id,
        "name": name,
        "type": "persona",
        "model": model,
        "allowed_tools": allowed_tools or "all",
        "can_chat": can_chat,
        "message": f"Persona agent '{name}' created. "
                   + ("User can chat with it directly." if can_chat else "Available for handoff only."),
    }


def execute_create_agent(args: dict, context: dict) -> Any:
    """Create a new agent — persona (lightweight) or code (full pipeline)."""
    app = context["app"]

    # Persona mode: if system_prompt is provided, skip the code pipeline
    if args.get("system_prompt"):
        return _create_persona_agent(args, app)

    # Code pipeline mode (full: gherkin → tests → code → audit → register)
    from mycelos.agents.agent_spec import AgentSpec
    from mycelos.agents.creator_pipeline import CreatorPipeline
    from mycelos.chat.events import step_progress_event as _spe
    from mycelos.security.permissions import PermissionRequired

    # Check dependencies before running pipeline
    dependencies: list[str] = args.get("dependencies", [])
    if dependencies:
        missing = _check_missing_packages(dependencies)
        if missing:
            raise PermissionRequired(
                tool="create_agent",
                action=f"pip install {' '.join(missing)}",
                reason=(
                    f"Agent '{args.get('name', 'new-agent')}' requires packages: "
                    f"{', '.join(missing)}. Install them?"
                ),
                target=", ".join(missing),
                action_type="package",
                original_args=args,
            )

    # Store pending events on the context for the ChatService to flush
    pending_events: list = []
    context["_pending_events"] = pending_events

    def _on_progress(step_id: str, status: str) -> None:
        pending_events.append(_spe(step_id, status))

    spec = AgentSpec(
        name=args.get("name", "new-agent"),
        description=args.get("description", ""),
        capabilities_needed=args.get("capabilities", []),
        input_format=args.get("input_format", ""),
        output_format=args.get("output_format", ""),
        trigger=args.get("trigger", "on_demand"),
        dependencies=dependencies,
    )

    pipeline = CreatorPipeline(app)
    pipeline_result = pipeline.run(spec, on_progress=_on_progress)

    if pipeline_result.success:
        return {
            "status": "success",
            "agent_id": pipeline_result.agent_id,
            "agent_name": pipeline_result.agent_name,
            "message": (
                f"Agent '{pipeline_result.agent_name}' created and registered. "
                f"Tests passed, audit passed."
            ),
            "cost": pipeline_result.cost,
        }
    elif pipeline_result.paused and pipeline_result.pause_reason == "retries_exhausted":
        return {
            "status": "retries_exhausted",
            "cost_so_far": pipeline_result.cost,
            "error": pipeline_result.error,
            "message": (
                f"Code generation failed after {CreatorPipeline.MAX_CODE_RETRIES} attempts. "
                f"Cost so far: ${pipeline_result.cost:.4f}. "
                f"Ask the user if they want to retry or adjust requirements."
            ),
        }
    elif pipeline_result.paused:
        return {
            "status": "paused",
            "pause_reason": pipeline_result.pause_reason,
            "message": pipeline_result.error,
        }
    else:
        return {
            "status": "failed",
            "error": pipeline_result.error,
            "message": f"Agent creation failed: {pipeline_result.error}",
        }


def execute_handoff(args: dict, context: dict) -> Any:
    """Execute agent handoff — delegates to ChatService._execute_handoff.

    The actual handoff logic (DB update, audit, etc.) lives in ChatService
    because it needs session state. This function is the registry entry point
    that the ChatService delegates to via _execute_tool_inner.
    """
    app = context["app"]
    session_id = context.get("session_id", "")
    target = args.get("target_agent", "mycelos")
    reason = args.get("reason", "")
    summary = args.get("context", args.get("summary", ""))

    # Validate: system agents are always valid
    system_agents = {"mycelos", "builder", "creator", "planner"}
    if target not in system_agents:
        agent = app.agent_registry.get(target)
        if not agent or not agent.get("user_facing"):
            return {"error": f"Agent '{target}' is not available for conversation"}

    prev_agent = context.get("agent_id", "mycelos")

    app.storage.execute(
        "INSERT OR REPLACE INTO session_agents (session_id, active_agent_id, handoff_reason) VALUES (?, ?, ?)",
        (session_id, target, reason),
    )

    app.audit.log("agent.handoff", details={
        "from": prev_agent,
        "to": target,
        "reason": reason,
        "session_id": session_id,
    })

    # Get display name for the target
    try:
        target_handlers = app.get_agent_handlers()
        target_handler = target_handlers.get(target)
        target_name = target_handler.display_name if target_handler else target
    except Exception:
        target_name = target

    return {
        "status": "handoff",
        "target_agent": target,
        "reason": reason,
        "message": f"Handed off to {target_name}: {reason}",
    }


# --- Registration ---

def register(registry: type) -> None:
    """Register all agent tools."""
    registry.register("create_agent", CREATE_AGENT_SCHEMA, execute_create_agent, ToolPermission.BUILDER, category="system")
    registry.register("handoff", HANDOFF_SCHEMA, execute_handoff, ToolPermission.SYSTEM, category="core")
