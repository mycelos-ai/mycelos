"""Per-turn context for the chat agent.

Background-task notices, conversation hydration, auto-titles and the
system messages injected before each LLM call (knowledge context, task
reminders, onboarding). Mixin for :class:`mycelos.chat.service.ChatService`;
split out of ``handle_message`` without behavior changes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mycelos.chat.events import ChatEvent, citations_from_notes, system_response_event

_log = logging.getLogger("mycelos.chat.service")


class ContextInjectionMixin:
    """Context gathering and injection done at the start of each turn."""

    def _notify_background_tasks(self, user_id: str, events: list[ChatEvent]) -> None:
        """Report background tasks that finished since the last message."""
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

    def _load_conversation(self, session_id: str) -> list[dict[str, Any]]:
        """Return the in-memory conversation, hydrating it from the session store."""
        # Ensure conversation exists. The in-memory cache is per-process
        # — if the session was created by another code path (e.g. file
        # upload → session_store.append_message) the cache misses but
        # the persisted history holds messages we MUST replay so the
        # agent sees them. Without this an upload marker just before
        # the first chat turn gets silently dropped.
        if session_id not in self._conversations:
            self._conversations[session_id] = []
            try:
                persisted = self._app.session_store.load_messages(session_id)
                for m in persisted:
                    role = m.get("role", "")
                    content = m.get("content", "")
                    if role in ("user", "assistant") and content:
                        self._conversations[session_id].append(
                            {"role": role, "content": content}
                        )
            except Exception:
                _log.debug(
                    "Failed to hydrate conversation from session_store",
                    exc_info=True,
                )
        conversation = self._conversations[session_id]
        return conversation

    def _auto_title_session(self, session_id: str, message: str) -> None:
        """Title an untitled session after its first user message."""
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

    def _inject_knowledge_context(
        self, message: str, conversation: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Inject relevant knowledge notes; return them as citations."""
        # Knowledge Base context enrichment (hard 5s timeout — must not hang chat)
        # ``citations`` are exactly the notes injected below, sent to the
        # client so the answer can name its real sources. Path and title only.
        citations: list[dict[str, str]] = []
        try:
            from concurrent.futures import ThreadPoolExecutor
            from concurrent.futures import TimeoutError as FuturesTimeout
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
                citations = citations_from_notes(relevant[:3])
        except Exception:
            citations = []
            _log.debug("Failed to retrieve knowledge base context", exc_info=True)
        return citations

    def _inject_task_reminders(
        self, session_id: str, conversation: list[dict[str, Any]],
    ) -> None:
        """Inject timed reminders and due tasks on the 1st and every 10th message."""
        # Task reminders (overdue + due today) — inject into conversation context
        # so the LLM mentions them naturally in its response
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

    def _inject_onboarding(
        self,
        message: str,
        session_id: str,
        conversation: list[dict[str, Any]],
        events: list[ChatEvent],
    ) -> None:
        """Inject the onboarding script for first-time users (or honor a skip)."""
        # First-time user onboarding — only if name is truly unknown
        try:
            onboarding_done = self._app.memory.get("default", "system", "onboarding_completed")
            user_name_known = self._app.memory.get("default", "system", "user.name")
            if not onboarding_done and not user_name_known:
                if session_id not in self._onboarding_skipped:
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
                        self._onboarding_skipped.add(session_id)
                        events.append(system_response_event(
                            "Onboarding skipped. You can set things up anytime — just ask!"
                        ))
        except Exception:
            _log.debug("Failed during onboarding check", exc_info=True)
