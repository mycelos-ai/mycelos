"""Agent routing: active agent per session, handoffs, per-agent tools and models.

Mixin for :class:`mycelos.chat.service.ChatService`; split out of
``chat/service.py`` without behavior changes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mycelos.chat.tool_selection import (
    _ROUTING_CONTROL_TOOLS,
    _SYSTEM_AGENT_IDS,
    _get_chat_agent_tools,
)
from mycelos.tools.registry import ToolRegistry

_log = logging.getLogger("mycelos.chat.service")


class AgentRoutingMixin:
    """Agent routing: active agent per session, handoffs, per-agent tools and models."""

    def _get_session_tools(
        self,
        handler: Any,
        session_id: str,
        user_id: str = "default",
        channel: str = "api",
    ) -> list[dict]:
        """Get tools for a session: budget-aware base + discovered extras.

        For the Mycelos handler, uses ``_get_chat_agent_tools`` with session
        parameters so the basis-set is budget-aware and mid-session discovered
        categories are merged in.  Other handlers (Builder, custom) get their
        tools via the normal ``handler.get_tools()`` path unchanged.
        """
        agent_id = getattr(handler, "agent_id", "mycelos")
        extras = self._session_extra_tools.get(session_id) or set()

        # Only Mycelos and persona handlers use session-aware loading
        use_session_aware = agent_id == "mycelos" or getattr(handler, "_is_persona", False)
        if use_session_aware:
            base = _get_chat_agent_tools(
                app=self._app,
                channel=channel,
                user_id=user_id,
                agent_id=agent_id,
                session_extras=extras,
            )
            # Re-add handler extras (handoff, custom agent tools, widgets)
            # by calling handler.get_tools and taking only non-base tools
            try:
                full_handler_tools = handler.get_tools(channel=channel)
            except TypeError:
                full_handler_tools = handler.get_tools()
            base_names = {t.get("function", {}).get("name") for t in base}
            for t in full_handler_tools:
                name = t.get("function", {}).get("name", "")
                if name not in base_names:
                    base.append(t)
                    base_names.add(name)
            # Constitution Rule 5: persona agents with an explicit tool
            # allowlist only see their registered tools. Routing-control
            # tools stay so the agent can always hand back.
            if getattr(handler, "_is_persona", False):
                allowed = self._get_agent_allowed_tools(agent_id)
                if allowed is not None:
                    permitted = allowed | _ROUTING_CONTROL_TOOLS
                    base = [
                        t for t in base
                        if t.get("function", {}).get("name") in permitted
                    ]
            # Every non-mycelos agent gets the explicit return path
            if agent_id != "mycelos":
                self._append_return_tool(base)
            return base
        else:
            try:
                tools = handler.get_tools(channel=channel)
            except TypeError:
                tools = handler.get_tools()
            if agent_id != "mycelos":
                tools = list(tools)
                self._append_return_tool(tools)
            return tools

    @staticmethod
    def _append_return_tool(tools: list[dict]) -> None:
        """Append the return_to_mycelos schema if it isn't there yet."""
        names = {t.get("function", {}).get("name") for t in tools}
        if "return_to_mycelos" not in names:
            schema = ToolRegistry.get_schema("return_to_mycelos")
            if schema:
                tools.append(schema)

    def _get_agent_allowed_tools(self, agent_id: str) -> set[str] | None:
        """Return the explicit tool allowlist for *agent_id*.

        ``None`` means unrestricted (system agents, personas registered
        with an empty allowed_tools list). Fail-closed (Constitution
        Rule 3): unknown agents and lookup/parse errors return an empty
        set, which denies everything except the routing-control tools.
        """
        if not agent_id or agent_id in _SYSTEM_AGENT_IDS:
            return None
        try:
            agent = self._app.agent_registry.get(agent_id)
        except Exception:
            return set()  # lookup error = denied (fail-closed)
        if agent is None:
            return set()  # unknown agent = denied (fail-closed)
        raw = agent.get("allowed_tools")
        if raw in (None, ""):
            return None
        try:
            names = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return set()  # unparseable allowlist = denied (fail-closed)
        if not isinstance(names, list):
            return set()
        if not names:
            return None  # empty list = "all tools" (persona semantics)
        return {str(n) for n in names}

    def _get_active_agent(self, session_id: str) -> str:
        """Get the active agent for this session. Default: mycelos."""
        if session_id in self._active_agents_cache:
            return self._active_agents_cache[session_id]
        row = self._app.storage.fetchone(
            "SELECT active_agent_id FROM session_agents WHERE session_id = ?",
            (session_id,),
        )
        agent_id = (row or {}).get("active_agent_id", "mycelos")
        self._active_agents_cache[session_id] = agent_id
        return agent_id

    def _execute_handoff(self, session_id: str, target_agent_id: str,
                         reason: str, context: str = "") -> dict:
        """Execute agent handoff — update DB, return result."""
        # Validate: system agents are always valid
        if target_agent_id not in _SYSTEM_AGENT_IDS:
            agent = self._app.agent_registry.get(target_agent_id)
            if not agent or agent.get("status") != "active" or not agent.get("user_facing"):
                return {"error": f"Agent '{target_agent_id}' is not available for conversation"}

        prev_agent = self._get_active_agent(session_id)

        self._app.storage.execute(
            "INSERT OR REPLACE INTO session_agents (session_id, active_agent_id, handoff_reason) VALUES (?, ?, ?)",
            (session_id, target_agent_id, reason),
        )

        self._active_agents_cache[session_id] = target_agent_id

        self._app.audit.log("agent.handoff", details={
            "from": prev_agent, "to": target_agent_id,
            "reason": reason, "session_id": session_id,
        })

        return {"status": "handoff", "target_agent": target_agent_id, "reason": reason}

    def _get_model_for_agent(self, agent_id: str) -> str | None:
        """Get the LLM model for this agent. Returns None for system default."""
        try:
            models = self._app.model_registry.resolve_models(agent_id, "execution")
            if models:
                return models[0]
        except Exception:
            _log.debug("Failed to resolve model for agent %s", agent_id, exc_info=True)
        return None  # Use default

    def _complete_for_agent(self, messages: list[dict], agent_id: str, tools=None):
        """Resolve the current agent chain for each round, including retries."""
        models = self._app.model_registry.resolve_models(agent_id, "execution")
        if models:
            return self._app.llm.complete(
                messages, tools=tools, model=models[0], fallback_models=models[1:],
            )
        return self._app.llm.complete(messages, tools=tools)

    def _augment_tools_with_connectors(self, tools: list[dict]) -> list[dict]:
        """Augment a tool list with MCP connector meta-tools and github_api if applicable.

        Used by the mycelos handler to add dynamic connector access without
        duplicating the connector injection logic. Now pulls schemas from
        the ToolRegistry instead of inline definitions.
        """
        tools = list(tools)  # copy, don't mutate
        existing_names = {t["function"]["name"] for t in tools}

        # Add connector meta-tools if any MCP connectors are running
        mcp_mgr = getattr(self._app, "_mcp_manager", None)
        has_connectors = mcp_mgr and mcp_mgr.tool_count > 0

        if has_connectors:
            for name in ("connector_tools", "connector_call"):
                if name not in existing_names:
                    schema = ToolRegistry.get_schema(name)
                    if schema:
                        tools.append(schema)

        # github_api fallback for REST endpoints MCP doesn't cover
        try:
            connectors = self._app.connector_registry.list_connectors(status="active")
            if any(c["id"] == "github" for c in connectors) and "github_api" not in existing_names:
                schema = ToolRegistry.get_schema("github_api")
                if schema:
                    tools.append(schema)
        except Exception:
            _log.debug("Failed to augment tools with github_api connector", exc_info=True)

        return tools
