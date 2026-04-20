"""PromptLoader — reads Markdown prompt files with {variable} substitution.

Prompts live as .md files in this directory. Variables use Python's
str.format_map() syntax: {variable_name}. Unset variables are left
as-is (no KeyError).

Available variables (built by build_prompt_variables):
    {system_info}           — date, time, OS, Python version, Docker detection
    {user_context}          — user name, language, persistent memory entries
    {active_connectors}     — MCP tools, channels, builtin connectors with capabilities
    {available_agents}      — custom agents/personas with routing descriptions
    {available_workflows}   — registered workflows with descriptions
    {registered_agents}     — agents for Builder (ID, type, capabilities)
    {registered_workflows}  — workflows for Builder (ID, description, steps)
    {available_capabilities} — all capabilities from connectors (deduplicated)
    {available_connectors}  — connectors for Builder (ID, name, type)
    {level_guidance}        — user-level-aware guidance (Newcomer→Guru)
    {handoff_rules}         — agent routing rules with custom agents
    {pending_workflows}     — paused/waiting workflow runs
    {agent_name}            — custom display name if user renamed the agent
    {channel_prompt}        — channel-specific instructions (api/cli/telegram)
    {system_context}        — Planner: formatted system state for planning
    {configured_providers}  — which LLM providers have credentials (prevents "add your key" hallucinations)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mycelos.app import App

logger = logging.getLogger(__name__)


class PromptLoader:
    """Load and render Markdown prompt templates.

    Variables use a simple {variable_name} syntax. Only variables that
    are passed as keyword arguments are substituted — all other braces
    (JSON examples, Python format strings, etc.) are left untouched.
    This avoids the need for double-brace escaping in prompt files.

    Usage:
        loader = PromptLoader()
        prompt = loader.load("mycelos", user_name="Stefan", channel="telegram")
    """

    def __init__(self, prompt_dir: Path | None = None) -> None:
        if prompt_dir is None:
            prompt_dir = Path(__file__).parent
        self.prompt_dir = prompt_dir
        self._cache: dict[str, str] = {}

    def load(self, name: str, **variables: str) -> str:
        """Load a prompt by name, substituting variables.

        Uses simple string replacement instead of format_map() to avoid
        conflicts with JSON, Python format strings, and other brace-
        containing content in prompt files.

        Args:
            name: Filename without .md extension (e.g., "mycelos", "builder").
            **variables: Key-value pairs to substitute into the template.

        Returns:
            The rendered prompt string.

        Raises:
            FileNotFoundError: If the prompt file doesn't exist.
        """
        if name not in self._cache:
            path = self.prompt_dir / f"{name}.md"
            if not path.exists():
                raise FileNotFoundError(f"Prompt file not found: {path}")
            self._cache[name] = path.read_text(encoding="utf-8")

        template = self._cache[name]
        for key, value in variables.items():
            template = template.replace("{" + key + "}", str(value))
        return template


def build_prompt_variables(app: "App") -> dict[str, str]:
    """Build ALL dynamic prompt variables from current system state.

    Returns a dict of variable_name → rendered text. Every agent's
    get_system_prompt() can pass this to PromptLoader.load(**vars) —
    only variables with matching {placeholders} in the template are
    substituted; the rest are silently ignored by _SafeDict.
    """
    variables: dict[str, str] = {}

    # --- system_info: date, time, OS, environment ---
    try:
        import datetime
        from mycelos.chat.service import _build_system_info
        variables["system_info"] = _build_system_info(datetime.datetime.now())
    except Exception:
        variables["system_info"] = ""

    # --- user_context: name, language, memory ---
    try:
        from mycelos.agents.handlers.base import build_user_context
        variables["user_context"] = build_user_context(app)
    except Exception:
        variables["user_context"] = ""

    # --- active_connectors: MCP tools, channels ---
    try:
        from mycelos.agents.handlers.mycelos_handler import _build_mcp_connectors_context
        variables["active_connectors"] = _build_mcp_connectors_context(app)
    except Exception:
        variables["active_connectors"] = ""

    # --- available_agents: custom agents for Mycelos routing ---
    try:
        from mycelos.agents.handlers.mycelos_handler import (
            _build_custom_agents_context,
            _build_paused_agents_context,
        )
        routing_text, _ = _build_custom_agents_context(app)
        paused = _build_paused_agents_context(app)
        variables["available_agents"] = (
            "## Custom Agents\n" + routing_text + paused
            if routing_text else ""
        )
    except Exception:
        variables["available_agents"] = ""

    # --- available_workflows: for Mycelos to suggest existing ones ---
    try:
        workflows = app.workflow_registry.list_workflows() or []
        if workflows:
            lines = ["## Available Workflows"]
            lines.append("These workflows are already registered. Suggest running them instead of creating new ones.")
            for wf in workflows:
                wf_id = wf.get("id", "")
                desc = wf.get("description", wf.get("name", wf_id))
                lines.append(f"- **{wf_id}**: {desc}")
            variables["available_workflows"] = "\n".join(lines)
        else:
            variables["available_workflows"] = ""
    except Exception:
        variables["available_workflows"] = ""

    # --- Builder-specific: registered_agents, registered_workflows,
    #     available_capabilities, available_connectors ---
    try:
        from mycelos.agents.planner_context import build_planner_context, format_context_for_prompt
        ctx = build_planner_context(app)
        formatted = format_context_for_prompt(ctx)
        variables["registered_agents"] = _extract_section(formatted, "### Registered Agents")
        variables["registered_workflows"] = _extract_section(formatted, "### Available Workflows")
        variables["available_capabilities"] = _extract_section(formatted, "### Available Capabilities")
        variables["available_connectors"] = _extract_section(formatted, "### Connectors")
    except Exception:
        variables["registered_agents"] = ""
        variables["registered_workflows"] = ""
        variables["available_capabilities"] = ""
        variables["available_connectors"] = ""

    # --- level_guidance: Newcomer→Guru ---
    try:
        from mycelos.gamification import check_milestones, get_level_prompt
        level = check_milestones(app)
        variables["level_guidance"] = get_level_prompt(level)
    except Exception:
        variables["level_guidance"] = ""

    # --- handoff_rules: agent routing ---
    try:
        from mycelos.agents.handlers.mycelos_handler import (
            _HANDOFF_RULES_BASE,
            _build_custom_agents_context,
        )
        routing_text, _ = _build_custom_agents_context(app)
        variables["handoff_rules"] = _HANDOFF_RULES_BASE.format(
            custom_agent_routing=routing_text
        )
    except Exception:
        variables["handoff_rules"] = ""

    # --- pending_workflows: paused/waiting runs ---
    try:
        runs = app.workflow_run_manager.get_pending_runs() if hasattr(app, "workflow_run_manager") else []
        if runs:
            lines = ["## Pending Workflows"]
            for run in runs:
                name = run.get("workflow_id", "unknown")
                status = run.get("status", "")
                lines.append(f"- **{name}**: {status}")
            lines.append("\nMention these pending workflows and ask whether to resume or abort.")
            variables["pending_workflows"] = "\n".join(lines)
        else:
            variables["pending_workflows"] = ""
    except Exception:
        variables["pending_workflows"] = ""

    # --- configured_providers: which LLM providers the user has set up.
    #     Prevents "please add your API key" hallucinations when the key
    #     is already stored (issue #3 from mycelos_retired).
    try:
        creds = app.credentials.list_credentials(user_id="default")
        llm_services = {"anthropic", "openai", "google", "gemini", "mistral", "cohere", "groq"}
        providers = sorted({
            (c.get("service") or "").lower()
            for c in creds
            if (c.get("service") or "").lower() in llm_services
        })
        # Ollama is credential-less — include it when an endpoint is recorded
        try:
            if app.memory.get("default", "system", "provider.ollama.url"):
                providers.append("ollama")
        except Exception:
            pass
        if providers:
            variables["configured_providers"] = (
                "## Configured LLM Providers\n"
                f"The user has API credentials configured for: {', '.join(providers)}.\n"
                "When the user asks to 'use X as the model' where X is a model from one "
                "of these providers (e.g. Sonnet, Opus, Haiku → Anthropic; GPT-4o → OpenAI; "
                "Gemini → Google), DO NOT ask for an API key — it is already stored. "
                "Instead, acknowledge the model switch and proceed. Only prompt for a key "
                "if the requested model is from a provider NOT in the list above."
            )
        else:
            variables["configured_providers"] = (
                "## Configured LLM Providers\n"
                "No LLM providers are configured yet. If the user asks to use a model, "
                "guide them to Settings to add an API key."
            )
    except Exception:
        variables["configured_providers"] = ""

    # --- agent_name: custom display name ---
    try:
        row = app.agent_registry.get("mycelos")
        name = row.get("display_name", "") if row else ""
        if name and name != "Mycelos":
            variables["agent_name"] = (
                f'## Your Name\nThe user has named you "{name}". '
                f"Use this name when referring to yourself. "
                f"Your system name is still Mycelos."
            )
        else:
            variables["agent_name"] = ""
    except Exception:
        variables["agent_name"] = ""

    return variables


def _extract_section(formatted: str, heading: str) -> str:
    """Extract a section from formatted planner context by heading."""
    lines = formatted.split("\n")
    result: list[str] = []
    in_section = False
    for line in lines:
        if line.strip() == heading:
            in_section = True
            result.append(line)
            continue
        if in_section:
            if line.startswith("### ") and line.strip() != heading:
                break
            result.append(line)
    return "\n".join(result).strip() if result else ""
