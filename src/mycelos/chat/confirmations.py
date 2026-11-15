"""User confirmations: request_action proposals and system permission prompts.

Mixin for :class:`mycelos.chat.service.ChatService`; split out of
``chat/service.py`` without behavior changes.
"""

from __future__ import annotations

import json

from mycelos.chat.events import (
    ChatEvent,
    agent_event,
    done_event,
    error_event,
    step_progress_event,
    system_response_event,
    text_event,
)
from mycelos.chat.tool_selection import (
    _ALLOWED_ACTION_PREFIXES,
    _BLOCKED_ACTION_PREFIXES,
)


class ConfirmationMixin:
    """User confirmations: request_action proposals and system permission prompts."""

    def _handle_request_action(self, action: str, reason: str) -> dict:
        """Handle a request_action tool call — validate and queue for confirmation."""
        if not action:
            return {"error": "Missing action."}

        action_lower = action.lower().strip()

        # Check blocked actions
        for prefix in _BLOCKED_ACTION_PREFIXES:
            if action_lower.startswith(prefix):
                return {"error": f"Action '{action}' is not allowed. Destructive actions are blocked."}

        # Check allowed actions
        allowed = False
        for prefix in _ALLOWED_ACTION_PREFIXES:
            if action_lower.startswith(prefix):
                allowed = True
                break

        if not allowed:
            return {
                "error": f"Action '{action}' is not in the allowed list.",
                "allowed_prefixes": _ALLOWED_ACTION_PREFIXES,
            }

        # Store as pending action (will be executed on user confirmation)
        # We need the session_id — store it via _current_session_id
        session_id = self._current_session_id
        if session_id:
            self._pending_actions[session_id] = {
                "action": action,
                "reason": reason,
            }

        self._app.audit.log("action.requested", details={
            "action": action, "reason": reason,
        })

        # Emit a widget event for the frontend (confirm dialog)
        # The widget is sent alongside the tool result so both
        # the LLM and the UI can handle it appropriately
        if session_id:
            widget_data = {
                "type": "action_confirm",
                "command": f"/{action}",
                "reason": reason,
                "editable": True,
            }
            # Store widget for the event stream to pick up
            self._pending_widgets.setdefault(session_id, []).append(widget_data)

        return {
            "status": "confirmation_required",
            "action": action,
            "reason": reason,
            "message": (
                f"Action: /{action}\n"
                f"Reason: {reason}\n\n"
                "The user needs to confirm this action. "
                "Ask them to approve."
            ),
        }

    def _handle_permission_response(self, message: str, pending: dict) -> list[ChatEvent]:
        """Handle user response to a system permission prompt.

        Accepts 1-5 (numeric), legacy Y/A/N/!, and PERM:{id}:{value} from web.

        1/Y/Enter → allow_session (this session)
        2/A       → always_allow (agent-scoped permanent)
        3         → allow_all_always (global permanent)
        4/N       → deny (one-time)
        5/!       → never_allow (permanent deny)
        """
        from mycelos.security.permissions import grant_permission

        events: list[ChatEvent] = []
        self._pending_permission = None  # clear pending

        cleaned = message.strip()
        perm = pending["permission"]
        agent_id = pending.get("agent_id", "mycelos")

        # Parse PERM:{id}:{value} from web
        if cleaned.startswith("PERM:"):
            parts = cleaned.split(":")
            if len(parts) >= 3:
                cleaned = parts[2]

        cleaned_lower = cleaned.lower()

        # Map input to decision
        if cleaned in ("1",) or cleaned_lower in ("", "y", "yes", "ja", "ok"):
            decision = "allow_session"
        elif cleaned in ("2",) or cleaned_lower in ("a", "always", "immer"):
            decision = "always_allow"
        elif cleaned == "3":
            decision = "allow_all_always"
        elif cleaned in ("4",) or cleaned_lower in ("n", "no", "nein"):
            decision = "deny"
        elif cleaned in ("5",) or cleaned_lower in ("!", "never", "nie"):
            decision = "never_allow"
        else:
            decision = "allow_session"  # Default: session-scoped

        # Grant/deny permission — all decisions go through grant_permission
        # (deny/never_allow return "Permission denied." without executing the action)
        result = grant_permission(
            self._app, perm, decision,
            agent_id=agent_id,
            session_grants=self._session_grants,
        )

        if decision in ("deny", "never_allow"):
            self._app.audit.log("permission.denied", details={
                "tool": perm.tool, "action": perm.action,
            })
            # Insert synthetic tool_result so conversation stays valid for Anthropic API
            conversation = pending.get("conversation", [])
            tool_call_id = pending.get("tool_call_id", "denied")
            conversation.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": f"Permission denied: {perm.tool} on {perm.target}",
            })
            session_id = pending.get("session_id", "")
            if session_id:
                self._conversations[session_id] = conversation
            events.append(system_response_event(result))
            events.append(done_event())
            return events
        events.append(system_response_event(f"Granted: {result}"))

        # Re-execute the original tool call
        session_id = pending["session_id"]
        user_id = pending["user_id"]
        conversation = pending["conversation"]
        tool_name = pending["tool_name"]
        tool_call_id = pending.get("tool_call_id", "retry")
        args = pending["args"]

        # Now try the tool again
        try:
            tool_result = self._execute_tool(tool_name, args, user_id=user_id)
            events.append(step_progress_event(tool_name, "done"))

            result_str = json.dumps(tool_result, ensure_ascii=False)
            if len(result_str) > 4000:
                result_str = result_str[:4000] + "...(truncated)"

            # Fix conversation: the assistant message with tool_calls needs
            # a matching tool_result. Remove the dangling assistant message
            # and rebuild correctly.
            # Find and remove the last assistant message with tool_calls
            while conversation and conversation[-1].get("role") == "assistant":
                last = conversation[-1]
                if last.get("tool_calls"):
                    conversation.pop()
                    break
                else:
                    break

            # Inject the tool result as a system context message
            # (avoids the tool_use/tool_result pairing issue)
            conversation.append({
                "role": "user",
                "content": (
                    f"[System: Permission was granted. Tool '{tool_name}' was re-executed. "
                    f"Result: {result_str[:2000]}]"
                ),
            })

            # Let LLM continue — validate conversation to fix any
            # tool_use/tool_result pairing issues from the permission interruption
            from mycelos.chat.conversation_validator import validate_conversation
            conversation = validate_conversation(conversation)
            self._conversations[session_id] = conversation

            # Use the active agent's tools, not default Mycelos tools
            active_agent_id = self._get_active_agent(session_id)
            handlers = self._app.get_agent_handlers()
            handler = handlers.get(active_agent_id, handlers.get("mycelos"))
            tools = self._get_session_tools(handler, session_id, user_id=user_id) if handler else []

            response = self._complete_for_agent(conversation, active_agent_id, tools=tools)

            content = response.content or ""
            conversation.append({"role": "assistant", "content": content})
            self._app.session_store.append_message(
                session_id, role="assistant", content=content,
            )
            events.append(agent_event("Mycelos"))
            events.append(text_event(content))
            events.append(done_event(tokens=response.total_tokens, model=response.model))

        except Exception as e:
            events.append(error_event(f"Tool re-execution failed: {e}"))
            events.append(done_event())

        return events

    def _execute_pending_action(
        self, session_id: str, pending: dict, conversation: list[dict]
    ) -> list[ChatEvent]:
        """Execute a confirmed pending action, then let LLM continue."""
        from mycelos.chat.slash_commands import handle_slash_command

        action = pending["action"]
        reason = pending["reason"]
        del self._pending_actions[session_id]

        events: list[ChatEvent] = [agent_event("Mycelos")]

        # Execute as slash command
        command = f"/{action}"
        result = handle_slash_command(self._app, command)

        self._app.audit.log("action.executed", details={
            "action": action, "reason": reason, "result": result[:200],
        })

        # Show the result
        events.append(system_response_event(f"Done: {result}"))

        # Inject result into conversation so LLM can continue
        conversation.append({
            "role": "assistant",
            "content": f"[System executed: /{action}]\nResult: {result}",
        })

        # Now re-run the LLM so it can continue with the original task
        # (e.g., now that the mount is granted, list the directory)
        try:
            active_agent_id = self._get_active_agent(session_id)
            handlers = self._app.get_agent_handlers()
            handler = handlers.get(active_agent_id, handlers.get("mycelos"))
            tools = self._get_session_tools(handler, session_id) if handler else []
            response = self._complete_for_agent(conversation, active_agent_id, tools=tools)

            if response.tool_calls:
                # LLM wants to call tools — process them
                continuation = self.handle_message(
                    "continue",  # trigger continuation
                    session_id=session_id,
                    user_id="default",
                )
                events.extend([e for e in continuation if e.type not in ("agent",)])
            else:
                # LLM has a text response
                content = response.content or ""
                conversation.append({"role": "assistant", "content": content})
                self._app.session_store.append_message(
                    session_id, role="assistant", content=content,
                )
                events.append(text_event(content))
                events.append(done_event(tokens=response.total_tokens, model=response.model))
        except Exception:
            events.append(done_event())

        return events
