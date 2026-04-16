"""Chat Orchestrator — classifies user intent and routes to the right handler.

Intent types:
- CONVERSATION: questions, smalltalk → direct LLM
- TASK_REQUEST: "summarize emails" → PlannerAgent
- CREATE_AGENT: "create an agent that..." → CreatorAgent
- SYSTEM_COMMAND: "show config", "list agents" → direct execution
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Intent(Enum):
    """Possible user intent categories for message routing."""

    CONVERSATION = "conversation"
    TASK_REQUEST = "task_request"
    CREATE_AGENT = "create_agent"
    SYSTEM_COMMAND = "system_command"


@dataclass
class RouteResult:
    """Result of routing a user message."""

    intent: Intent
    response: str | None = None
    task_id: str | None = None
    plan: dict | None = None
    proposal: dict | None = None


_CLASSIFIER_PROMPT = """\
Classify the user's message into exactly one category.
Respond ONLY with valid JSON: {"intent": "<category>", "confidence": 0.0-1.0}

Categories:
- "conversation": questions, greetings, AND simple requests the assistant can do directly
  with its tools (search, list files, read files, check repos, get news). This is the DEFAULT.
- "task_request": user wants a COMPLEX multi-step task that needs planning, scheduling,
  a workflow, or structured information gathering. NOT for simple tool calls.
  Includes: brainstorming, research, analysis, reporting, structured interviews.
- "create_agent": user explicitly wants to BUILD/CREATE a new agent, bot, or automation
- "system_command": user asks about system status, config, agents list, history

IMPORTANT: Most user requests are "conversation" — the assistant has tools for
searching, reading files, checking GitHub, etc. Only classify as "task_request"
if it truly needs a multi-step workflow or scheduling.

Examples:
- "Hello, how are you?" → conversation
- "What's in my Downloads folder?" → conversation (simple filesystem_list)
- "Search for AI news" → conversation (simple search tool)
- "Show me my GitHub repos" → conversation (simple connector call)
- "Check my GitHub PRs" → conversation (simple connector call)
- "Read the file ~/notes.txt" → conversation (simple filesystem_read)
- "Remember that we decided X" → conversation (simple note_write tool)
- "Note: meeting with Alex on Friday" → conversation (simple note_write)
- "What tasks are due today?" → conversation (simple note_list tool)
- "What did we decide about security?" → conversation (simple note_search)
- "Summarize these 50 emails and create tasks" → task_request (complex, multi-step)
- "Send me daily news at 7am" → task_request (needs scheduling)
- "Monitor my repos and alert on issues" → task_request (needs workflow)
- "Collect ideas for my project" → task_request (needs structured workflow)
- "Brainstorm features for X" → task_request (needs structured approach)
- "Research AI trends and summarize" → task_request (multi-step: search + summarize)
- "Give me a daily briefing" → task_request (needs workflow)
- "Analyze my Git repos, who committed most" → task_request (multi-step analysis)
- "Write a report about our progress" → task_request (multi-step: gather + write)
- "Create an agent that reviews PRs" → create_agent
- "Build me a bot for invoices" → create_agent
- "Show me the current config" → system_command
- "What agents are running?" → system_command
"""


# Confirmation patterns — user says "yes" to a pending plan
_CONFIRM_PATTERNS = {
    "ja", "yes", "ok", "okay", "sure", "go", "do it", "mach das",
    "ja bitte", "ja mach", "ausfuehren", "starten", "start",
    "los", "run", "run it", "execute", "ja klar", "klar",
    "genau", "passt", "machen", "lgtm",
}

# Words that indicate hedging/negation — not a simple confirmation
_REJECT_WORDS = {
    "aber", "but", "nicht", "not", "nein", "no", "warte",
    "wait", "aender", "change", "stopp", "stop", "abbrech", "cancel",
}


def is_plan_confirmation(text: str) -> bool:
    """Check if the user's message is a confirmation of a pending plan.

    Matches against short affirmative phrases. Long messages, questions,
    or messages with hedging words are not confirmations.
    """
    cleaned = text.strip().lower().rstrip("!. ")
    if not cleaned or len(cleaned) > 40:
        return False
    # Reject if contains hedging/negation words
    if any(w in cleaned for w in _REJECT_WORDS):
        return False
    if cleaned in _CONFIRM_PATTERNS:
        return True
    # Check if starts with a confirm word (strip trailing punctuation)
    first_word = cleaned.split()[0].rstrip(",.!?;:") if cleaned.split() else ""
    if first_word in {"ja", "yes", "ok", "okay", "sure", "klar"} and len(cleaned.split()) <= 4:
        return True
    return False


class ChatOrchestrator:
    """Classifies user intent and routes to the appropriate handler.

    Uses a cheap LLM call to classify the user's message into one of four
    intent categories. Falls back to CONVERSATION on any error, ensuring
    the system always has a safe default behavior.

    Args:
        llm: An LLM broker instance conforming to the LLMBroker protocol.
        classifier_model: Optional model override for classification calls.
            When set, uses this (typically cheaper) model instead of the
            broker's default.
    """

    def __init__(self, llm: Any, classifier_model: str | None = None) -> None:
        self._llm = llm
        self._classifier_model = classifier_model
        self._task_manager: Any = None
        self._planner: Any = None
        self._app: Any = None

    def classify(self, user_message: str) -> Intent:
        """Classify a user message into an intent category.

        Uses keyword pre-detection for agent creation (fast, no LLM needed),
        then falls back to LLM classification for ambiguous cases.
        """
        try:
            response = self._llm.complete(
                messages=[
                    {"role": "system", "content": _CLASSIFIER_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                model=self._classifier_model,
            )
            # Extract JSON from response — Haiku often adds markdown
            # wrappers and/or extra text after the JSON
            content = response.content.strip()

            # Strip markdown code block wrapper
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(
                    line for line in lines
                    if not line.strip().startswith("```")
                ).strip()

            # Extract just the JSON object (ignore trailing text)
            # Find the first { and its matching }
            start = content.find("{")
            if start >= 0:
                depth = 0
                for i, ch in enumerate(content[start:], start):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            content = content[start:i + 1]
                            break

            data = json.loads(content)
            intent = Intent(data.get("intent", "conversation"))

            import logging
            logging.getLogger("mycelos.orchestrator").debug(
                "Classified: %s (confidence: %s) ← %s",
                intent.value, data.get("confidence", "?"), user_message[:60],
            )
            return intent
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            import logging
            logging.getLogger("mycelos.orchestrator").warning(
                "Classifier failed: %s — raw: %s", e, response.content[:100] if response else "no response",
            )
            return Intent.CONVERSATION

    def set_services(
        self,
        task_manager: Any = None,
        planner: Any = None,
        app: Any = None,
    ) -> None:
        """Inject service dependencies for routing.

        Args:
            task_manager: A TaskManager instance for task CRUD.
            planner: A PlannerAgent instance for plan generation.
            app: The App instance for building planner context.
        """
        if task_manager is not None:
            self._task_manager = task_manager
        if planner is not None:
            self._planner = planner
        if app is not None:
            self._app = app

    def route(self, user_message: str, user_id: str = "default") -> RouteResult:
        """Classify and route a message to the appropriate handler.

        Args:
            user_message: The raw message text from the user.
            user_id: The user identifier for task ownership.

        Returns:
            A RouteResult with the classified intent and any dispatch
            artifacts (task_id, plan, proposal).
        """
        intent = self.classify(user_message)

        if intent == Intent.TASK_REQUEST and self._task_manager and self._planner:
            return self._handle_task_request(user_message, user_id)
        elif intent == Intent.SYSTEM_COMMAND:
            return RouteResult(intent=Intent.SYSTEM_COMMAND)
        else:
            return RouteResult(intent=Intent.CONVERSATION)

    def _handle_task_request(self, message: str, user_id: str) -> RouteResult:
        """Create a task, move it to planning, and generate a plan.

        Args:
            message: The user's task request text.
            user_id: The user identifier for task ownership.

        Returns:
            RouteResult with task_id and plan populated.
        """
        task_id = self._task_manager.create(message, user_id=user_id)
        self._task_manager.update_status(task_id, "planning")

        # Build context from app if available
        context: dict[str, Any] = {}
        if self._app is not None:
            from mycelos.agents.planner_context import build_planner_context

            context = build_planner_context(self._app)

        plan = self._planner.plan(message, context=context)
        return RouteResult(intent=Intent.TASK_REQUEST, task_id=task_id, plan=plan)

