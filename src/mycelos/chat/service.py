"""ChatService — channel-agnostic message handler with Tool Use.

The Chat-Agent ("Mycelos") is the primary interface. It can:
- Answer questions directly (LLM)
- Use tools (search.web, http.get) for simple tasks
- Hand off to Planner for complex workflows
- Hand off to Creator for new agent creation

The service is composed of focused mixins; this module keeps the
per-request state and the turn orchestration in ``handle_message``:

- ``context_injection``  — notices, hydration, KB/task/onboarding context
- ``attachments``        — session attachments as Multi-Part content
- ``routing``            — active agent, handoffs, per-agent tools/models
- ``tool_loop``          — one tool call of the loop, result events, handoff turn
- ``tool_execution``     — security gate around every tool call
- ``confirmations``      — request_action and permission prompts
- ``workflow_commands``  — /run and paused workflow runs
- ``creator_interview``  — Creator-Agent interview + pipeline
- ``compat``             — shims and setup hints kept for callers
- ``tool_selection``, ``guards``, ``system_info`` — module-level helpers
"""

from __future__ import annotations

import logging as _tlog
import time as _time
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
from threading import Lock, RLock
from typing import Any

from mycelos.app import App
from mycelos.chat.attachments import AttachmentContextMixin
from mycelos.chat.compat import CompatShimsMixin
from mycelos.chat.confirmations import ConfirmationMixin
from mycelos.chat.context_injection import ContextInjectionMixin
from mycelos.chat.conversation_validator import validate_conversation
from mycelos.chat.creator_interview import CreatorInterviewMixin
from mycelos.chat.events import (
    ChatEvent,
    agent_event,
    citations_event,
    done_event,
    error_event,
    step_progress_event,
    suggested_actions_event,
    system_response_event,
    text_event,
)

# Re-exports: tests and other modules import these from here.
from mycelos.chat.guards import (  # noqa: F401
    _ALLOWED_MEMORY_CATEGORIES,
    _INJECTION_PATTERNS,
    _MAX_MEMORY_VALUE_LENGTH,
    _sanitize_dict,
    _validate_memory_write,
)
from mycelos.chat.routing import AgentRoutingMixin
from mycelos.chat.system_info import _build_system_info  # noqa: F401
from mycelos.chat.tool_execution import ToolExecutionMixin
from mycelos.chat.tool_loop import ToolLoopMixin
from mycelos.chat.tool_result_guard import ToolResultGuard, validate_tool_calls
from mycelos.chat.tool_selection import (  # noqa: F401
    _ALLOWED_ACTION_PREFIXES,
    _ATTACHMENT_BUDGET_FRACTION,
    _BLOCKED_ACTION_PREFIXES,
    _BUILDER_ONLY_TOOLS,
    _CHAT_CONTEXT_WINDOW,
    _CONNECTOR_GATED_TOOLS,
    _ROUTING_CONTROL_TOOLS,
    _SYSTEM_AGENT_IDS,
    CHAT_AGENT_TOOLS,
    MAX_TOOL_CONCURRENCY,
    MAX_TOOL_ROUNDS,
    _ChatAgentToolsProxy,
    _get_chat_agent_tools,
    _partition_tool_calls,
)
from mycelos.chat.workflow_commands import WorkflowCommandsMixin
from mycelos.orchestrator import is_plan_confirmation
from mycelos.prompts import PromptLoader

_prompt_loader = PromptLoader()
_perf = _tlog.getLogger("mycelos.perf")
_log = _tlog.getLogger("mycelos.chat.service")


def _serialized_session(method):
    @wraps(method)
    def run(self, message, session_id, *args, **kwargs):
        with self._session_locks_guard:
            lock = self._session_locks.setdefault(session_id, RLock())
        with lock:
            return method(self, message, session_id, *args, **kwargs)
    return run


class ChatService(
    ContextInjectionMixin,
    AttachmentContextMixin,
    AgentRoutingMixin,
    ToolLoopMixin,
    ToolExecutionMixin,
    ConfirmationMixin,
    WorkflowCommandsMixin,
    CreatorInterviewMixin,
    CompatShimsMixin,
):
    """Channel-agnostic chat message handler with tool use.

    The Chat-Agent ("Mycelos") handles all user interaction:
    - Simple questions: direct LLM response
    - Web search/fetch: executes tools inline
    - Complex tasks: hands off to Planner
    - New agents: hands off to Creator
    """

    def __init__(self, app: App) -> None:
        self._app = app
        self._request_session = ContextVar(f"chat_session_{id(self)}", default="")
        self._request_user = ContextVar(f"chat_user_{id(self)}", default="default")
        self._request_events = ContextVar(f"chat_events_{id(self)}", default=None)
        self._permissions_by_session: dict[str, dict] = {}
        self._session_locks: dict[str, Any] = {}
        self._session_locks_guard = Lock()
        self._conversations: dict[str, list[dict[str, Any]]] = {}
        # Active interview sessions per session_id
        self._interviews: dict[str, Any] = {}  # session_id -> InterviewEngine
        # Pending action confirmations per session_id
        self._pending_actions: dict[str, dict[str, Any]] = {}  # session_id -> {action, reason}
        # Session-scoped permission grants (lost on restart)
        self._session_grants: set[str] = set()
        # Lazy Tool Discovery: extra tool categories discovered mid-session
        self._session_extra_tools: dict[str, set[str]] = {}  # session_id → set of category names
        # Per-session set of attachment filenames the agent has explicitly
        # asked to keep in the next turn's context, even if budget pressure
        # would normally evict them. Populated by attachment_load tool;
        # consumed (and cleared) by _build_attachment_blocks + handle_message
        # on the next successful LLM call.
        self._session_force_include: dict[str, set[str]] = {}
        # session_id → currently-active agent_id (handoff cache)
        self._active_agents_cache: dict[str, str] = {}
        # session_id → message-counter for periodic task-reminder injection
        self._task_reminder_counter: dict[str, int] = {}
        # session_ids that explicitly skipped onboarding flow this run
        self._onboarding_skipped: set[str] = set()
        # Pending permission prompt awaiting the user's reply ({session_id, …})
        self._pending_permission: dict[str, Any] | None = None
        # Events queued by long-running tools for the next flush point
        self._pending_events: list[ChatEvent] = []
        # session_id → list of widget payloads queued for the next response
        self._pending_widgets: dict[str, list] = {}
        # Bound by gateway/server.py at startup when Huey is configured;
        # left None when running without a background-task queue.
        self._pipeline_task: Any | None = None
        # Set per-request by handle_message so request_action and tool
        # context dispatch can resolve the current session/user.
        self._current_session_id: str = ""
        self._current_user_id: str = "default"

    @property
    def _current_session_id(self):
        return self._request_session.get()

    @_current_session_id.setter
    def _current_session_id(self, value):
        self._request_session.set(value)

    @property
    def _current_user_id(self):
        return self._request_user.get()

    @_current_user_id.setter
    def _current_user_id(self, value):
        self._request_user.set(value)

    @property
    def _pending_events(self):
        events = self._request_events.get()
        if events is None:
            events = []
            self._request_events.set(events)
        return events

    @_pending_events.setter
    def _pending_events(self, value):
        self._request_events.set(value)

    @property
    def _pending_permission(self):
        return self._permissions_by_session.get(self._current_session_id)

    @_pending_permission.setter
    def _pending_permission(self, value):
        if value is None:
            self._permissions_by_session.pop(self._current_session_id, None)
        else:
            session_id = value.get("session_id") or self._current_session_id
            self._permissions_by_session[session_id] = value
            if not self._current_session_id:
                self._current_session_id = session_id

    def create_session(self, user_id: str = "default") -> str:
        """Create a new chat session."""
        session_id = self._app.session_store.create_session(user_id=user_id)
        self._conversations[session_id] = []
        return session_id

    def resume_session(self, session_id: str) -> list[dict[str, Any]]:
        """Resume an existing session, loading its messages."""
        messages = self._app.session_store.load_messages(session_id)
        self._conversations[session_id] = [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]
        return messages

    def get_system_prompt(self, user_name: str | None = None, channel: str = "api") -> str:
        """Build the system prompt with dynamic context.

        Uses build_prompt_variables() for all dynamic content, then adds
        channel-specific instructions on top.
        """
        from mycelos.prompts import build_prompt_variables

        variables = build_prompt_variables(self._app)

        # Channel-specific instructions
        try:
            channel_prompt = _prompt_loader.load(f"mycelos-channel-{channel}")
        except FileNotFoundError:
            channel_prompt = _prompt_loader.load("mycelos-channel-api")
        variables["channel_prompt"] = channel_prompt

        return _prompt_loader.load("mycelos", **variables)

    @_serialized_session
    def handle_message(
        self,
        message: str,
        session_id: str,
        user_id: str = "default",
        channel: str = "api",
        workflow_run_id: str | None = None,
        target_agent_id: str | None = None,
    ) -> list[ChatEvent]:
        """Process a user message and return response events."""
        events: list[ChatEvent] = []
        self._current_session_id = session_id  # for request_action
        self._current_user_id = user_id
        self._current_channel = channel  # tools adapt cards to the channel
        self._pending_events = []

        self._notify_background_tasks(user_id, events)
        self._check_daily_cost(events)

        conversation = self._load_conversation(session_id)

        # Add system prompt if conversation has no system message yet.
        # The legacy `[System:` marker pipeline is gone — file uploads
        # are now ridden along as Anthropic Multi-Part content via
        # _build_attachment_blocks below.
        if not conversation or conversation[0].get("role") != "system":
            user_name = self._app.memory.get("default", "system", "user.name")
            conversation.insert(0, {
                "role": "system",
                "content": self.get_system_prompt(user_name, channel=channel),
            })

        user_content = self._build_user_content(session_id, message)
        conversation.append({"role": "user", "content": user_content})
        # Persist the pure text version — Multi-Part content can't be
        # cleanly replayed in JSONL conversation history, and the binary
        # data is still on disk in the session attachments folder.
        self._app.session_store.append_message(session_id, role="user", content=message)

        self._auto_title_session(session_id, message)

        # Knowledge Base context enrichment. ``citations`` are exactly the
        # notes injected, sent to the client so the answer can name its
        # real sources.
        citations = self._inject_knowledge_context(message, conversation)
        self._inject_task_reminders(session_id, conversation)
        self._inject_onboarding(message, session_id, conversation, events)

        # Check for pending PERMISSION prompt FIRST (system-level, not LLM)
        pending_perm = self._pending_permission
        if pending_perm and pending_perm.get("session_id") == session_id:
            return self._handle_permission_response(message, pending_perm)

        # Check for pending action confirmation
        pending_action = self._pending_actions.get(session_id)
        if pending_action and is_plan_confirmation(message):
            return self._execute_pending_action(session_id, pending_action, conversation)

        # Check for active interview session
        if session_id in self._interviews:
            return self._handle_interview_message(message, session_id, conversation)

        # Explicit agent switch: sidebar click on a customer-facing agent.
        # We perform a direct handoff before routing this message, so the
        # very first message is handled by the chosen agent.
        if target_agent_id:
            try:
                current = self._get_active_agent(session_id)
                if current != target_agent_id:
                    handoff_result = self._execute_handoff(
                        session_id, target_agent_id, reason="user selected via sidebar"
                    )
                    if handoff_result.get("error"):
                        events.append(system_response_event(handoff_result["error"]))
                        return events
            except Exception:
                _log.debug("Direct target_agent_id handoff failed", exc_info=True)

        # Explicit resume: sidebar click passes the exact run_id to resume.
        if workflow_run_id:
            try:
                target = self._app.workflow_run_manager.get(workflow_run_id)
                if target and target["status"] in ("paused", "waiting_input"):
                    return self._resume_workflow(target, message, session_id)
                # Fall through to normal chat if the run is gone or already running.
            except Exception:
                _log.debug("Explicit workflow_run_id lookup failed", exc_info=True)

        # Check if user is responding to a paused workflow (implicit auto-resume)
        try:
            pending_runs = self._app.workflow_run_manager.get_pending_runs()
            waiting = [r for r in pending_runs if r["status"] == "waiting_input"]
            if waiting:
                return self._resume_workflow(waiting[0], message, session_id)
        except Exception:
            _log.debug("Failed to check for paused workflow runs", exc_info=True)

        # /run <workflow> — deterministic workflow execution with progress + persistence
        if message.startswith("/run ") or message.strip() == "/run":
            return self._handle_run_command(message, session_id, events)

        user_name = self._app.memory.get("default", "system", "user.name")

        # Get active agent handler
        active_agent_id = self._get_active_agent(session_id)
        handlers = self._app.get_agent_handlers()
        handler = handlers.get(active_agent_id, handlers["mycelos"])

        if not events:
            events.append(agent_event(handler.display_name))

        # Use agent-specific system prompt + channel-specific instructions
        agent_prompt = handler.get_system_prompt()
        try:
            channel_prompt = _prompt_loader.load(f"mycelos-channel-{channel}")
        except FileNotFoundError:
            channel_prompt = _prompt_loader.load("mycelos-channel-api")
        agent_prompt += "\n\n" + channel_prompt
        if conversation and conversation[0].get("role") == "system":
            conversation[0]["content"] = agent_prompt
        else:
            conversation.insert(0, {"role": "system", "content": agent_prompt})

        # Use agent-specific tools (includes handoff tool)
        # Session-aware: budget-based basis-set + discovered extras for Mycelos
        tools = self._get_session_tools(handler, session_id, user_id=user_id, channel=channel)

        # Add MCP connector tools if the agent has connector_tools/connector_call in its tool list
        tool_names = {t["function"]["name"] for t in tools}
        if "connector_tools" in tool_names or "connector_call" in tool_names or active_agent_id == "mycelos":
            tools = self._augment_tools_with_connectors(tools)

        # Use agent-specific model (None = system default)
        agent_model = self._get_model_for_agent(active_agent_id)

        # Name detection for new users
        if not user_name and len(conversation) <= 6:
            self._try_save_name(message)

        # LLM completion with tool use loop
        total_tokens = 0
        total_cost = 0.0
        model_used = ""
        try:
            guard = ToolResultGuard()
            conversation = validate_conversation(conversation)

            # Auto-compact if conversation is getting too long
            from mycelos.chat.compaction import compact_conversation, needs_compaction
            if needs_compaction(conversation, model=agent_model or ""):
                _perf.info("Auto-compacting conversation (%d messages)", len(conversation))
                conversation = compact_conversation(
                    conversation,
                    self._app.llm,
                    model=agent_model or "",
                    summary_model=self._app.resolve_cheapest_model(),
                )
                events.append(step_progress_event("auto-compact", "done"))

            self._conversations[session_id] = conversation
            _t0 = _time.monotonic()
            response = self._complete_for_agent(
                conversation, active_agent_id, tools=tools
            )
            _perf.info("LLM initial call: %.1fs (agent=%s, model=%s, tokens=%d)",
                       _time.monotonic() - _t0, active_agent_id, response.model, response.total_tokens)
            total_tokens += response.total_tokens
            _resp_cost = getattr(response, 'cost', 0.0)
            total_cost += _resp_cost if isinstance(_resp_cost, (int, float)) else 0.0
            model_used = response.model

            self._log_llm_round(session_id, 0, response)

            # Validate tool calls from initial response
            if response.tool_calls:
                response.tool_calls = validate_tool_calls(response.tool_calls)

            # Tool-call loop: execute tools and feed results back to LLM
            rounds = 0
            while response.tool_calls and rounds < MAX_TOOL_ROUNDS:
                rounds += 1

                # Build assistant message with tool_calls in OpenAI format
                # LiteLLM translates this to Anthropic's content blocks
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": tc["function"],
                        }
                        for tc in response.tool_calls
                    ],
                }
                # Anthropic requires content to be None or absent when tool_calls present
                if response.content:
                    assistant_msg["content"] = response.content
                conversation.append(assistant_msg)
                guard.track_tool_calls(response.tool_calls)

                # Execute tool calls — parallel for concurrent-safe batches
                _special_tools = {"handoff", "return_to_mycelos", "request_action", "session_set"}
                batches = _partition_tool_calls(response.tool_calls)
                for batch in batches:
                    # Parallel execution for multi-tool concurrent-safe batches
                    if len(batch) > 1 and not any(tc["function"]["name"] in _special_tools for tc in batch):
                        self._execute_batch_parallel(
                            batch, conversation, events, user_id, session_id,
                        )
                        continue
                    # Sequential execution (single tools or special tools)
                    for tc in batch:
                        turn_ended = self._run_sequential_tool_call(
                            tc, conversation, events, guard,
                            session_id=session_id, user_id=user_id,
                            channel=channel, total_tokens=total_tokens,
                        )
                        if turn_ended:
                            return events

                # Flush any missing tool results before calling LLM again
                if guard.has_pending:
                    conversation.extend(guard.flush_pending())

                # Continue conversation — LLM sees tool results now
                conversation = validate_conversation(conversation)
                self._conversations[session_id] = conversation
                response = self._complete_for_agent(
                    conversation, active_agent_id, tools=tools
                )
                total_tokens += response.total_tokens
                _resp_cost = getattr(response, 'cost', 0.0)
                total_cost += _resp_cost if isinstance(_resp_cost, (int, float)) else 0.0

                self._log_llm_round(session_id, rounds, response)

                # Validate tool calls from loop response
                if response.tool_calls:
                    response.tool_calls = validate_tool_calls(response.tool_calls)
                    if not response.tool_calls:
                        # All tool calls were invalid — treat as text-only response
                        break

        except Exception as exc:
            # Don't pop the user message — it was valid, the error is in processing
            events.append(error_event(f"LLM error: {exc}"))
            return events

        # Force-include flag consumed — only clear after LLM call succeeded.
        self._session_force_include.pop(session_id, None)

        # Final text response — sanitize to prevent credential leakage from LLM
        from mycelos.security.sanitizer import ResponseSanitizer
        assistant_content = ResponseSanitizer().sanitize_text(response.content or "")
        conversation.append({"role": "assistant", "content": assistant_content})

        answer_meta: dict[str, Any] = {"tokens": total_tokens, "model": model_used}
        if citations:
            answer_meta["citations"] = citations
        self._app.session_store.append_message(
            session_id, role="assistant", content=assistant_content,
            metadata=answer_meta,
        )

        if citations:
            events.append(citations_event(citations))
        events.append(text_event(assistant_content))

        # Auto-detect commands in LLM response → suggested action buttons
        from mycelos.chat.confirmable import extract_suggested_commands
        suggested_cmds = extract_suggested_commands(assistant_content)
        if suggested_cmds:
            actions = []
            for cmd in suggested_cmds[:5]:  # max 5 buttons
                # Determine if this is a prefill (needs user input) or direct execute
                needs_input = cmd.rstrip().endswith(">") or "<" in cmd
                clean_cmd = cmd.split("<")[0].rstrip() + " " if needs_input else cmd
                label = cmd if len(cmd) < 40 else cmd[:37] + "..."
                actions.append({
                    "label": label,
                    "command": clean_cmd if needs_input else cmd,
                    "prefill": needs_input,
                })
            events.append(suggested_actions_event(actions))

        events.append(done_event(tokens=total_tokens, model=model_used, cost=total_cost))

        return events

    def _check_daily_cost(self, events: list[ChatEvent]) -> None:
        """Warn when today's LLM cost crosses 5/10/25 USD."""
        # Daily cost check
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            rows = self._app.storage.fetchall(
                "SELECT COALESCE(SUM(cost), 0) as total FROM llm_usage WHERE created_at >= ?",
                (today,),
            )
            daily_cost = rows[0]["total"] if rows else 0
            if daily_cost >= 25.0:
                events.append(system_response_event(
                    f"[Cost Alert] Today's LLM cost: ${daily_cost:.2f} — consider reviewing usage."
                ))
            elif daily_cost >= 10.0:
                events.append(system_response_event(
                    f"[Cost Warning] Today's LLM cost: ${daily_cost:.2f}"
                ))
            elif daily_cost >= 5.0:
                events.append(system_response_event(
                    f"[Cost Info] Today's LLM cost: ${daily_cost:.2f}"
                ))
        except Exception:
            _log.debug("Failed to check daily LLM cost", exc_info=True)

    def _build_user_content(self, session_id: str, message: str) -> list[dict] | str:
        """Build the user turn: current time hint, attachments, parked stubs."""
        # Add user message. We prepend the current local time as a short
        # system-style hint so the LLM always knows "now" — the system
        # prompt is only rebuilt at session start, which means relative
        # phrasing like "in 5 minutes" would otherwise drift once the
        # session has been alive for a while. The persisted session
        # store deliberately keeps the *pure* user text so replays,
        # audits and exports stay clean.
        try:
            now = datetime.now()
            time_prefix = f"[current time: {now.strftime('%Y-%m-%d %H:%M')}]\n"
        except Exception:
            time_prefix = ""

        # Multi-Part: stitch session attachments into the user message
        # so the agent can read them natively. Eviction handles token
        # budget pressure; force_include flags (set by attachment_load)
        # protect specific files from eviction.
        attachment_blocks, evicted = self._build_attachment_blocks(
            session_id=session_id,
            budget_tokens=int(_CHAT_CONTEXT_WINDOW * _ATTACHMENT_BUDGET_FRACTION),
        )
        evicted_stubs = "\n".join(
            f"[Attachment '{name}' parked — call attachment_load('{name}') "
            f"to bring it back into context.]"
            for name in evicted
        )
        text_part = (evicted_stubs + "\n\n" + time_prefix + message).strip()

        if attachment_blocks:
            user_content: list[dict] | str = attachment_blocks + [
                {"type": "text", "text": text_part}
            ]
        elif evicted:
            # Everything was evicted — keep the stubs so the agent can
            # call attachment_load to bring files back.
            user_content = (evicted_stubs + "\n\n" + time_prefix + message).strip()
        else:
            user_content = time_prefix + message
        return user_content
