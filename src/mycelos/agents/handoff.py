"""Agent Handoff Protocol — structured delegation between agents.

Pattern: "Announced Delegation with Summarized Context"

Mycelos (main agent) delegates to specialists via HandoffEnvelope.
Specialists return structured HandoffResult.
User always sees which agent is working via agent_event.
Context is summarized, not passed in full.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class HandoffEnvelope:
    """Context passed from Mycelos to a specialist agent.

    Contains everything the specialist needs to do its job,
    without exposing the full conversation history.
    """

    delegating_agent: str           # "mycelos"
    target_agent: str               # "creator", "planner", "evaluator"
    user_request: str               # the original user message
    context_summary: str            # 2-3 sentence summary of relevant context
    session_id: str                 # for returning to the right conversation
    user_id: str = "default"        # for policy evaluation
    task_inputs: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass
class HandoffResult:
    """What a specialist returns to Mycelos.

    Mycelos uses this to present the result to the user.
    The suggested_response is what Mycelos should tell the user.
    """

    source_agent: str               # "creator", "planner"
    success: bool = False
    result_summary: str = ""        # human-readable summary
    result_data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    needs_user_action: bool = False # does the user need to confirm?
    suggested_response: str = ""    # what Mycelos should tell the user
    error: str = ""                 # error message if not success
    continue_interview: bool = False  # for multi-turn specialists (Creator)
    interview_response: str = ""    # the specialist's message to show


def build_context_summary(
    conversation: list[dict[str, Any]],
    user_request: str,
    llm: Any | None = None,
    model: str | None = None,
) -> str:
    """Build a context summary for a handoff.

    If LLM is available, generates a smart summary.
    Otherwise, extracts the last few user messages.
    """
    if llm:
        # Use cheapest configured model for summarization
        recent = conversation[-6:]  # last 3 exchanges
        conv_text = "\n".join(
            f"{m.get('role', '?')}: {m.get('content', '')[:200]}"
            for m in recent
            if m.get("role") in ("user", "assistant") and m.get("content")
        )

        try:
            response = llm.complete(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Summarize the conversation context in 2-3 sentences. "
                            "Focus on what the user wants and any decisions made. "
                            "Be concise."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Conversation:\n{conv_text}\n\nLatest request: {user_request}",
                    },
                ],
                model=model,
            )
            return response.content
        except Exception:
            pass

    # Fallback: just use the user request
    return f"User request: {user_request}"
