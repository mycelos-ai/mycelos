"""ChatService — channel-agnostic message handler with Tool Use.

The Chat-Agent ("Mycelos") is the primary interface. It can:
- Answer questions directly (LLM)
- Use tools (search.web, http.get) for simple tasks
- Hand off to Planner for complex workflows
- Hand off to Creator for new agent creation
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from mycelos.app import App
from mycelos.chat.conversation_validator import validate_conversation
from mycelos.prompts import PromptLoader

_prompt_loader = PromptLoader()
from mycelos.chat.tool_result_guard import ToolResultGuard, validate_tool_calls
from mycelos.chat.events import (
    ChatEvent,
    agent_event,
    done_event,
    error_event,
    session_event,
    step_progress_event,
    suggested_actions_event,
    system_response_event,
    text_event,
)
from concurrent.futures import ThreadPoolExecutor, as_completed

from mycelos.orchestrator import is_plan_confirmation
from mycelos.tools.registry import ToolPermission, ToolRegistry

import logging as _tlog
_perf = _tlog.getLogger("mycelos.perf")
_log = _tlog.getLogger("mycelos.chat.service")

# Backwards-compatible alias: tools are now defined in mycelos.tools.*
# but many tests and modules import CHAT_AGENT_TOOLS from here.

# Builder-only tools — only loaded when Builder agent is active.
_BUILDER_ONLY_TOOLS = {
    "create_workflow",        # 550 tokens — building workflows is Builder's job
}

# Web-UI-only tools — only loaded when channel="api" (web client).
# In CLI/Telegram these widgets don't render, so the LLM doesn't need them.
_WEB_UI_ONLY_TOOLS = {
    "show_connector_setup",   # 161 tokens — renders setup form in web chat
    "show_credential_input",  # 143 tokens — renders secure credential input
}

# Connector-gated tools — only loaded when the corresponding connector is active.
_CONNECTOR_GATED_TOOLS = {
    "email": {"email_inbox", "email_search", "email_read", "email_send", "email_count"},
}


def _get_chat_agent_tools(
    app: Any = None,
    channel: str = "api",
    user_id: str = "default",
    agent_id: str = "mycelos",
    session_extras: set[str] | None = None,
) -> list[dict]:
    """Return the chat agent tool list from the ToolRegistry.

    When *app* has a storage backend, uses budget-aware session loading
    (``get_tools_for_session``) which sends core + adaptive basis-set.
    Falls back to the full ``get_tools_for`` when storage is unavailable.

    *session_extras*: category names discovered mid-session via
    ``discover_tools``.  Tools from these categories are merged in on
    top of the basis-set.

    Excludes:
    - Dynamic tools (connector_tools, connector_call, github_api) injected at runtime
    - handoff (added dynamically by handlers)
    - Builder-only tools (create_workflow) -- saves ~550 tokens per call
    - Web-UI widgets (show_*) when channel != "api"
    - Connector-gated tools (email_*) -- only included if the connector is active
    """
    _STATIC_TOOL_NAMES = {
        "search_web", "search_news", "http_get",
        "memory_read", "memory_write",
        "filesystem_read", "filesystem_write", "filesystem_list",
        "search_mcp_servers", "system_status", "workflow_info",
        "create_schedule", "run_workflow",
        "note_write", "note_read", "note_search", "note_list",
        "note_update", "note_link",
        "note_done", "note_remind", "note_move",
        "file_analyze", "file_manage",
        "email_inbox", "email_search", "email_read", "email_send", "email_count",
        "session_set", "session_list",
        "show_connector_setup", "show_credential_input",
    }

    # Determine which connector-gated tools to include
    active_connector_ids: set[str] = set()
    if app is not None:
        try:
            for c in app.connector_registry.list_connectors(status="active"):
                active_connector_ids.add(c.get("id", ""))
        except Exception:
            pass

    excluded_gated: set[str] = set()
    for connector_id, gated_tools in _CONNECTOR_GATED_TOOLS.items():
        if connector_id not in active_connector_ids:
            excluded_gated |= gated_tools

    # Budget-aware loading when storage is available
    storage = getattr(app, "storage", None) if app else None
    if storage:
        all_tools = ToolRegistry.get_tools_for_session(
            agent_type="mycelos",
            user_id=user_id,
            agent_id=agent_id,
            storage=storage,
            context_window=200_000,
        )
    else:
        all_tools = ToolRegistry.get_tools_for("mycelos")

    # Merge in discovered session extras
    if session_extras and storage:
        from mycelos.tools.registry import _AGENT_PERMISSIONS
        allowed = _AGENT_PERMISSIONS.get("mycelos", {ToolPermission.OPEN})
        existing_names = {t.get("function", {}).get("name") for t in all_tools}
        for name, entry in ToolRegistry._tools.items():
            cat = entry.get("category")
            if cat in session_extras and entry["permission"] in allowed:
                if name not in existing_names:
                    all_tools.append(entry["schema"])
                    existing_names.add(name)

    # When using session-aware loading, trust the category system for
    # which tools to include.  The _STATIC_TOOL_NAMES allowlist is only
    # needed for the legacy get_tools_for() path (no storage).
    from mycelos.tools.categories import _TOOL_TO_CATEGORY
    use_category_trust = bool(storage)

    result = []
    for t in all_tools:
        name = t["function"]["name"]
        # discover_tools is injected by get_tools_for_session, always allow it
        if name == "discover_tools":
            result.append(t)
            continue
        # Session-aware: allow any tool that has a category assignment
        # (the budget system already filtered appropriately).
        # Legacy path: restrict to the static allowlist.
        if use_category_trust:
            if name not in _STATIC_TOOL_NAMES and name not in _TOOL_TO_CATEGORY:
                continue
        else:
            if name not in _STATIC_TOOL_NAMES:
                continue
        if name in _BUILDER_ONLY_TOOLS:
            continue
        if name in _WEB_UI_ONLY_TOOLS and channel != "api":
            continue
        if name in excluded_gated:
            continue
        result.append(t)
    return result


class _ChatAgentToolsProxy:
    """Lazy proxy so `CHAT_AGENT_TOOLS` works as a module-level list.

    Supports iteration, indexing, and len() — delegates to the registry.
    """

    def __iter__(self):
        return iter(_get_chat_agent_tools())

    def __len__(self):
        return len(_get_chat_agent_tools())

    def __getitem__(self, idx):
        return _get_chat_agent_tools()[idx]

    def __contains__(self, item):
        return item in _get_chat_agent_tools()

    def __add__(self, other):
        return _get_chat_agent_tools() + list(other)

    def __radd__(self, other):
        return list(other) + _get_chat_agent_tools()

    def __repr__(self):
        return repr(_get_chat_agent_tools())


CHAT_AGENT_TOOLS = _ChatAgentToolsProxy()

# Actions the LLM is allowed to propose via request_action
_ALLOWED_ACTION_PREFIXES = [
    "mount add", "mount revoke",
    "connector setup", "connector add",
    "schedule add",
    "memory set",
    "pip install",
    "agent grant", "agent revoke",
]

# Actions that are blocked (destructive)
_BLOCKED_ACTION_PREFIXES = [
    "config rollback",
    "memory clear",
    "connector remove",
    "schedule delete",
    "workflow delete",
]

# Maximum tool-call rounds to prevent infinite loops
MAX_TOOL_ROUNDS = 5

# Maximum concurrent tool executions
MAX_TOOL_CONCURRENCY = 8


def _partition_tool_calls(tool_calls: list[dict]) -> list[list[dict]]:
    """Partition tool calls into batches for parallel execution.

    Consecutive concurrent-safe tools go into the same batch.
    Non-safe tools get their own single-item batch.
    This enables parallel execution of read-only tools while
    preserving order for write tools.

    Example:
        [search_web, http_get, note_write, email_read, email_count]
        → [[search_web, http_get], [note_write], [email_read, email_count]]
    """
    from mycelos.tools.registry import ToolRegistry

    batches: list[list[dict]] = []
    current_batch: list[dict] = []

    for tc in tool_calls:
        name = tc["function"]["name"]
        if ToolRegistry.is_concurrent_safe(name):
            current_batch.append(tc)
        else:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
            batches.append([tc])

    if current_batch:
        batches.append(current_batch)

    return batches


class ChatService:
    """Channel-agnostic chat message handler with tool use.

    The Chat-Agent ("Mycelos") handles all user interaction:
    - Simple questions: direct LLM response
    - Web search/fetch: executes tools inline
    - Complex tasks: hands off to Planner
    - New agents: hands off to Creator
    """

    def __init__(self, app: App) -> None:
        self._app = app
        self._conversations: dict[str, list[dict[str, Any]]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        # Active interview sessions per session_id
        self._interviews: dict[str, Any] = {}  # session_id -> InterviewEngine
        # Pending action confirmations per session_id
        self._pending_actions: dict[str, dict[str, Any]] = {}  # session_id -> {action, reason}
        # Session-scoped permission grants (lost on restart)
        self._session_grants: set[str] = set()
        # Lazy Tool Discovery: extra tool categories discovered mid-session
        self._session_extra_tools: dict[str, set[str]] = {}  # session_id → set of category names

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
            return base
        else:
            try:
                return handler.get_tools(channel=channel)
            except TypeError:
                return handler.get_tools()

    def create_session(self, user_id: str = "default") -> str:
        """Create a new chat session."""
        session_id = self._app.session_store.create_session(user_id=user_id)
        self._conversations[session_id] = []
        return session_id

    def _get_active_agent(self, session_id: str) -> str:
        """Get the active agent for this session. Default: mycelos."""
        if not hasattr(self, '_active_agents_cache'):
            self._active_agents_cache = {}
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
        system_agents = {"mycelos", "builder", "creator", "planner"}
        if target_agent_id not in system_agents:
            agent = self._app.agent_registry.get(target_agent_id)
            if not agent or agent.get("status") != "active" or not agent.get("user_facing"):
                return {"error": f"Agent '{target_agent_id}' is not available for conversation"}

        prev_agent = self._get_active_agent(session_id)

        self._app.storage.execute(
            "INSERT OR REPLACE INTO session_agents (session_id, active_agent_id, handoff_reason) VALUES (?, ?, ?)",
            (session_id, target_agent_id, reason),
        )

        if hasattr(self, '_active_agents_cache'):
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

        # Proactive notification: check for completed background tasks
        try:
            completed = self._app.task_runner.get_completed_unnotified(user_id)
            for task in completed:
                result_data = json.loads(task.get("result", "{}") or "{}")
                summary = result_data.get("summary", f"Background task {task['task_type']} completed.")
                if task.get("status") == "failed":
                    error = task.get("error", "Unknown error")
                    events.append(system_response_event(f"[Background] Task failed: {error}"))
                else:
                    events.append(system_response_event(f"[Background] {summary}"))
                self._app.task_runner.mark_notified(task["id"])
        except Exception:
            _log.debug("Failed to check background task notifications", exc_info=True)

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

        # Ensure conversation exists
        if session_id not in self._conversations:
            self._conversations[session_id] = []
        conversation = self._conversations[session_id]

        # Add system prompt if conversation is empty
        if not conversation:
            user_name = self._app.memory.get("default", "system", "user.name")
            conversation.append({
                "role": "system",
                "content": self.get_system_prompt(user_name, channel=channel),
            })

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
        conversation.append({"role": "user", "content": time_prefix + message})
        self._app.session_store.append_message(session_id, role="user", content=message)

        # Deterministic auto-title: if this session has no title yet, use the
        # first user message so the sidebar shows something meaningful even
        # when the LLM forgets to call session_set(). The LLM may still
        # overwrite the title later via the tool — a manual/LLM-chosen title
        # always wins because this check only fires when title is empty.
        try:
            existing_title = self._app.session_store.get_session_meta(session_id).get("title", "")
            if not existing_title:
                max_len = 60
                stripped = message.strip().replace("\n", " ")
                if len(stripped) > max_len:
                    auto_title = stripped[:max_len].rstrip() + "…"
                else:
                    auto_title = stripped
                if auto_title:
                    self._app.session_store.update_session(session_id, title=auto_title)
        except Exception:
            _log.debug("Auto-title failed", exc_info=True)

        # Knowledge Base context enrichment (hard 5s timeout — must not hang chat)
        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
            kb = self._app.knowledge_base
            with ThreadPoolExecutor(max_workers=1) as _ex:
                _fut = _ex.submit(kb.find_relevant, message, top_k=5, threshold=0.7)
                try:
                    relevant = _fut.result(timeout=5.0)
                except FuturesTimeout:
                    _log.warning("knowledge_base.find_relevant timed out after 5s — skipping KB context")
                    relevant = []
            if relevant:
                context_parts = []
                for note in relevant[:3]:  # Max 3 notes
                    title = note.get("title", "")
                    ntype = note.get("type", "")
                    updated = (note.get("updated_at") or "")[:10]
                    content_preview = (note.get("content") or "")[:500]
                    context_parts.append(
                        f"- **{title}** ({ntype}, {updated})\n  {content_preview}"
                    )
                kb_context = "[Knowledge Context]\nRelevant notes from your knowledge base:\n\n" + "\n\n".join(context_parts)
                conversation.append({"role": "system", "content": kb_context})
        except Exception:
            _log.debug("Failed to retrieve knowledge base context", exc_info=True)

        # Task reminders (overdue + due today) — inject into conversation context
        # so the LLM mentions them naturally in its response
        if not getattr(self, "_task_reminder_counter", None):
            self._task_reminder_counter = {}
        msg_count = self._task_reminder_counter.get(session_id, 0) + 1
        self._task_reminder_counter[session_id] = msg_count
        if msg_count == 1 or msg_count % 10 == 0:
            try:
                # Check for pending timed reminder (from ReminderService)
                pending_reminder = self._app.memory.get("default", "system", "pending_reminder")
                if pending_reminder:
                    conversation.append({
                        "role": "system",
                        "content": (
                            f"[TIMED REMINDER] Deliver this reminder to the user NOW:\n"
                            f"{pending_reminder}"
                        ),
                    })
                    # Clear it so it's only shown once
                    self._app.memory.delete("default", "system", "pending_reminder")

                kb = self._app.knowledge_base
                overdue = kb.list_notes(type="task", status="open", due="overdue")
                due_today = kb.list_notes(type="task", status="open", due="today")
                reminder_items = []
                for t in (overdue or []):
                    reminder_items.append(f"- {t.get('title', '?')} (OVERDUE: {t.get('due', '?')})")
                for t in (due_today or []):
                    reminder_items.append(f"- {t.get('title', '?')} (due today)")
                if reminder_items:
                    conversation.append({
                        "role": "system",
                        "content": (
                            "[TASK REMINDERS] The user has pending tasks. "
                            "Mention them briefly at the start of your response:\n"
                            + "\n".join(reminder_items)
                        ),
                    })
            except Exception:
                _log.debug("Failed to inject task reminders", exc_info=True)

        # First-time user onboarding — only if name is truly unknown
        try:
            onboarding_done = self._app.memory.get("default", "system", "onboarding_completed")
            user_name_known = self._app.memory.get("default", "system", "user.name")
            if not onboarding_done and not user_name_known:
                onboarding_skipped = getattr(self, '_onboarding_skipped', set())
                if session_id not in onboarding_skipped:
                    # Inject onboarding context for the LLM
                    conversation.append({
                        "role": "system",
                        "content": (
                            "[ONBOARDING] This is a NEW USER. Follow these steps IN ORDER, "
                            "one at a time (don't ask everything at once):\n\n"
                            "Step 1: LANGUAGE\n"
                            "  Detect the user's language from their first message. "
                            "Respond in that language.\n\n"
                            "Step 2: INTRODUCE YOURSELF\n"
                            "  Say: 'Hi! I'm Mycelos — your personal AI assistant that "
                            "grows with you. Your data stays local, I use cloud AI as a "
                            "tool — not as storage.'\n"
                            "  Then ask: 'What's your name?'\n\n"
                            "Step 3: YOUR NAME\n"
                            "  After they tell you their name, ask:\n"
                            "  'Nice to meet you, [name]! My default name is Mycelos, "
                            "but you can give me a different name if you like. "
                            "What should I be called?'\n"
                            "  If they say 'Mycelos is fine' or similar, keep it. "
                            "Otherwise save their choice.\n"
                            "  IMPORTANT: The user gives YOU (the agent) a name. "
                            "Don't confuse this with the user's own name.\n"
                            "  If they choose a custom name, confirm: 'From now on I'm [name]! "
                            "The new name will appear in future sessions.'\n\n"
                            "Step 4: GOALS\n"
                            "  Ask what they want to use you for.\n\n"
                            "Step 5: FIRST CAPTURE\n"
                            "  Ask: 'What's on your mind right now? I'll capture it "
                            "for you.' Then use note_write to create their first "
                            "knowledge note.\n\n"
                            "Step 6: SAVE\n"
                            "  Use memory_write to save (use category='fact' for all):\n"
                            "  - key 'user.name' = the HUMAN's name (e.g. 'Stefan')\n"
                            "  - key 'user.language' = detected language (e.g. 'de' or 'en')\n"
                            "  - key 'agent.display_name' = the name the human chose for YOU "
                            "the agent (e.g. 'Fridolin'). Only save if they chose something "
                            "other than 'Mycelos'.\n"
                            "  - key 'onboarding_completed' = 'true'\n\n"
                            "If user says 'skip', still set onboarding_completed=true.\n"
                            "Be warm, concise. ONE question at a time."
                        ),
                    })
                    # Check if user wants to skip
                    if message.strip().lower() in ("skip", "later", "überspringen", "später"):
                        self._app.memory.set("default", "system", "onboarding_completed", "true")
                        if not hasattr(self, '_onboarding_skipped'):
                            self._onboarding_skipped = set()
                        self._onboarding_skipped.add(session_id)
                        events.append(system_response_event(
                            "Onboarding skipped. You can set things up anytime — just ask!"
                        ))
        except Exception:
            _log.debug("Failed during onboarding check", exc_info=True)

        # Level system greeting (show level-up or hint)
        # Shows once per session, but gamification.py controls frequency
        # (beginners see hints every session, advanced users less often)
        try:
            from mycelos.gamification import get_session_greeting
            if not hasattr(self, '_level_shown_sessions'):
                self._level_shown_sessions = set()
            if session_id not in self._level_shown_sessions:
                self._level_shown_sessions.add(session_id)
                greeting = get_session_greeting(self._app, user_id)
                if greeting:
                    events.append(system_response_event(greeting))
        except Exception:
            _log.debug("Failed to generate session greeting", exc_info=True)

        # Check for pending PERMISSION prompt FIRST (system-level, not LLM)
        pending_perm = getattr(self, "_pending_permission", None)
        if pending_perm and pending_perm.get("session_id") == session_id:
            return self._handle_permission_response(message, pending_perm)

        # Check for pending action confirmation
        pending_action = self._pending_actions.get(session_id)
        if pending_action and is_plan_confirmation(message):
            return self._execute_pending_action(session_id, pending_action, conversation)

        # Check for plan confirmation
        pending = self._pending.get(session_id)
        if pending and is_plan_confirmation(message):
            return self._handle_confirmation(session_id, pending, conversation)

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

        # Perf logging
        import time as _time
        import logging as _tlog
        _perf = _tlog.getLogger("mycelos.perf")

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
            from mycelos.chat.compaction import needs_compaction, compact_conversation
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
            response = self._app.llm.complete(
                conversation, tools=tools, model=agent_model
            )
            _perf.info("LLM initial call: %.1fs (agent=%s, model=%s, tokens=%d)",
                       _time.monotonic() - _t0, active_agent_id, response.model, response.total_tokens)
            total_tokens += response.total_tokens
            _resp_cost = getattr(response, 'cost', 0.0)
            total_cost += _resp_cost if isinstance(_resp_cost, (int, float)) else 0.0
            model_used = response.model

            # Session audit: log this LLM round
            try:
                self._app.session_store.append_llm_round(
                    session_id=session_id,
                    round_num=0,
                    model=response.model,
                    tokens_in=getattr(response, "prompt_tokens", 0) or 0,
                    tokens_out=getattr(response, "completion_tokens", 0) or 0,
                    stop_reason=getattr(response, "stop_reason", "") or ("tool_use" if response.tool_calls else "end_turn"),
                )
            except Exception:
                pass

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
                _special_tools = {"handoff", "request_action", "session_set"}
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
                        # SYSTEM permission prompt — LLM doesn't see this
                        # Determine which agent is requesting
                        current_agent = "mycelos"
                        if session_id in self._interviews:
                            current_agent = "creator"

                        events.append(step_progress_event(tool_name, "permission_needed"))
                        # Rich text prompt for Terminal/Telegram
                        from mycelos.i18n import t
                        import uuid as _uuid
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
                        return events

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
                    if hasattr(self, '_pending_events') and self._pending_events:
                        events.extend(self._pending_events)
                        self._pending_events = []

                    events.append(step_progress_event(tool_name, "done"))

                    # session_set → emit session-meta event for live sidebar update
                    # Widget tools — emit as widget event (renders as form in Web UI)
                    if isinstance(result, dict) and "__widget__" in result:
                        widget_type = result.pop("__widget__")
                        events.append(ChatEvent(type="widget", data={"widget": {
                            "type": widget_type, **result,
                        }}))

                    if tool_name == "session_set" and isinstance(result, dict) and "title" in result:
                        events.append(ChatEvent(type="session-meta", data={
                            "session_id": result.get("session_id", ""),
                            "title": result.get("title", ""),
                            "topic": result.get("topic", ""),
                        }))

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
                        return events

                    # If handoff tool was called, switch agent and continue
                    # in the SAME conversation with the new agent's prompt
                    if (isinstance(result, dict)
                            and result.get("status") == "handoff"
                            and tool_name == "handoff"):
                        handoff_msg = result.get("message", "")
                        events.append(system_response_event(handoff_msg))

                        new_agent_id = self._get_active_agent(session_id)
                        new_handlers = self._app.get_agent_handlers()
                        new_handler = new_handlers.get(new_agent_id)

                        # Fallback: if no registered handler, check agent registry
                        # for custom/persona agents with a system_prompt
                        if not new_handler:
                            try:
                                agent_info = self._app.agent_registry.get(new_agent_id)
                                if agent_info and agent_info.get("system_prompt"):
                                    # Create a lightweight dynamic handler
                                    from mycelos.agents.handlers.base import DynamicAgentHandler
                                    new_handler = DynamicAgentHandler(
                                        self._app, agent_info,
                                    )
                            except Exception:
                                _log.debug("No handler or registry entry for %s", new_agent_id)

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
                            new_model = self._get_model_for_agent(new_agent_id)
                            _t_handoff = _time.monotonic()
                            try:
                                # Tool loop for the new agent (max 10 rounds)
                                handoff_tokens = 0
                                for _handoff_round in range(10):
                                    new_response = self._app.llm.complete(
                                        validate_conversation(conversation),
                                        tools=new_tools, model=new_model,
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
                        return events

                    # Emit any other pending widget events
                    pending_widgets = getattr(self, "_pending_widgets", {}).pop(session_id, [])
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

                # Flush any missing tool results before calling LLM again
                if guard.has_pending:
                    conversation.extend(guard.flush_pending())

                # Continue conversation — LLM sees tool results now
                conversation = validate_conversation(conversation)
                self._conversations[session_id] = conversation
                response = self._app.llm.complete(
                    conversation, tools=tools, model=agent_model
                )
                total_tokens += response.total_tokens
                _resp_cost = getattr(response, 'cost', 0.0)
                total_cost += _resp_cost if isinstance(_resp_cost, (int, float)) else 0.0

                # Session audit: log this LLM round
                try:
                    self._app.session_store.append_llm_round(
                        session_id=session_id,
                        round_num=rounds,
                        model=response.model,
                        tokens_in=getattr(response, "prompt_tokens", 0) or 0,
                        tokens_out=getattr(response, "completion_tokens", 0) or 0,
                        stop_reason=getattr(response, "stop_reason", "") or ("tool_use" if response.tool_calls else "end_turn"),
                    )
                except Exception:
                    pass

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

        # Final text response — sanitize to prevent credential leakage from LLM
        from mycelos.security.sanitizer import ResponseSanitizer
        assistant_content = ResponseSanitizer().sanitize_text(response.content or "")
        conversation.append({"role": "assistant", "content": assistant_content})

        self._app.session_store.append_message(
            session_id, role="assistant", content=assistant_content,
            metadata={"tokens": total_tokens, "model": model_used},
        )

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

    def _handle_interview_message(
        self, message: str, session_id: str, conversation: list[dict]
    ) -> list[ChatEvent]:
        """Continue an active Creator interview session.

        Shows [Creator-Agent] attribution. On completion, runs the
        Creator Pipeline and returns a HandoffResult to Mycelos.
        """
        from mycelos.agents.handoff import HandoffResult

        interview_data = self._interviews[session_id]
        engine = interview_data["engine"] if isinstance(interview_data, dict) else interview_data

        events: list[ChatEvent] = [agent_event("Creator-Agent")]
        result = engine.process_message(message)

        # Interview cancelled — return to Mycelos
        if result.cancelled or result.scope_exceeded:
            del self._interviews[session_id]

            handoff_result = HandoffResult(
                source_agent="creator",
                success=False,
                result_summary="Interview cancelled by user.",
                suggested_response=result.response,
            )

            self._app.audit.log("agent.handoff_return", details={
                "from": "creator", "to": "mycelos", "success": False,
            })

            # Mycelos announces return
            events.append(text_event(result.response))
            events.append(agent_event("Mycelos"))
            events.append(done_event())
            return events

        # Interview confirmed — run Creator Pipeline
        if result.confirmed and result.spec:
            del self._interviews[session_id]

            conversation.append({"role": "assistant", "content": result.response})
            self._app.session_store.append_message(
                session_id, role="assistant", content=result.response,
            )
            events.append(text_event(result.response))

            # Dispatch pipeline in background
            import dataclasses as _dc
            spec_dict = _dc.asdict(result.spec)
            task_id = self._app.task_runner.dispatch(
                task_type="creator_pipeline",
                payload=spec_dict,
                session_id=session_id,
                agent_id="creator",
            )

            # Queue Huey task if available
            huey_task = getattr(self, "_pipeline_task", None)
            if huey_task:
                huey_task(task_id, spec_dict)

            self._app.audit.log("agent.handoff_return", details={
                "from": "creator", "to": "mycelos",
                "success": True,
                "background_task_id": task_id,
            })

            events.append(agent_event("Mycelos"))
            events.append(text_event(
                f"Building your agent in the background (Task {task_id[:8]})...\n"
                f"You can keep chatting. I'll notify you when it's done."
            ))
            events.append(done_event())
            return events

        # Interview continues — show next question
        conversation.append({"role": "assistant", "content": result.response})
        self._app.session_store.append_message(
            session_id, role="assistant", content=result.response,
        )
        events.append(text_event(result.response))
        events.append(done_event())
        return events

    def _run_creator_pipeline(self, spec: "AgentSpec") -> tuple[list[ChatEvent], "HandoffResult"]:
        """Run the Creator Pipeline and return events + HandoffResult."""
        from mycelos.agents.creator_pipeline import CreatorPipeline
        from mycelos.agents.handoff import HandoffResult

        events: list[ChatEvent] = []

        events.append(step_progress_event("creator-pipeline", "running"))
        pipeline = CreatorPipeline(self._app)
        result = pipeline.run(spec)
        events.append(step_progress_event("creator-pipeline", "done"))

        if result.success:
            scenarios = len(result.gherkin.split("Scenario:")) - 1
            handoff_result = HandoffResult(
                source_agent="creator",
                success=True,
                result_summary=f"Agent {result.agent_name} created with {scenarios} scenarios.",
                result_data={
                    "agent_name": result.agent_name,
                    "scenarios": scenarios,
                    "cost": result.cost,
                },
                suggested_response=(
                    f"Agent **{result.agent_name}** has been created and registered.\n"
                    f"- {scenarios} acceptance scenarios\n"
                    f"- Tests: passed\n"
                    f"- Audit: passed\n"
                    f"- Status: active"
                ),
            )
            events.append(done_event(cost=result.cost))
        elif result.paused:
            handoff_result = HandoffResult(
                source_agent="creator",
                success=False,
                needs_user_action=True,
                result_summary=f"Pipeline paused: {result.error}",
                suggested_response=f"The agent creation was paused: {result.error}\nShould I continue?",
            )
            events.append(done_event())
        else:
            handoff_result = HandoffResult(
                source_agent="creator",
                success=False,
                error=result.error,
                suggested_response=f"Agent creation failed: {result.error}",
            )
            events.append(error_event(f"Creator pipeline failed: {result.error}"))
            events.append(done_event())

        return events, handoff_result

    def _suggest_agent_name(self, description: str) -> str:
        """Suggest an agent name from the description."""
        import re

        words = re.findall(r'[a-zA-Z]+', description.lower())
        # Filter common stop words
        stops = {
            "ich", "will", "einen", "agent", "der", "die", "das", "ein", "eine",
            "create", "build", "make", "i", "want", "a", "an", "the", "that",
            "for", "about", "with", "from", "and", "oder", "und", "nach",
            "bitte", "kannst", "du", "mir", "mach", "erstell", "brauche",
        }
        meaningful = [w for w in words if w not in stops and len(w) > 2][:3]
        if not meaningful:
            meaningful = ["custom"]
        return "-".join(meaningful) + "-agent"

    def _build_tool_list(self) -> list[dict]:
        """Build the dynamic tool list from the ToolRegistry.

        Returns all tools available to the current agent (mycelos by default),
        with dynamic filtering for connector tools based on what's actually
        running.
        """
        # Get the active agent for this session
        session_id = getattr(self, "_current_session_id", "")
        agent_id = self._get_active_agent(session_id) if session_id else "mycelos"
        agent_type = agent_id if agent_id in ("mycelos", "builder") else "custom"

        # Get all tools for this agent type from registry
        all_tools = ToolRegistry.get_tools_for(agent_type)

        # Dynamic filtering: only include connector_tools/connector_call/github_api
        # if the relevant connectors are actually running
        mcp_mgr = getattr(self._app, "_mcp_manager", None)
        has_connectors = mcp_mgr and mcp_mgr.tool_count > 0
        has_github = False
        try:
            connectors = self._app.connector_registry.list_connectors(status="active")
            has_github = any(c["id"] == "github" for c in connectors)
        except Exception:
            _log.debug("Failed to check active connectors for tool filtering", exc_info=True)

        # Filter out dynamic connector tools if connectors aren't running
        _dynamic_connector_tools = {"connector_tools", "connector_call"}
        tools = []
        for t in all_tools:
            name = t["function"]["name"]
            if name in _dynamic_connector_tools and not has_connectors:
                continue
            if name == "github_api" and not has_github:
                continue
            tools.append(t)

        return tools

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
                pool.submit(self._execute_tool, name, args, user_id=user_id): (tc, name)
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
                    args, session_id or getattr(self, "_current_session_id", ""),
                )

            # Build context for registry execution
            active_agent = agent_id or self._get_active_agent(
                session_id or getattr(self, "_current_session_id", "")
            )
            context = {
                "app": self._app,
                "user_id": getattr(self, "_current_user_id", "default"),
                "session_id": session_id or getattr(self, "_current_session_id", ""),
                "agent_id": active_agent,
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

            # Update active_agents_cache after handoff
            if tool_name == "handoff" and isinstance(result, dict) and result.get("status") == "handoff":
                target = result.get("target_agent", args.get("target_agent"))
                session_id = getattr(self, "_current_session_id", "")
                if target and session_id:
                    if hasattr(self, "_active_agents_cache"):
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

    def _handle_system_command(
        self, session_id: str, message: str, conversation: list[dict]
    ) -> list[ChatEvent]:
        """Handle a system command directly without LLM."""
        from mycelos.chat.context import handle_system_command as _handle_system_command

        response = _handle_system_command(self._app, message)
        conversation.append({"role": "assistant", "content": response})
        self._app.session_store.append_message(session_id, role="assistant", content=response)

        return [
            agent_event("System"),
            system_response_event(response),
            done_event(),
        ]

    def _handle_confirmation(
        self, session_id: str, pending: dict, conversation: list[dict]
    ) -> list[ChatEvent]:
        """Handle plan confirmation → execute workflow or create missing agents."""
        action = pending.get("action", "execute")

        if action == "create_missing_agents":
            return self._handle_create_missing_agents(session_id, pending, conversation)

        from mycelos.cli.chat_cmd import (
            _extract_inputs,
            _resolve_workflow,
        )

        events: list[ChatEvent] = [agent_event("Mycelos")]
        task_id = pending["task_id"]
        plan = pending["plan"]
        workflow_name = pending.get("workflow_name")

        self._app.task_manager.update_status(task_id, "running")

        try:
            workflow_def = _resolve_workflow(self._app, plan, workflow_name)

            if workflow_def is None:
                error_msg = "No matching workflow found."
                self._app.task_manager.set_result(task_id, result=error_msg, status="failed")
                events.append(error_event(error_msg))
            elif not workflow_def.get("plan"):
                error_msg = f"Workflow has no plan. Cannot execute."
                self._app.task_manager.set_result(task_id, result=error_msg, status="failed")
                events.append(error_event(error_msg))
            else:
                inputs = _extract_inputs(plan)

                # Check if workflow should run in background
                run_in_background = workflow_def.get("background", False)

                if run_in_background:
                    from mycelos.scheduler.jobs import execute_background_workflow
                    bg_run_id = execute_background_workflow(
                        self._app, workflow_def["id"], inputs=inputs, user_id="default"
                    )
                    wf_name = workflow_def.get("name", workflow_name or "workflow")
                    events.append(text_event(
                        f"Workflow '{wf_name}' is running in the background (run: {bg_run_id}).\n"
                        f"I'll notify you when it's done. Check progress with `/workflow runs`."
                    ))
                    events.append(done_event())
                else:
                    import uuid
                    from mycelos.workflows.agent import WorkflowAgent

                    run_id = str(uuid.uuid4())[:16]
                    agent = WorkflowAgent(
                        app=self._app,
                        workflow_def=workflow_def,
                        run_id=run_id,
                        session_id=session_id,
                    )

                    # Progress callback — appends step-progress events so
                    # the frontend shows real-time tool activity during workflow
                    def _on_progress(step_id: str, status: str) -> None:
                        events.append(step_progress_event(step_id, status))

                    wf_name = workflow_def.get("name", workflow_name or "workflow")
                    events.append(step_progress_event(wf_name, "running"))
                    exec_result = agent.execute(inputs=inputs, on_progress=_on_progress)

                    if exec_result.status == "completed":
                        result_text = exec_result.result
                        self._app.task_manager.set_result(
                            task_id, result=result_text,
                            cost=exec_result.cost, status="completed",
                        )
                        conversation.append({"role": "assistant", "content": result_text})
                        self._app.session_store.append_message(
                            session_id, role="assistant", content=result_text
                        )
                        events.append(text_event(result_text))
                        events.append(done_event(cost=exec_result.cost))
                    elif exec_result.status == "needs_clarification":
                        question = exec_result.clarification
                        conversation.append({"role": "assistant", "content": question})
                        self._app.session_store.append_message(
                            session_id, role="assistant", content=question
                        )
                        events.append(text_event(
                            f"The workflow needs your input:\n\n**{question}**\n\n"
                            f"Reply with your answer and the workflow will continue."
                        ))
                        events.append(done_event(cost=exec_result.cost))
                    else:
                        error_msg = f"Workflow failed: {exec_result.error}"
                        self._app.task_manager.set_result(
                            task_id, result=error_msg,
                            cost=exec_result.cost, status="failed",
                        )
                        events.append(error_event(error_msg))

        except Exception as exc:
            self._app.task_manager.set_result(task_id, result=str(exc), status="failed")
            events.append(error_event(f"Execution error: {exc}"))

        self._pending.pop(session_id, None)
        return events

    def _handle_create_missing_agents(
        self, session_id: str, pending: dict, conversation: list[dict]
    ) -> list[ChatEvent]:
        """Create missing agents identified by the Planner, then re-plan.

        Runs the CreatorPipeline for each missing agent spec. On success,
        triggers a re-plan with the newly available agents. Events are
        emitted for progress tracking.

        Args:
            session_id: The current chat session identifier.
            pending: The pending state dict with missing_agents list.
            conversation: The conversation message list (mutated in place).

        Returns:
            List of ChatEvents describing creation progress and re-plan.
        """
        from mycelos.agents.agent_spec import AgentSpec
        from mycelos.agents.creator_pipeline import CreatorPipeline

        events: list[ChatEvent] = [agent_event("Mycelos")]
        missing = pending.get("missing_agents", [])

        created: list[str] = []
        for agent_info in missing:
            agent_name = agent_info.get("name", "new-agent")
            events.append(step_progress_event(agent_name, "creating"))

            spec = AgentSpec(
                name=agent_name,
                description=agent_info.get("description", ""),
                capabilities_needed=agent_info.get("capabilities", []),
            )

            pipeline = CreatorPipeline(self._app)
            result = pipeline.run(spec)

            if result.success:
                events.append(step_progress_event(spec.name, "created"))
                created.append(spec.name)
            else:
                events.append(error_event(
                    f"Agent '{spec.name}' konnte nicht erstellt werden: {result.error}"
                ))

        if created:
            events.append(text_event(
                f"{len(created)} Agent(s) erstellt: {', '.join(created)}. "
                "Re-creating the workflow plan..."
            ))

            # Re-plan with the new agents available
            task_id = pending.get("task_id")
            if task_id:
                task = self._app.task_manager.get(task_id)
                if task:
                    new_route = self._app.orchestrator.route(
                        task["goal"], user_id="default"
                    )
                    if new_route.plan:
                        plan_json = json.dumps(
                            new_route.plan, indent=2, ensure_ascii=False
                        )
                        conversation.append({"role": "system", "content":
                            f"Nach der Agent-Erstellung hat der Planner einen neuen Plan:\n"
                            f"{plan_json}\n"
                            f"Erklaere den aktualisierten Plan und frage ob er ausgefuehrt werden soll."
                        })
                        # Update pending with new plan for subsequent confirmation
                        self._pending[session_id] = {
                            "task_id": task_id,
                            "plan": new_route.plan,
                            "workflow_name": new_route.plan.get("workflow_id"),
                        }
        else:
            events.append(error_event("No agents could be created."))

        self._pending.pop(session_id, None) if not created else None
        events.append(done_event())
        return events

    def _get_pending_runs_context(self) -> str:
        """Check for paused/waiting workflow runs and build context string.

        Returns:
            Markdown-formatted context about pending workflows, or empty
            string if none exist or an error occurs.
        """
        try:
            pending = self._app.workflow_run_manager.get_pending_runs()
            if not pending:
                return ""
            lines = ["## Pending Workflows"]
            for run in pending:
                wf_name = run.get("workflow_name", run.get("workflow_id", "?"))
                status = run["status"]
                step = run.get("current_step", "?")
                cost = run.get("cost", 0.0)
                error = run.get("error", "")
                reason = ""
                if "budget" in error:
                    reason = " (budget exceeded)"
                elif "failed" in error:
                    reason = " (step failed)"
                lines.append(
                    f"- **{wf_name}**: {status}{reason} — letzter Step: {step}, Kosten: ${cost:.4f}"
                )
            lines.append(
                "\nMention these pending workflows to the user and ask whether they "
                "want to resume or abort them."
            )
            return "\n".join(lines)
        except Exception:
            return ""

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

    def _handle_run_command(
        self, message: str, session_id: str, events: list[ChatEvent],
    ) -> list[ChatEvent]:
        """Handle /run <workflow> deterministically with progress + session persistence."""
        import uuid
        from mycelos.chat.events import (
            system_response_event, text_event, done_event, step_progress_event,
        )

        parts = message.strip().split(None, 2)  # ["/run", workflow_id, ...]
        if len(parts) < 2:
            # List available workflows
            workflows = self._app.workflow_registry.list_workflows()
            if not workflows:
                resp = "No workflows registered. Create one first."
            else:
                lines = ["**Available workflows:**\n"]
                for wf in workflows:
                    lines.append(f"  `/run {wf['id']}` -- {wf.get('description', wf.get('name', wf['id']))}")
                resp = "\n".join(lines)
            events.append(system_response_event(resp))
            self._app.session_store.append_message(
                session_id, role="assistant", content=resp,
                metadata={"agent": "System"},
            )
            events.append(done_event())
            return events

        workflow_id = parts[1]
        workflow_def = self._app.workflow_registry.get(workflow_id)
        if not workflow_def:
            resp = f"Workflow `{workflow_id}` not found. Use `/run` to see available workflows."
            events.append(system_response_event(resp))
            self._app.session_store.append_message(
                session_id, role="assistant", content=resp,
                metadata={"agent": "System"},
            )
            events.append(done_event())
            return events

        # Parse key=value inputs from remaining args
        from mycelos.chat.slash_commands import _parse_run_inputs
        raw_args = parts[2].split() if len(parts) > 2 else []
        inputs = _parse_run_inputs(raw_args)

        # Execute workflow with progress callback
        from mycelos.workflows.agent import WorkflowAgent

        run_id = str(uuid.uuid4())[:16]
        try:
            agent = WorkflowAgent(
                app=self._app, workflow_def=workflow_def, run_id=run_id,
                session_id=session_id,
            )
        except ValueError as exc:
            resp = f"Cannot run workflow `{workflow_id}`: {exc}"
            events.append(system_response_event(resp))
            self._app.session_store.append_message(
                session_id, role="assistant", content=resp,
                metadata={"agent": "System"},
            )
            events.append(done_event())
            return events

        wf_name = workflow_def.get("name", workflow_id)
        events.append(step_progress_event(wf_name, "running"))

        def _on_progress(step_id: str, status: str) -> None:
            events.append(step_progress_event(step_id, status))

        exec_result = agent.execute(inputs=inputs, on_progress=_on_progress)

        resp_parts = [f"**Workflow:** {wf_name}"]
        if exec_result.status == "completed":
            if exec_result.result:
                resp_parts.append(f"\n{exec_result.result}")
            else:
                resp_parts.append("Completed successfully.")
        elif exec_result.status == "needs_clarification":
            resp_parts.append(f"\n**Question:** {exec_result.clarification}")
        else:
            resp_parts.append(f"**Status:** {exec_result.status}")
            if exec_result.error:
                resp_parts.append(f"**Error:** {exec_result.error}")

        if exec_result.cost and exec_result.cost > 0:
            resp_parts.append(f"\n_Cost: ${exec_result.cost:.4f}_")

        resp = "\n".join(resp_parts)
        events.append(text_event(resp))
        self._app.session_store.append_message(
            session_id, role="assistant", content=resp,
        )
        events.append(done_event(cost=exec_result.cost))
        return events

    def _resume_workflow(self, run: dict, user_answer: str, session_id: str) -> list[ChatEvent]:
        """Resume a paused workflow with the user's answer."""
        from mycelos.workflows.agent import WorkflowAgent

        events: list[ChatEvent] = [agent_event("Mycelos")]
        run_id = run["id"]
        wf_name = run.get("workflow_name", run.get("workflow_id", "workflow"))
        events.append(step_progress_event(wf_name, "running"))

        try:
            agent = WorkflowAgent.from_run(self._app, run_id)
            if agent is None:
                events.append(error_event(f"Could not restore workflow run {run_id[:8]}"))
                events.append(done_event())
                return events

            # Transition run status back to running
            try:
                self._app.workflow_run_manager.resume(run_id)
            except Exception:
                _log.warning("Failed to transition run %s to running", run_id, exc_info=True)

            result = agent.resume(user_answer)

            if result.status == "completed":
                self._app.session_store.append_message(
                    session_id, role="assistant", content=result.result
                )
                events.append(text_event(result.result))
                events.append(done_event(cost=result.cost))
            elif result.status == "needs_clarification":
                events.append(text_event(
                    f"The workflow needs more input:\n\n**{result.clarification}**"
                ))
                events.append(done_event(cost=result.cost))
            else:
                events.append(error_event(f"Workflow failed: {result.error}"))
                events.append(done_event())

        except Exception as exc:
            _log.error("Workflow resume error: %s", exc, exc_info=True)
            events.append(error_event("Failed to resume workflow. Check server logs for details."))
            events.append(done_event())

        return events

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
        session_id = getattr(self, "_current_session_id", None)
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
            from mycelos.chat.events import ChatEvent
            widget_data = {
                "type": "action_confirm",
                "command": f"/{action}",
                "reason": reason,
                "editable": True,
            }
            # Store widget for the event stream to pick up
            if not hasattr(self, "_pending_widgets"):
                self._pending_widgets: dict[str, list] = {}
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
            tools = (
                self._get_session_tools(handler, session_id, user_id=user_id)
                if handler
                else self._build_tool_list()
            )

            response = self._app.llm.complete(conversation, tools=tools)

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
            tools = self._build_tool_list()
            response = self._app.llm.complete(conversation, tools=tools)

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

    # --- Backward-compatible shims for methods moved to mycelos.tools.* ---
    # Tests and other code may call these directly on ChatService.

    def _get_system_status(self) -> dict:
        """Shim: delegates to mycelos.tools.system.execute_system_status."""
        from mycelos.tools.system import execute_system_status
        return execute_system_status({}, {"app": self._app})

    def _filesystem_read(self, path: str) -> dict:
        """Shim: delegates to mycelos.tools.filesystem.execute_filesystem_read."""
        from mycelos.tools.filesystem import execute_filesystem_read
        return execute_filesystem_read({"path": path}, {"app": self._app})

    def _filesystem_write(self, path: str, content: str) -> dict:
        """Shim: delegates to mycelos.tools.filesystem.execute_filesystem_write."""
        from mycelos.tools.filesystem import execute_filesystem_write
        return execute_filesystem_write({"path": path, "content": content}, {"app": self._app})

    def _filesystem_list(self, path: str) -> dict:
        """Shim: delegates to mycelos.tools.filesystem.execute_filesystem_list."""
        from mycelos.tools.filesystem import execute_filesystem_list
        return execute_filesystem_list({"path": path}, {"app": self._app})

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Shim: delegates to mycelos.tools.filesystem._normalize_path."""
        from mycelos.tools.filesystem import _normalize_path
        return _normalize_path(path)

    def _get_follow_up_suggestions(self) -> str:
        """Check what setups the user hasn't done yet and suggest them."""
        suggestions = []

        # Check Telegram — look in channels table (not just credentials)
        try:
            from mycelos.channels.telegram import load_channel_config
            telegram_cfg = load_channel_config(self._app.storage)
            if telegram_cfg and telegram_cfg.get("status") == "active":
                suggestions.append(
                    "Telegram is ACTIVE and working. The user can message the bot from their phone. "
                    "When the user asks for scheduled messages or notifications, you CAN deliver them via Telegram."
                )
            else:
                telegram_cred = self._app.credentials.get_credential("telegram")
                if not telegram_cred or not telegram_cred.get("api_key"):
                    suggestions.append(
                        "The user has NOT set up Telegram yet. "
                        "If it comes up naturally, mention: "
                        "'Du kannst mich auch ueber Telegram nutzen — /connector add telegram'"
                    )
        except Exception:
            _log.debug("Failed to check Telegram setup status", exc_info=True)

        # Check mounts
        try:
            from mycelos.security.mounts import MountRegistry
            mounts = MountRegistry(self._app.storage)
            if not mounts.list_mounts():
                suggestions.append(
                    "The user has NO directories mounted yet. "
                    "If they ask about files, just try to access them — "
                    "the system will automatically prompt for permission."
                )
        except Exception:
            _log.debug("Failed to check mount status", exc_info=True)

        # Check scheduled tasks
        try:
            tasks = self._app.schedule_manager.list_tasks(status="active")
            if not tasks:
                suggestions.append(
                    "The user has NO scheduled tasks. "
                    "If they mention 'daily', 'every morning', or 'recurring', "
                    "suggest scheduling with /schedule add"
                )
        except Exception:
            _log.debug("Failed to check scheduled tasks", exc_info=True)

        if not suggestions:
            return ""

        return "## Setup Suggestions (mention naturally, don't force)\n" + "\n".join(
            f"- {s}" for s in suggestions
        )

    def _try_save_name(self, user_input: str) -> None:
        """Try to extract and save user name from input."""
        words = user_input.strip().split()
        if 1 <= len(words) <= 3 and all(w[0].isupper() for w in words if w):
            name = user_input.strip()
            self._app.memory.set("default", "system", "user.name", name, created_by="chat")
            self._app.audit.log("user.name_set", details={"name": name})
            self._app.audit.log("onboarding.step_1", details={"name": name})


# ---------------------------------------------------------------------------
# System Prompt — the Chat-Agent is "Mycelos", the primary interface
# ---------------------------------------------------------------------------

import re as _re
from typing import Any as _Any


def _sanitize_dict(sanitizer: _Any, d: dict) -> dict:
    """Recursively sanitize all string values in a dict."""
    result = {}
    for k, v in d.items():
        if isinstance(v, str):
            result[k] = sanitizer.sanitize_text(v)
        elif isinstance(v, dict):
            result[k] = _sanitize_dict(sanitizer, v)
        elif isinstance(v, list):
            result[k] = [
                _sanitize_dict(sanitizer, item) if isinstance(item, dict)
                else sanitizer.sanitize_text(item) if isinstance(item, str)
                else item
                for item in v
            ]
        else:
            result[k] = v
    return result

# H-03: Patterns that indicate prompt injection attempts in memory values
_INJECTION_PATTERNS = [
    _re.compile(r"ignore\s+(all\s+)?previous", _re.IGNORECASE),
    _re.compile(r"you\s+must\s+(always|never)", _re.IGNORECASE),
    _re.compile(r"(system|assistant)\s*:\s*", _re.IGNORECASE),
    _re.compile(r"<\s*(system|instruction|prompt)", _re.IGNORECASE),
    _re.compile(r"from\s+now\s+on", _re.IGNORECASE),
    _re.compile(r"override\s+(all|any|previous)", _re.IGNORECASE),
    _re.compile(r"forget\s+(everything|all|previous)", _re.IGNORECASE),
    _re.compile(r"new\s+instructions?\s*:", _re.IGNORECASE),
]

# Allowed categories for memory_write (LLM can only write user-scoped entries)
_ALLOWED_MEMORY_CATEGORIES = {"preference", "decision", "context", "fact"}

# Max length for memory values (prevents bloat + reduces injection surface)
_MAX_MEMORY_VALUE_LENGTH = 500


def _validate_memory_write(category: str, key: str, value: str) -> str | None:
    """Validate a memory_write request from the LLM.

    Returns an error message if invalid, None if OK.

    Security layers:
    1. Category must be in allowlist (forces user.* key prefix)
    2. Key must be alphanumeric + dots/underscores (no path traversal)
    3. Value max length enforced
    4. Value checked against injection patterns
    """
    # Layer 1: Category allowlist
    if category not in _ALLOWED_MEMORY_CATEGORIES:
        return f"Invalid category '{category}'. Allowed: {', '.join(sorted(_ALLOWED_MEMORY_CATEGORIES))}"

    # Layer 2: Key format validation (no path traversal, no dots that escape user.* prefix)
    if not _re.match(r"^[a-zA-Z0-9_][a-zA-Z0-9_.]{0,100}$", key):
        return "Invalid key format. Use alphanumeric characters, dots, and underscores."

    # Layer 3: Value length
    if len(value) > _MAX_MEMORY_VALUE_LENGTH:
        return f"Value too long ({len(value)} chars). Maximum is {_MAX_MEMORY_VALUE_LENGTH}."

    # Layer 4: Injection pattern detection
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(value):
            return "Value contains suspicious content and was blocked."

    return None


def _build_system_info(now: Any, user_tz: str | None = None) -> str:
    """Build system environment info for the prompt.

    If ``user_tz`` (IANA name like ``Europe/Berlin``) is provided, date
    and time are rendered in the user's timezone — not the container's.
    This is critical inside Docker, where the OS TZ is usually UTC but
    the user lives elsewhere, so "tomorrow 9am" resolved against UTC
    would land in the past and fire the reminder immediately.
    """
    import platform
    import os
    from pathlib import Path
    from datetime import datetime as _dt, timezone as _tz

    # Make `now` aware: naive values are assumed OS-local.
    if getattr(now, "tzinfo", None) is None:
        now_aware = now.astimezone()
    else:
        now_aware = now

    tz_name = ""
    local_now = now_aware
    if user_tz:
        try:
            from zoneinfo import ZoneInfo
            local_now = now_aware.astimezone(ZoneInfo(user_tz))
            tz_name = user_tz
        except Exception:
            _log.debug("Unknown user timezone %r, falling back to OS tz", user_tz, exc_info=True)

    if not tz_name:
        # Fall back to OS offset (e.g. "UTC", "UTC+2").
        try:
            off = now_aware.utcoffset()
            hours = int((off.total_seconds() if off else 0) // 3600)
            tz_name = "UTC" if hours == 0 else f"UTC{hours:+d}"
        except Exception:
            tz_name = "UTC"

    parts = ["## System Environment"]
    parts.append(f"Date: {local_now.strftime('%A, %d. %B %Y')}")
    parts.append(f"Time: {local_now.strftime('%H:%M')} ({tz_name})")
    parts.append(f"Timezone: {tz_name}")
    utc_now = _dt.now(_tz.utc)
    parts.append(f"UTC now: {utc_now.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    parts.append(f"OS: {platform.system()} {platform.release()} ({platform.machine()})")
    parts.append(f"Python: {platform.python_version()}")

    # Detect Docker
    if Path("/.dockerenv").exists() or os.environ.get("DOCKER_CONTAINER"):
        parts.append("Environment: Docker Container")
    elif os.environ.get("KUBERNETES_SERVICE_HOST"):
        parts.append("Environment: Kubernetes Pod")
    else:
        parts.append("Environment: Local (bare metal)")

    # Home directory (not sensitive)
    parts.append(f"Home: {Path.home()}")

    # Shell
    shell = os.environ.get("SHELL", "unknown")
    parts.append(f"Shell: {Path(shell).name if shell != 'unknown' else 'unknown'}")

    # Node.js availability (for MCP connectors)
    import shutil
    if shutil.which("npx"):
        parts.append("Node.js: available (MCP connectors supported)")
    else:
        parts.append("Node.js: not found (MCP connectors need it)")

    return "\n".join(parts)


