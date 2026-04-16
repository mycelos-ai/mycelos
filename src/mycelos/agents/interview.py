"""InterviewEngine — guided agent creation interview for non-technical users.

State machine that walks users through structured requirements gathering
before handing off to the Creator Pipeline. Inspired by the Claude Code
Superpowers approach: interview first, then TDD.

Phases:
1. GREETING     — Understand the initial idea
2. CLARIFYING   — Ask follow-up questions to build a complete spec
3. SCOPE_CHECK  — Verify feasibility, reject if too complex
4. SUMMARY      — Show plain-language summary for confirmation
5. GHERKIN_REVIEW — Show acceptance scenarios for confirmation
6. CONFIRMED    — Ready to hand off to Creator Pipeline
7. CANCELLED    — User cancelled or scope exceeded
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from mycelos.agents.agent_spec import AgentSpec, classify_effort
from mycelos.i18n import t


class InterviewPhase(Enum):
    """Phases of the agent creation interview."""

    GREETING = "greeting"
    CLARIFYING = "clarifying"
    SCOPE_CHECK = "scope_check"
    SUMMARY = "summary"
    GHERKIN_REVIEW = "gherkin_review"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


@dataclass
class InterviewResult:
    """Result of processing a single interview message."""

    response: str = ""
    phase: InterviewPhase = InterviewPhase.GREETING
    spec: AgentSpec | None = None
    confirmed: bool = False
    cancelled: bool = False
    scope_exceeded: bool = False
    widgets: list[Any] = field(default_factory=list)


# Cancel phrases that abort the interview at any phase
_CANCEL_PHRASES = {
    "abbrechen", "cancel", "stop", "stopp", "nein danke",
    "vergiss es", "lass gut sein", "egal",
}

# Confirmation phrases
_CONFIRM_PHRASES = {
    "ja", "yes", "ok", "okay", "passt", "genau", "klar",
    "ja passt", "ja genau", "ja klar", "ja bitte",
    "ja genau so", "lgtm", "sieht gut aus",
    "looks good", "yes please", "please continue",
    "yes please continue", "go ahead", "continue",
    "that looks good", "perfect", "great",
}

# Rejection phrases (for Gherkin review)
_REJECT_PHRASES = {
    "nein", "no", "nicht ganz", "fehlt", "aendern", "change",
    "anders", "nein das fehlt noch was",
}


def _is_confirm(text: str) -> bool:
    """Check if user message is a confirmation."""
    cleaned = text.strip().lower().rstrip("!., ")
    if cleaned in _CONFIRM_PHRASES:
        return True
    # Check if message STARTS with a confirm word (up to 6 words)
    first = cleaned.split()[0] if cleaned.split() else ""
    if first in {"ja", "yes", "ok", "okay", "klar", "passt", "perfect", "great", "sure", "lgtm"} and len(cleaned.split()) <= 6:
        return True
    # Check if the message CONTAINS a clear confirm phrase
    for phrase in ("looks good", "sieht gut aus", "please continue", "go ahead", "let's go"):
        if phrase in cleaned:
            return True
    return False


def _is_cancel(text: str) -> bool:
    """Check if user message is a cancellation."""
    cleaned = text.strip().lower().rstrip("!., ")
    return cleaned in _CANCEL_PHRASES


def _extract_json(text: str) -> dict | None:
    """Extract JSON from LLM response, handling markdown code blocks and extra text.

    LLMs often return JSON wrapped in ```json blocks or with extra
    explanation text. This extracts just the JSON object.
    """
    content = text.strip()

    # Strip markdown code blocks
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()

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
                    try:
                        return json.loads(content[start:i + 1])
                    except json.JSONDecodeError:
                        return None
    return None


def _is_reject(text: str) -> bool:
    """Check if user message is a rejection."""
    cleaned = text.strip().lower().rstrip("!., ")
    if cleaned in _REJECT_PHRASES:
        return True
    return any(word in cleaned for word in ("nein", "nicht", "fehlt", "aendern", "anders"))


# --- LLM Prompts ---

_GREETING_PROMPT = """\
You are the interview assistant for Mycelos's Creator Agent.
The user wants to create a new agent (a small automation / helper).

Your job: Understand what they want to build.
- Ask in their language (German if they write German, English if English)
- Be friendly, brief, and non-technical
- Summarize what you understood
- Ask ONE focused follow-up question

IMPORTANT: You are building SMALL agents (automations), NOT full applications.
Agents can: search the web, read/write files, call APIs, process data, send messages.
Agents CANNOT: build UIs, run servers, create databases, build frameworks.

Respond with JSON:
{"understood": true/false, "summary": "<what you understood>", "follow_up": "<one question>"}
"""

_CLARIFYING_PROMPT = """\
You are continuing the agent creation interview.
Based on the conversation so far, either:
1. Ask another clarifying question (if important details are missing)
2. Or declare the spec complete (if you have enough info)

Details needed for a complete spec:
- What the agent should DO (core action)
- What TRIGGERS it (on demand, scheduled, event)
- What external services it needs (web search, email, files, APIs)
- What OUTPUT it produces

Keep questions simple and non-technical. ONE question at a time.
Maximum 3 clarifying rounds — then finalize with what you have.

If complete, respond with JSON:
{"complete": true, "spec": {"name": "<kebab-case-name>", "description": "<what it does>", "use_case": "<concrete example>", "capabilities_needed": ["<cap1>", ...], "trigger": "on_demand|scheduled|event", "model_tier": "haiku|sonnet"}}

If not complete, respond with JSON:
{"complete": false, "follow_up": "<one question>"}

Known capabilities: search.web, search.news, http.get, http.post, filesystem.read, filesystem.write, google.gmail.read, google.gmail.send, google.drive.read, google.drive.write, google.calendar.read, sandbox.execute
"""


class InterviewEngine:
    """Guides non-technical users through agent creation requirements.

    Usage:
        engine = InterviewEngine(llm=broker)
        result = engine.process_message("Ich will einen News-Agent")
        # result.response contains the next question/summary
        # result.phase shows current phase
        # When result.confirmed is True, result.spec is the final AgentSpec
    """

    def __init__(self, llm: Any, user_language: str = "de") -> None:
        self._llm = llm
        self._user_language = user_language
        self._phase = InterviewPhase.GREETING
        self._spec: AgentSpec | None = None
        self._history: list[dict[str, str]] = []
        self._clarify_rounds = 0

    @property
    def phase(self) -> InterviewPhase:
        return self._phase

    @property
    def conversation_history(self) -> list[dict[str, str]]:
        return list(self._history)

    def process_message(self, message: str) -> InterviewResult:
        """Process a user message and advance the interview state.

        Args:
            message: The user's message text.

        Returns:
            InterviewResult with response text and current state.
        """
        # Check for cancel at any active phase
        if self._phase not in (InterviewPhase.CONFIRMED, InterviewPhase.CANCELLED):
            if _is_cancel(message):
                self._phase = InterviewPhase.CANCELLED
                return InterviewResult(
                    response=t("interview.cancelled"),
                    phase=self._phase,
                    cancelled=True,
                )

        try:
            if self._phase == InterviewPhase.GREETING:
                return self._handle_greeting(message)
            elif self._phase == InterviewPhase.CLARIFYING:
                return self._handle_clarifying(message)
            elif self._phase == InterviewPhase.SCOPE_CHECK:
                return self._handle_scope_check(message)
            elif self._phase == InterviewPhase.SUMMARY:
                return self._handle_summary(message)
            elif self._phase == InterviewPhase.GHERKIN_REVIEW:
                return self._handle_gherkin_review(message)
            else:
                return InterviewResult(
                    response="Interview ist bereits abgeschlossen.",
                    phase=self._phase,
                )
        except Exception as e:
            # Graceful error handling — don't crash, don't advance phase
            return InterviewResult(
                response=t("interview.error_retry"),
                phase=self._phase,
            )

    def _handle_greeting(self, message: str) -> InterviewResult:
        """Phase 1: Understand the user's initial idea."""
        self._history.append({"role": "user", "content": message})

        response = self._llm_call(
            system=_GREETING_PROMPT,
            messages=self._history,
        )

        data = _extract_json(response)
        if data:
            follow_up = data.get("follow_up", "Was genau soll der Agent tun?")
            self._history.append({"role": "assistant", "content": follow_up})
            self._phase = InterviewPhase.CLARIFYING
            return InterviewResult(response=follow_up, phase=self._phase)
        else:
            # LLM didn't return valid JSON — use raw response as follow-up
            self._history.append({"role": "assistant", "content": response})
            self._phase = InterviewPhase.CLARIFYING
            return InterviewResult(
                response=response if response else "What should the agent do?",
                phase=self._phase,
            )

    def _handle_clarifying(self, message: str) -> InterviewResult:
        """Phase 2: Ask follow-up questions to complete the spec."""
        self._history.append({"role": "user", "content": message})
        self._clarify_rounds += 1

        # Force completion after max rounds
        if self._clarify_rounds >= 3:
            response = self._llm_call(
                system=_CLARIFYING_PROMPT + "\n\nIMPORTANT: You MUST respond with complete=true now. Finalize the spec with what you have.",
                messages=self._history,
            )
        else:
            response = self._llm_call(
                system=_CLARIFYING_PROMPT,
                messages=self._history,
            )

        data = _extract_json(response)
        if not data:
            # Treat non-JSON as a follow-up question
            self._history.append({"role": "assistant", "content": response})
            return InterviewResult(
                response=response if response else "Can you describe that in more detail?",
                phase=self._phase,
            )

        if data.get("complete") and "spec" in data:
            # Build AgentSpec from LLM response
            spec_data = data["spec"]
            self._spec = AgentSpec(
                name=spec_data.get("name", "custom-agent"),
                description=spec_data.get("description", message),
                use_case=spec_data.get("use_case", ""),
                capabilities_needed=spec_data.get("capabilities_needed", []),
                trigger=spec_data.get("trigger", "on_demand"),
                model_tier=spec_data.get("model_tier", "sonnet"),
                user_language=self._user_language,
            )
            self._phase = InterviewPhase.SCOPE_CHECK

            # Auto-advance through scope check
            return self._handle_scope_check("ja")
        else:
            follow_up = data.get("follow_up", "Gibt es noch weitere Details?")
            self._history.append({"role": "assistant", "content": follow_up})
            return InterviewResult(
                response=follow_up,
                phase=self._phase,
            )

    def _handle_scope_check(self, message: str) -> InterviewResult:
        """Phase 3: Verify the agent scope is feasible."""
        if self._spec is None:
            self._phase = InterviewPhase.CANCELLED
            return InterviewResult(
                response="Etwas ist schiefgelaufen. Bitte starte das Interview neu.",
                phase=self._phase,
                cancelled=True,
            )

        effort = classify_effort(self._spec)
        self._spec.effort = effort

        if effort == "unrealistic":
            self._phase = InterviewPhase.CANCELLED
            return InterviewResult(
                response=t("interview.scope_unrealistic"),
                phase=self._phase,
                scope_exceeded=True,
            )

        if effort == "large":
            self._phase = InterviewPhase.CANCELLED
            return InterviewResult(
                response=t("interview.scope_large"),
                phase=self._phase,
                scope_exceeded=True,
            )

        # Scope is OK — show summary
        summary = self._build_summary()
        self._phase = InterviewPhase.SUMMARY
        return InterviewResult(
            response=summary,
            phase=self._phase,
        )

    def _handle_summary(self, message: str) -> InterviewResult:
        """Phase 4: User confirms the summary, then generate Gherkin."""
        if _is_confirm(message):
            # Generate Gherkin scenarios
            return self._generate_and_show_gherkin()
        else:
            # User wants changes — go back to clarifying
            self._phase = InterviewPhase.CLARIFYING
            self._history.append({"role": "user", "content": message})
            return InterviewResult(
                response=t("interview.change_prompt"),
                phase=self._phase,
            )

    def _handle_gherkin_review(self, message: str) -> InterviewResult:
        """Phase 5: User confirms or rejects Gherkin scenarios."""
        if _is_confirm(message):
            self._phase = InterviewPhase.CONFIRMED
            return InterviewResult(
                response=t("interview.confirmed"),
                phase=self._phase,
                confirmed=True,
                spec=self._spec,
            )
        elif _is_reject(message):
            self._phase = InterviewPhase.CLARIFYING
            self._history.append({"role": "user", "content": message})

            response = self._llm_call(
                system=_CLARIFYING_PROMPT,
                messages=self._history,
            )
            try:
                data = json.loads(response)
                follow_up = data.get("follow_up", "Was sollen wir aendern?")
            except (json.JSONDecodeError, ValueError):
                follow_up = response if response else "Was sollen wir aendern?"

            self._history.append({"role": "assistant", "content": follow_up})
            return InterviewResult(
                response=follow_up,
                phase=self._phase,
            )
        else:
            # Ambiguous — ask explicitly
            return InterviewResult(
                response=t("interview.gherkin_ambiguous"),
                phase=self._phase,
            )

    def _build_summary(self) -> str:
        """Build a user-friendly summary of the agent spec."""
        spec = self._spec
        if spec is None:
            return ""

        # Map capabilities to i18n keys
        _CAP_KEYS = {
            "search.web": "interview.cap_search_web",
            "search.news": "interview.cap_search_news",
            "http.get": "interview.cap_http_get",
            "http.post": "interview.cap_http_post",
            "filesystem.read": "interview.cap_filesystem_read",
            "filesystem.write": "interview.cap_filesystem_write",
            "google.gmail.read": "interview.cap_gmail_read",
            "google.gmail.send": "interview.cap_gmail_send",
            "google.drive.read": "interview.cap_gdrive_read",
            "google.drive.write": "interview.cap_gdrive_write",
            "google.calendar.read": "interview.cap_calendar_read",
            "sandbox.execute": "interview.cap_sandbox_execute",
        }

        _TRIGGER_KEYS = {
            "on_demand": "interview.trigger_on_demand",
            "scheduled": "interview.trigger_scheduled",
            "event": "interview.trigger_event",
        }

        caps_text = "\n".join(
            f"  - {t(_CAP_KEYS.get(c, c)) if c in _CAP_KEYS else c}"
            for c in spec.capabilities_needed
        ) if spec.capabilities_needed else f"  - {t('interview.summary_no_services')}"

        trigger_key = _TRIGGER_KEYS.get(spec.trigger)
        trigger_text = t(trigger_key) if trigger_key else spec.trigger

        summary = (
            f"{t('interview.summary_header')}\n\n"
            f"**{t('interview.summary_name')}:** {spec.name}\n"
            f"**{t('interview.summary_description')}:** {spec.description}\n"
            f"**{t('interview.summary_use_case')}:** {spec.use_case or spec.description}\n"
            f"**{t('interview.summary_trigger')}:** {trigger_text}\n"
            f"**{t('interview.summary_services')}:**\n{caps_text}\n\n"
            f"{t('interview.summary_confirm')}"
        )

        return summary

    def _generate_and_show_gherkin(self) -> InterviewResult:
        """Generate Gherkin scenarios and present them to the user."""
        from mycelos.agents.gherkin_generator import generate_gherkin

        try:
            gherkin = generate_gherkin(self._spec, self._llm)
            self._spec.gherkin_scenarios = gherkin
            self._phase = InterviewPhase.GHERKIN_REVIEW

            return InterviewResult(
                response=(
                    f"{t('interview.gherkin_header')}\n\n"
                    f"```gherkin\n{gherkin}\n```\n\n"
                    f"{t('interview.gherkin_confirm')}"
                ),
                phase=self._phase,
            )
        except Exception as e:
            # If Gherkin generation fails, still move forward with empty scenarios
            self._spec.gherkin_scenarios = ""
            self._phase = InterviewPhase.GHERKIN_REVIEW
            return InterviewResult(
                response=t("interview.gherkin_failed"),
                phase=self._phase,
            )

    def _llm_call(self, system: str, messages: list[dict[str, str]]) -> str:
        """Make an LLM call with system prompt and conversation history."""
        llm_messages = [{"role": "system", "content": system}] + messages
        response = self._llm.complete(messages=llm_messages)
        return response.content.strip()
