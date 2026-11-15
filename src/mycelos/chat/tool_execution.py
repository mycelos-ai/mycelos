"""Tool execution behind the chat security gate (allowlist, policy, sanitizer, audit).

Mixin for :class:`mycelos.chat.service.ChatService`; split out of
``chat/service.py`` without behavior changes.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from typing import Any

from mycelos.chat.events import step_progress_event
from mycelos.chat.guards import _sanitize_dict
from mycelos.chat.tool_selection import _ROUTING_CONTROL_TOOLS, MAX_TOOL_CONCURRENCY
from mycelos.tools.registry import ToolRegistry

_log = logging.getLogger("mycelos.chat.service")
_perf = logging.getLogger("mycelos.perf")


class ToolExecutionMixin:
    """Tool execution behind the chat security gate (allowlist, policy, sanitizer, audit)."""

    def _execute_batch_parallel(
        self,
        batch: list[dict],
        conversation: list[dict],
        events: list,
        user_id: str,
        session_id: str,
    ) -> None:
        """Execute a batch of concurrent-safe tool calls in parallel.

        Results are added to the conversation in the original order.
        Events (step-progress) are emitted for each tool.
        """
        import time as _time

        # Emit "running" for all tools at once
        parsed: list[tuple[dict, str, dict]] = []
        for tc in batch:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            parsed.append((tc, name, args))
            events.append(step_progress_event(name, "running"))

        _perf.info("Parallel batch: %d tools [%s]", len(parsed), ", ".join(n for _, n, _ in parsed))
        _t0 = _time.monotonic()

        # Execute in thread pool
        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=min(len(parsed), MAX_TOOL_CONCURRENCY)) as pool:
            futures = {
                pool.submit(copy_context().run, self._execute_tool, name, args,
                            user_id=user_id, session_id=session_id): (tc, name)
                for tc, name, args in parsed
            }
            for future in as_completed(futures):
                tc, name = futures[future]
                try:
                    results[tc["id"]] = future.result()
                except Exception as e:
                    results[tc["id"]] = {"error": str(e)}

        _perf.info("Parallel batch done: %.1fs", _time.monotonic() - _t0)

        # Add results to conversation in original order
        for tc, name, args in parsed:
            result = results.get(tc["id"], {"error": "execution failed"})
            events.append(step_progress_event(name, "done"))
            tool_result_str = json.dumps(result, ensure_ascii=False, default=str) if not isinstance(result, str) else result
            conversation.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": tool_result_str,
            })

    def _execute_tool(self, tool_name: str, args: dict, user_id: str = "default",
                      session_id: str = "", agent_id: str = "") -> Any:
        """Execute a tool call with security checks.

        Security Gate (SEC21):
        1. PolicyEngine — check if tool is allowed for this user
        2. Execute tool
        3. ResponseSanitizer — redact credentials in result
        4. Audit log
        """
        import logging
        _log = logging.getLogger("mycelos.chat.tools")

        # Layer 0: Agent tool allowlist (Constitution Rule 5).
        # Routed persona/custom agents only use tools from their
        # registered allowlist; routing-control tools are always allowed
        # so the agent can hand back. Fail-closed on unknown agents.
        acting_agent = agent_id or self._get_active_agent(
            session_id or self._current_session_id
        )
        if tool_name not in _ROUTING_CONTROL_TOOLS:
            allowed_for_agent = self._get_agent_allowed_tools(acting_agent)
            if allowed_for_agent is not None and tool_name not in allowed_for_agent:
                self._app.audit.log("tool.blocked", details={
                    "tool": tool_name, "reason": "agent_allowlist",
                    "agent_id": acting_agent, "user_id": user_id,
                })
                return {
                    "error": (
                        f"Tool '{tool_name}' is not available to agent "
                        f"'{acting_agent}'. Use one of your registered tools "
                        f"or call return_to_mycelos."
                    )
                }

        # Layer 1: Policy check
        # In chat context, the user is directly present — default is "always" (not "confirm").
        # Only explicitly set policies ("never", "confirm") override the default.
        # This differs from workflow/agent context where default is "confirm".
        decision = self._app.policy_engine.evaluate(user_id, None, tool_name)
        if decision == "confirm":
            # Check if this is an explicit policy or just the default fallback
            explicit = self._app.storage.fetchone(
                "SELECT decision FROM policies WHERE user_id = ? AND agent_id IS NULL AND resource = ?",
                (user_id, tool_name),
            )
            if not explicit:
                decision = "always"  # No explicit policy → auto-allow in chat
        _log.debug("Tool %s: policy=%s user=%s args=%s", tool_name, decision, user_id, str(args)[:200])

        if decision == "never":
            self._app.audit.log("tool.blocked", details={
                "tool": tool_name, "reason": "policy:never", "user_id": user_id,
            })
            return {"error": f"Tool '{tool_name}' is blocked by policy."}

        # "confirm" — tool requires user confirmation before execution
        if decision == "confirm":
            self._app.audit.log("tool.confirmation_required", details={
                "tool": tool_name, "user_id": user_id, "policy": "confirm",
            })
            return {
                "status": "confirmation_required",
                "tool": tool_name,
                "args": args,
                "message": (
                    f"Tool '{tool_name}' requires user confirmation. "
                    "Ask the user to approve this action or suggest the "
                    "equivalent slash command."
                ),
            }

        # Store user_id for downstream tool calls (e.g. _connector_call_tool)
        self._current_user_id = user_id

        # Layer 2: Execute — PermissionRequired propagates to tool loop
        from mycelos.security.permissions import PermissionRequired as _PermReq
        try:
            result = self._execute_tool_inner(tool_name, args, session_id=session_id, agent_id=agent_id)
        except _PermReq:
            raise  # Let the tool loop in handle_message show the permission prompt
        _log.debug("Tool %s: result=%s", tool_name, str(result)[:300])

        # Layer 3: Sanitize result
        from mycelos.security.sanitizer import ResponseSanitizer
        sanitizer = ResponseSanitizer()
        if isinstance(result, dict):
            # Sanitize all string values recursively
            result = _sanitize_dict(sanitizer, result)
        elif isinstance(result, str):
            result = sanitizer.sanitize_text(result)

        # Layer 4: Audit
        self._app.audit.log("tool.executed", details={
            "tool": tool_name, "user_id": user_id, "policy": decision,
        })

        return result

    def _execute_tool_inner(self, tool_name: str, args: dict,
                            session_id: str = "", agent_id: str = "") -> Any:
        """Execute a tool call (no security checks — called by _execute_tool).

        Delegates to ToolRegistry.execute() for all registered tools.
        Handles request_action locally (service-level concern).
        """
        from mycelos.security.permissions import PermissionRequired
        try:
            # run_agent_* tools — execute custom code agents directly
            if tool_name.startswith("run_agent_"):
                return self._execute_custom_agent(tool_name, args)

            # request_action is a service-level concern, not a registry tool
            if tool_name == "request_action":
                return self._handle_request_action(
                    args.get("action", ""),
                    args.get("reason", ""),
                )

            # discover_tools — Lazy Tool Discovery (not a registry tool)
            if tool_name == "discover_tools":
                return self._handle_discover_tools(
                    args, session_id or self._current_session_id,
                )

            # Build context for registry execution
            active_agent = agent_id or self._get_active_agent(
                session_id or self._current_session_id
            )
            context = {
                "app": self._app,
                "chat_service": self,
                "user_id": self._current_user_id,
                "session_id": session_id or self._current_session_id,
                "agent_id": active_agent,
                "channel": getattr(self, "_current_channel", "api"),
            }

            # Provide a progress callback so long-running tools
            # (run_workflow, create_agent) can emit real-time events.
            # Note: some tools (create_agent) replace context["_pending_events"]
            # with their own list, so we check the context dict after execution.
            context["_pending_events"] = []

            def _on_progress(step_id: str, status: str) -> None:
                from mycelos.chat.events import step_progress_event
                context["_pending_events"].append(step_progress_event(step_id, status))

            context["on_progress"] = _on_progress

            result = ToolRegistry.execute(tool_name, args, context)

            # Flush pending events from long-running tools
            if context.get("_pending_events"):
                self._pending_events = context["_pending_events"]

            # Update active_agents_cache after handoff / return_to_mycelos
            if tool_name in _ROUTING_CONTROL_TOOLS and isinstance(result, dict) and result.get("status") == "handoff":
                target = result.get("target_agent", args.get("target_agent"))
                session_id = self._current_session_id
                if target and session_id:
                    self._active_agents_cache[session_id] = target

            return result

        except PermissionRequired:
            raise  # Let the tool loop handle this with a system prompt
        except Exception as e:
            import logging as _elog
            _elog.getLogger("mycelos.chat.tools").error(
                "Tool %s failed: %s", tool_name, e, exc_info=True
            )
            return {"error": str(e)}

    def _execute_custom_agent(self, tool_name: str, args: dict) -> Any:
        """Execute a custom code agent as a tool call.

        Tool name format: run_agent_<agent_id_with_underscores>
        Maps back to agent_id with hyphens (e.g., run_agent_pdf_text_extractor → pdf-text-extractor).
        """
        # Extract agent ID from tool name
        raw_id = tool_name[len("run_agent_"):]  # "pdf_text_extractor"

        # Try exact match first, then with hyphens
        agent_info = self._app.agent_registry.get(raw_id)
        if not agent_info:
            agent_info = self._app.agent_registry.get(raw_id.replace("_", "-"))
        if not agent_info:
            return {"error": f"Agent '{raw_id}' not found"}

        agent_id = agent_info["id"]
        task = args.get("task", "")
        inputs = args.get("inputs", {})

        # Load agent code from object store via code_hash
        code_hash = agent_info.get("code_hash")
        if not code_hash:
            return {"error": f"No code_hash for agent '{agent_id}'"}
        try:
            from mycelos.storage.object_store import ObjectStore
            obj_store = ObjectStore(self._app.data_dir)
            code = obj_store.load(code_hash)
            if not code:
                return {"error": f"Code not found in object store for agent '{agent_id}'"}
        except Exception as e:
            _log.debug("Failed to load agent code for %s: %s", agent_id, e)
            return {"error": f"Could not load agent '{agent_id}'"}

        # Execute the agent code in a subprocess
        try:
            from mycelos.agents.models import AgentInput
            from mycelos.execution.agent_runner import run_agent_code

            agent_input = AgentInput(
                task_goal=task,
                task_inputs=inputs,
                artifacts=[],
                context={},
                config={},
            )
            output = run_agent_code(code, agent_input, timeout=30)

            self._app.audit.log("agent.executed", details={
                "agent_id": agent_id, "success": output.success,
            })

            if output.success:
                return {
                    "status": "success",
                    "agent": agent_id,
                    "result": output.result,
                    "artifacts": output.artifacts,
                }
            else:
                return {
                    "status": "failed",
                    "agent": agent_id,
                    "error": output.error or "Agent execution failed",
                }
        except Exception as e:
            _log.error("Custom agent execution failed for %s: %s", agent_id, e)
            return {"error": f"Agent execution failed. Check server logs."}

    def _handle_discover_tools(self, args: dict, session_id: str) -> dict:
        """Handle discover_tools — load a tool category mid-session."""
        from mycelos.tools.categories import DISCOVERABLE_CATEGORIES, _categories

        category = args.get("category", "")
        if category not in DISCOVERABLE_CATEGORIES:
            return {
                "error": f"Unknown category: {category}. Available: {', '.join(DISCOVERABLE_CATEGORIES)}",
            }

        # Track this category as discovered for this session
        extras = self._session_extra_tools.setdefault(session_id, set())
        already_loaded = category in extras
        extras.add(category)

        # Return the list of tools now available (dynamically from registry)
        tool_names = _categories().get(category, [])

        self._app.audit.log("tool.discovered", details={
            "category": category, "tools": tool_names, "session_id": session_id,
        })

        if already_loaded:
            return {
                "status": "already_loaded",
                "category": category,
                "tools_available": tool_names,
                "message": f"Category '{category}' was already loaded. Tools available: {', '.join(tool_names)}",
            }

        return {
            "status": "loaded",
            "category": category,
            "tools_loaded": tool_names,
            "message": f"Loaded {len(tool_names)} tools from '{category}'. You can use them now.",
        }
