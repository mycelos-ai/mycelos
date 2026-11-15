"""The tool-call loop of a chat turn.

Executes one tool call of the main loop (sequential path), emits the
events a tool result carries (widgets, views, suggested actions,
session meta), raises the system permission prompt, and continues the
turn with a routed agent after a handoff. Mixin for
:class:`mycelos.chat.service.ChatService`; split out of
``handle_message`` without behavior changes.
"""

from __future__ import annotations

import json
import logging
import time as _time
from typing import Any

from mycelos.chat.conversation_validator import validate_conversation
from mycelos.chat.events import (
    ChatEvent,
    agent_event,
    done_event,
    error_event,
    step_progress_event,
    system_response_event,
    text_event,
)
from mycelos.chat.tool_result_guard import ToolResultGuard
from mycelos.chat.tool_selection import _ROUTING_CONTROL_TOOLS

_log = logging.getLogger("mycelos.chat.service")
_perf = logging.getLogger("mycelos.perf")


class ToolLoopMixin:
    """Tool-call handling inside :meth:`ChatService.handle_message`."""

    def _log_llm_round(self, session_id: str, round_num: int, response: Any) -> None:
        """Session audit: log one LLM round of the main loop."""
        try:
            self._app.session_store.append_llm_round(
                session_id=session_id,
                round_num=round_num,
                model=response.model,
                tokens_in=getattr(response, "prompt_tokens", 0) or 0,
                tokens_out=getattr(response, "completion_tokens", 0) or 0,
                stop_reason=getattr(response, "stop_reason", "") or ("tool_use" if response.tool_calls else "end_turn"),
            )
        except Exception:
            pass

    def _run_sequential_tool_call(
        self,
        tc: dict,
        conversation: list[dict[str, Any]],
        events: list[ChatEvent],
        guard: ToolResultGuard,
        *,
        session_id: str,
        user_id: str,
        channel: str,
        total_tokens: int,
    ) -> bool:
        """Execute one tool call and append its result to the conversation.

        Returns True when the turn ends with this call (permission prompt,
        action confirmation, or handoff); the caller then returns *events*.
        """
        tool_name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, KeyError):
            args = {}

        events.append(step_progress_event(tool_name, "running"))

        # Session audit: log tool call
        try:
            current_agent_id = self._get_active_agent(session_id) or "mycelos"
            self._app.session_store.append_tool_call(
                session_id=session_id,
                tool_call_id=tc["id"],
                name=tool_name,
                args=args,
                agent=current_agent_id,
            )
        except Exception:
            pass

        # Try to execute — may raise PermissionRequired
        from mycelos.security.permissions import PermissionRequired
        _t_tool = _time.monotonic()
        try:
            try:
                result = self._execute_tool(tool_name, args, user_id=user_id)
            except PermissionRequired:
                raise
            except Exception as _exec_exc:
                import traceback as _tb
                try:
                    self._app.session_store.append_tool_error(
                        session_id=session_id,
                        tool_call_id=tc["id"],
                        name=tool_name,
                        error=str(_exec_exc),
                        traceback=_tb.format_exc(),
                    )
                except Exception:
                    pass
                raise
        except PermissionRequired as perm:
            self._emit_permission_prompt(
                perm, tc, tool_name, args, conversation, events,
                session_id=session_id, user_id=user_id,
            )
            return True

        _perf.info("Tool %s: %.1fs", tool_name, _time.monotonic() - _t_tool)

        # Session audit: log tool result
        try:
            self._app.session_store.append_tool_result(
                session_id=session_id,
                tool_call_id=tc["id"],
                name=tool_name,
                result=result,
                duration_ms=int((_time.monotonic() - _t_tool) * 1000),
            )
        except Exception:
            pass

        # Flush progress events from long-running tools (e.g. create_agent)
        if self._pending_events:
            events.extend(self._pending_events)
            self._pending_events = []

        events.append(step_progress_event(tool_name, "done"))

        self._emit_tool_result_events(tool_name, result, session_id, events)

        # If request_action was called, STOP the tool loop
        if (isinstance(result, dict)
                and result.get("status") == "confirmation_required"
                and tool_name == "request_action"):
            action = result.get("action", "")
            reason = result.get("reason", "")
            events.append(ChatEvent(type="widget", data={"widget": {
                "type": "action_confirm",
                "command": f"/{action}",
                "reason": reason,
                "editable": True,
            }}))
            events.append(done_event())
            return True

        # If a routing tool (handoff / return_to_mycelos) was
        # called, switch agent and continue in the SAME
        # conversation with the new agent's prompt
        if (isinstance(result, dict)
                and result.get("status") == "handoff"
                and tool_name in _ROUTING_CONTROL_TOOLS):
            self._continue_with_routed_agent(
                tc, args, result, conversation, events, guard,
                session_id=session_id, user_id=user_id, channel=channel,
                total_tokens=total_tokens,
            )
            return True

        # Emit any other pending widget events
        pending_widgets = self._pending_widgets.pop(session_id, [])
        for widget in pending_widgets:
            events.append(ChatEvent(type="widget", data={"widget": widget}))

        # Tool result in OpenAI format — LiteLLM handles translation
        result_str = json.dumps(result, ensure_ascii=False)
        if len(result_str) > 4000:
            result_str = result_str[:4000] + "...(truncated)"
        conversation.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": result_str,
        })
        guard.record_tool_result(tc["id"])
        return False

    def _emit_permission_prompt(
        self,
        perm: Any,
        tc: dict,
        tool_name: str,
        args: dict,
        conversation: list[dict[str, Any]],
        events: list[ChatEvent],
        *,
        session_id: str,
        user_id: str,
    ) -> None:
        """Show the system permission prompt and park the tool call."""
        # SYSTEM permission prompt — LLM doesn't see this
        # Determine which agent is requesting
        current_agent = "mycelos"
        if session_id in self._interviews:
            current_agent = "creator"

        events.append(step_progress_event(tool_name, "permission_needed"))
        # Rich text prompt for Terminal/Telegram
        import uuid as _uuid

        from mycelos.i18n import t
        permission_id = _uuid.uuid4().hex[:12]
        agent_name = current_agent.replace("-", " ").title()
        prompt_text = (
            f"**{t('permission.title')}**\n\n"
            f"[{agent_name}] `{perm.tool}`\n"
            f"  `{perm.target}`\n"
            f"  _{perm.reason}_\n\n"
            f"  1. {t('permission.option_allow_session', agent=agent_name)}\n"
            f"  2. {t('permission.option_allow_always', agent=agent_name)}\n"
            f"  3. {t('permission.option_allow_all')}\n"
            f"  4. {t('permission.option_deny')}\n"
            f"  5. {t('permission.option_never', agent=agent_name)}\n\n"
            f"{t('permission.choose')}"
        )
        events.append(system_response_event(prompt_text))
        # Widget for Web Frontend
        events.append(ChatEvent(type="widget", data={"widget": {
            "type": "permission_prompt",
            "tool": perm.tool,
            "action": perm.action,
            "reason": perm.reason,
            "target": perm.target,
            "agent": current_agent,
            "permission_id": permission_id,
        }}))

        self._pending_permission = {
            "tool_name": tool_name,
            "tool_call_id": tc["id"],
            "args": args,
            "permission": perm,
            "session_id": session_id,
            "user_id": user_id,
            "agent_id": current_agent,
            "conversation": conversation,
        }
        self._app.audit.log("permission.required", details={
            "tool": perm.tool, "action": perm.action, "target": perm.target,
        })
        events.append(done_event())

    def _emit_tool_result_events(
        self, tool_name: str, result: Any, session_id: str, events: list[ChatEvent],
    ) -> None:
        """Turn UI markers in a tool result into events (removes the markers)."""
        # session_set → emit session-meta event for live sidebar update
        # Widget tools — emit as widget event (renders as form in Web UI)
        if isinstance(result, dict) and "__widget__" in result:
            widget_type = result.pop("__widget__")
            events.append(ChatEvent(type="widget", data={"widget": {
                "type": widget_type, **result,
            }}))

        # Knowledge views (knowledge_view, Package 8 slice 4): the
        # view goes to the client as a widget; the model only keeps
        # the text summary and sources in its tool result.
        if isinstance(result, dict) and "__view__" in result:
            view_payload = result.pop("__view__")
            # Slice 5: a tool may hand over several views (an
            # approval card plus its import preview).
            for one_view in (view_payload if isinstance(view_payload, list) else [view_payload]):
                if isinstance(one_view, dict):
                    events.append(ChatEvent(type="widget", data={"widget": one_view}))
                    # Kept in the session so a reload shows it again.
                    self._app.session_store.append_view(session_id, one_view)

        # Suggested-actions tools (e.g. ui_open_page) — emit a
        # suggested-actions event so the frontend renders the
        # clickable card. Tool result still goes back to the LLM
        # as the dict (minus the marker), so it knows what was
        # shown.
        if isinstance(result, dict) and "__suggested_actions__" in result:
            suggested = result.pop("__suggested_actions__")
            if isinstance(suggested, list):
                events.append(ChatEvent(type="suggested-actions", data={
                    "actions": suggested,
                }))

        if tool_name == "session_set" and isinstance(result, dict) and "title" in result:
            events.append(ChatEvent(type="session-meta", data={
                "session_id": result.get("session_id", ""),
                "title": result.get("title", ""),
                "topic": result.get("topic", ""),
            }))

    def _continue_with_routed_agent(
        self,
        tc: dict,
        args: dict,
        result: dict,
        conversation: list[dict[str, Any]],
        events: list[ChatEvent],
        guard: ToolResultGuard,
        *,
        session_id: str,
        user_id: str,
        channel: str,
        total_tokens: int,
    ) -> None:
        """After handoff/return_to_mycelos, let the new agent answer this turn."""
        new_agent_id = self._get_active_agent(session_id)
        new_handlers = self._app.get_agent_handlers()
        new_handler = new_handlers.get(new_agent_id)

        if not new_handler:
            try:
                agent_info = self._app.agent_registry.get(new_agent_id)
                if agent_info and agent_info.get("system_prompt"):
                    from mycelos.agents.handlers.persona_handler import PersonaHandler
                    new_handler = PersonaHandler(
                        self._app, agent_info["id"], agent_info,
                    )
            except Exception:
                _log.debug("No handler or registry entry for %s", new_agent_id)

        # Friendly transition line — i18n UI string, the
        # routed agent's own reply stays LLM-generated.
        from mycelos.i18n import t as _t
        if new_handler:
            transition_key = (
                "chat.handoff_return" if new_agent_id == "mycelos"
                else "chat.handoff_transition"
            )
            events.append(system_response_event(
                _t(transition_key, agent=new_handler.display_name)
            ))
        else:
            events.append(system_response_event(result.get("message", "")))

        if new_handler:
            events.append(agent_event(new_handler.display_name))

            # Swap the system prompt in the existing conversation
            new_prompt = new_handler.get_system_prompt()
            if conversation and conversation[0].get("role") == "system":
                conversation[0]["content"] = new_prompt
            else:
                conversation.insert(0, {"role": "system", "content": new_prompt})

            # Build a rich tool result that gives the new agent
            # full context — no fake user messages needed
            handoff_reason = args.get("reason", "")
            handoff_summary = args.get("summary", args.get("context", ""))
            user_name_val = self._app.memory.get("default", "system", "user.name") or ""
            user_lang = self._app.memory.get(
                "default", "system", "user.preference.language"
            ) or "en"

            tool_result_content = {
                "status": "handoff_accepted",
                "instruction": (
                    "You are now the active agent for this conversation. "
                    "Continue helping the user with their request. "
                    "The conversation history above is yours to work with."
                ),
                "reason": handoff_reason,
                "user_name": user_name_val,
                "user_language": user_lang,
            }
            if handoff_summary:
                tool_result_content["context"] = handoff_summary

            conversation.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(tool_result_content, ensure_ascii=False),
            })
            guard.record_tool_result(tc["id"])

            new_tools = self._get_session_tools(
                new_handler, session_id, user_id=user_id, channel=channel,
            )
            # Augment with MCP connector tools if agent uses them
            new_tool_names = {t["function"]["name"] for t in new_tools}
            if "connector_tools" in new_tool_names or "connector_call" in new_tool_names:
                new_tools = self._augment_tools_with_connectors(new_tools)
            _t_handoff = _time.monotonic()
            try:
                # Tool loop for the new agent (max 10 rounds)
                handoff_tokens = 0
                for _handoff_round in range(10):
                    new_response = self._complete_for_agent(
                        validate_conversation(conversation),
                        new_agent_id, tools=new_tools,
                    )
                    handoff_tokens += new_response.total_tokens
                    _perf.info("LLM handoff round %d: %.1fs (agent=%s, tokens=%d)",
                               _handoff_round + 1,
                               _time.monotonic() - _t_handoff, new_agent_id,
                               new_response.total_tokens)

                    # No tool calls → text response, we're done
                    if not new_response.tool_calls:
                        new_content = new_response.content or ""
                        # Skip empty responses — the handoff message
                        # already informed the user.
                        if new_content.strip():
                            conversation.append({"role": "assistant", "content": new_content})
                            self._app.session_store.append_message(
                                session_id, role="assistant", content=new_content,
                                metadata={"agent": new_agent_id},
                            )
                            events.append(text_event(new_content))
                        break

                    # Tool calls → execute each and feed results back
                    conversation.append({
                        "role": "assistant",
                        "tool_calls": new_response.tool_calls,
                        "content": new_response.content or "",
                    })

                    # Log LLM round to session
                    try:
                        self._app.session_store.append_llm_round(
                            session_id, round_num=_handoff_round,
                            model=new_response.model or "",
                            tokens_in=0, tokens_out=0,
                            stop_reason="tool_use",
                        )
                    except Exception:
                        pass

                    for ntc in new_response.tool_calls:
                        ntc_name = ntc["function"]["name"]
                        try:
                            ntc_args = json.loads(ntc["function"]["arguments"])
                        except (json.JSONDecodeError, TypeError):
                            ntc_args = {}

                        # Progress + audit (same as regular loop)
                        events.append(step_progress_event(ntc_name, "running"))
                        try:
                            self._app.session_store.append_tool_call(
                                session_id=session_id,
                                tool_call_id=ntc["id"],
                                name=ntc_name,
                                args=ntc_args,
                                agent=new_agent_id,
                            )
                        except Exception:
                            pass

                        _t_htool = _time.monotonic()
                        try:
                            ntc_result = self._execute_tool(
                                ntc_name, ntc_args,
                                user_id=user_id, session_id=session_id,
                                agent_id=new_agent_id,
                            )
                        except Exception as tool_err:
                            ntc_result = {"error": str(tool_err)}
                            try:
                                import traceback as _tb
                                self._app.session_store.append_tool_error(
                                    session_id=session_id,
                                    tool_call_id=ntc["id"],
                                    name=ntc_name,
                                    error=str(tool_err),
                                    traceback=_tb.format_exc(),
                                )
                            except Exception:
                                pass

                        # Log result to session
                        try:
                            self._app.session_store.append_tool_result(
                                session_id=session_id,
                                tool_call_id=ntc["id"],
                                name=ntc_name,
                                result=ntc_result,
                                duration_ms=int((_time.monotonic() - _t_htool) * 1000),
                            )
                        except Exception:
                            pass

                        events.append(step_progress_event(ntc_name, "done"))

                        conversation.append({
                            "role": "tool",
                            "tool_call_id": ntc["id"],
                            "content": json.dumps(ntc_result, ensure_ascii=False)
                                       if not isinstance(ntc_result, str) else ntc_result,
                        })
                        guard.record_tool_result(ntc["id"])

                events.append(done_event(
                    tokens=total_tokens + handoff_tokens,
                    model=new_response.model,
                ))
            except Exception as e:
                events.append(error_event(str(e)))
                events.append(done_event())
        else:
            events.append(done_event())
