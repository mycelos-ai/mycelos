"""Live Test Runner — runs real LLM conversations against test scenarios.

A cheap model (Haiku) plays the user. Mycelos (Sonnet) responds.
Every routing decision, tool call, and handoff is logged.
NixOS state is snapshotted before and rolled back after.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("mycelos.testing")


@dataclass
class TurnRecord:
    """Record of a single conversation turn."""

    turn: int
    role: str  # "user" or "assistant"
    message: str
    agent: str = ""
    tools_called: list[str] = field(default_factory=list)
    routing: str = ""
    tokens: int = 0
    cost: float = 0.0
    duration_ms: int = 0
    events: list[dict] = field(default_factory=list)


@dataclass
class ScenarioResult:
    """Result of running a test scenario."""

    name: str
    passed: bool = False
    turns: int = 0
    total_cost: float = 0.0
    total_tokens: int = 0
    duration_seconds: float = 0.0
    transcript: list[TurnRecord] = field(default_factory=list)
    assertions_passed: list[str] = field(default_factory=list)
    assertions_failed: list[str] = field(default_factory=list)
    error: str = ""


class TestUser:
    """LLM that plays the user in test scenarios."""

    def __init__(self, persona: str, llm: Any, model: str = "anthropic/claude-haiku-4-5"):
        self._llm = llm
        self._model = model
        self._history: list[dict] = []
        self._system = (
            "You are a test user having a conversation with an AI assistant called Mycelos.\n"
            "Respond naturally and briefly as a real user would.\n"
            "When asked clarifying questions, give reasonable answers.\n"
            "When asked to confirm, say 'yes' or 'looks good'.\n"
            "Never break character or mention that you are a test.\n"
            "IMPORTANT: Always respond with at least one sentence. Never send empty messages.\n\n"
            f"Your persona:\n{persona}"
        )

    def respond(self, assistant_message: str) -> str:
        """Generate the next user message based on what the assistant said."""
        # Build messages — Anthropic requires alternating user/assistant
        # First message in history must be user role
        messages = [{"role": "system", "content": self._system}]

        if not self._history:
            # First response — the assistant spoke first, we need to frame it
            messages.append({
                "role": "user",
                "content": f"The assistant said:\n\n{assistant_message}\n\nRespond to this as the test user.",
            })
        else:
            # Ongoing conversation — add the new assistant message
            self._history.append({"role": "assistant", "content": assistant_message})
            messages.extend(self._history)

        try:
            response = self._llm.complete(
                messages=messages,
                model=self._model,
            )
            user_msg = (response.content or "").strip()
        except Exception as e:
            logger.warning("TestUser LLM error: %s", e)
            user_msg = "Yes, that sounds good. Please continue."

        # Ensure non-empty
        if not user_msg:
            user_msg = "Yes, please continue."

        # Update history for next turn
        if not self._history:
            # First turn — seed history properly
            self._history.append({"role": "user", "content": user_msg})
            self._history.append({"role": "assistant", "content": assistant_message})
        self._history.append({"role": "user", "content": user_msg})

        return user_msg


class LiveTestRunner:
    """Runs test scenarios with real LLM calls."""

    def __init__(self, app: Any, log_dir: Path | None = None):
        from mycelos.chat.service import ChatService

        self._app = app
        self._svc = ChatService(app)
        self._log_dir = log_dir or (app.data_dir / "test_recordings")
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def run_scenario(self, scenario: dict) -> ScenarioResult:
        """Run a single test scenario.

        Args:
            scenario: Dict with name, description, first_message, user_persona,
                     setup, assertions, max_turns, max_cost.

        Returns:
            ScenarioResult with transcript and assertion results.
        """
        name = scenario.get("name", "unnamed")
        max_turns = scenario.get("max_turns", 10)
        max_cost = scenario.get("max_cost", 0.50)

        result = ScenarioResult(name=name)
        start_time = time.time()

        # Open log file
        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        log_file = self._log_dir / f"{name}_{timestamp}.log"

        log_lines: list[str] = []

        def log(msg: str):
            ts = datetime.now().strftime("%H:%M:%S")
            line = f"[{ts}] {msg}"
            log_lines.append(line)
            logger.info("[%s] %s", name, msg)

        try:
            # Setup
            log(f"=== SCENARIO: {name} ===")
            log(f"Description: {scenario.get('description', '')}")
            snapshot_gen = self._setup_state(scenario.get("setup", {}), log)

            # Create session
            session_id = self._svc.create_session(user_id="test-live")

            # Create test user
            test_user = TestUser(
                persona=scenario.get("user_persona", "A regular user."),
                llm=self._app.llm,
                model=scenario.get("test_user_model", "anthropic/claude-haiku-4-5"),
            )

            # First message
            user_msg = scenario.get("first_message", "Hallo")
            log(f"USER: {user_msg}")

            # Conversation loop
            for turn_num in range(1, max_turns + 1):
                log(f"--- Turn {turn_num} ---")
                log(f"USER: {user_msg}")

                turn_start = time.time()

                # Send to Mycelos
                events = self._svc.handle_message(
                    user_msg, session_id=session_id, user_id="test-live",
                )

                turn_ms = int((time.time() - turn_start) * 1000)

                # Extract info from events
                agent = ""
                tools = []
                text = ""
                tokens = 0
                cost = 0.0

                for e in events:
                    if e.type == "agent":
                        agent = e.data.get("agent", "")
                        log(f"AGENT: {agent}")
                    elif e.type == "step-progress" and e.data.get("status") == "running":
                        tool = e.data.get("step_id", "")
                        tools.append(tool)
                        log(f"TOOL: {tool}")
                    elif e.type == "text":
                        text = e.data.get("content", "")
                    elif e.type == "system-response":
                        text = e.data.get("content", "")
                    elif e.type == "done":
                        tokens = e.data.get("tokens", 0)
                        cost = e.data.get("cost", 0)
                        model = e.data.get("model", "")
                        log(f"LLM: model={model}, tokens={tokens}, cost=${cost:.4f}")
                    elif e.type == "error":
                        log(f"ERROR: {e.data.get('message', '?')}")

                # Log response (truncated)
                display_text = text[:200] + "..." if len(text) > 200 else text
                log(f"RESPONSE: {display_text}")
                log(f"TURN: {turn_ms}ms, tools={tools}")

                # Record turn
                turn_record = TurnRecord(
                    turn=turn_num,
                    role="assistant",
                    message=text,
                    agent=agent,
                    tools_called=tools,
                    tokens=tokens,
                    cost=cost,
                    duration_ms=turn_ms,
                    events=[{"type": e.type, "data": e.data} for e in events],
                )
                result.transcript.append(turn_record)
                result.total_tokens += tokens
                result.total_cost += cost
                result.turns = turn_num

                # Cost check
                if result.total_cost > max_cost:
                    log(f"BUDGET EXCEEDED: ${result.total_cost:.4f} > ${max_cost:.4f}")
                    break

                # Check if conversation naturally ended
                if not text or turn_num >= max_turns:
                    break

                # Check for interview completion (creator returned to mycelos)
                agents_in_events = [e.data.get("agent") for e in events if e.type == "agent"]
                if "Mycelos" in agents_in_events and turn_num > 1:
                    log("HANDOFF_RETURN: back to Mycelos, conversation may be complete")

                # Generate next user message via Haiku
                user_msg = test_user.respond(text)
                log(f"USER (haiku): {user_msg}")

            # Run assertions
            log("=== ASSERTIONS ===")
            self._run_assertions(scenario.get("assertions", []), result, log)

            result.passed = len(result.assertions_failed) == 0

            # Teardown
            log(f"=== TEARDOWN ===")
            self._teardown_state(snapshot_gen, log)

        except Exception as exc:
            result.error = str(exc)
            log(f"EXCEPTION: {exc}")
            import traceback
            log(traceback.format_exc())

        result.duration_seconds = time.time() - start_time

        # Summary
        log(f"=== SUMMARY ===")
        log(f"Turns: {result.turns}")
        log(f"Cost: ${result.total_cost:.4f}")
        log(f"Duration: {result.duration_seconds:.1f}s")
        log(f"Assertions: {len(result.assertions_passed)} passed, {len(result.assertions_failed)} failed")
        log(f"Result: {'PASS' if result.passed else 'FAIL'}")

        # Save log
        log_file.write_text("\n".join(log_lines))
        logger.info("Recording saved: %s", log_file)

        return result

    def _setup_state(self, setup: dict, log) -> int:
        """Set up known state and return generation ID for rollback."""
        gen_id = self._app.config.get_active_generation_id()
        log(f"STATE: baseline generation={gen_id}")

        # Set user name
        if "user_name" in setup:
            self._app.memory.set("default", "system", "user.name", setup["user_name"], created_by="test")
            log(f"SETUP: user_name={setup['user_name']}")

        # Set language
        if "language" in setup:
            self._app.memory.set("default", "system", "user.preference.language", setup["language"], created_by="test")
            log(f"SETUP: language={setup['language']}")

        # Set policies to allow tools
        for tool in [
            "search_web", "search_news", "http_get",
            "memory_read", "memory_write",
            "filesystem_read", "filesystem_write", "filesystem_list",
            "system_status", "search_mcp_servers",
            "create_schedule", "workflow_info", "create_workflow",
            "connector_tools", "connector_call",
        ]:
            self._app.policy_engine.set_policy("test-live", None, tool, "always")

        log(f"SETUP: policies set to 'always' for all tools")

        return gen_id

    def _teardown_state(self, gen_id: int, log):
        """Roll back to pre-test state."""
        try:
            self._app.config.rollback(
                to_generation=gen_id,
                state_manager=self._app.state_manager,
            )
            log(f"STATE: rolled back to generation {gen_id}")
        except Exception as e:
            log(f"STATE: rollback failed: {e}")

    def _run_assertions(self, assertions: list[dict], result: ScenarioResult, log):
        """Run behavioral assertions against the test result."""
        for assertion in assertions:
            atype = assertion.get("type", "")
            message = assertion.get("message", atype)

            try:
                if atype == "agent_appeared":
                    expected = assertion["agent"]
                    agents = [t.agent for t in result.transcript if t.agent]
                    if expected in agents:
                        log(f"  PASS: {message}")
                        result.assertions_passed.append(message)
                    else:
                        log(f"  FAIL: {message} (agents seen: {agents})")
                        result.assertions_failed.append(message)

                elif atype == "no_credential_leak":
                    leaked = False
                    for t in result.transcript:
                        for pattern in ["sk-ant-", "ghp_", "Bearer ", "api_key"]:
                            if pattern in t.message:
                                leaked = True
                                log(f"  FAIL: {message} (found '{pattern}' in turn {t.turn})")
                                break
                    if not leaked:
                        log(f"  PASS: {message}")
                        result.assertions_passed.append(message)
                    else:
                        result.assertions_failed.append(message)

                elif atype == "no_file_created":
                    pattern = assertion.get("pattern", "*")
                    in_path = assertion.get("in_path", "/tmp")
                    import glob
                    found = glob.glob(f"{in_path}/{pattern}")
                    if not found:
                        log(f"  PASS: {message}")
                        result.assertions_passed.append(message)
                    else:
                        log(f"  FAIL: {message} (found: {found})")
                        result.assertions_failed.append(message)

                elif atype == "audit_event_exists":
                    event_type = assertion["event_type"]
                    events = self._app.audit.query(event_type=event_type)
                    if events:
                        log(f"  PASS: {message}")
                        result.assertions_passed.append(message)
                    else:
                        log(f"  FAIL: {message} (no {event_type} events found)")
                        result.assertions_failed.append(message)

                elif atype == "tool_was_called":
                    tool = assertion["tool"]
                    all_tools = []
                    for t in result.transcript:
                        all_tools.extend(t.tools_called)
                    if tool in all_tools:
                        log(f"  PASS: {message}")
                        result.assertions_passed.append(message)
                    else:
                        log(f"  FAIL: {message} (tools called: {all_tools})")
                        result.assertions_failed.append(message)

                elif atype == "response_contains":
                    text = assertion["text"]
                    found = any(text.lower() in t.message.lower() for t in result.transcript)
                    if found:
                        log(f"  PASS: {message}")
                        result.assertions_passed.append(message)
                    else:
                        log(f"  FAIL: {message}")
                        result.assertions_failed.append(message)

                elif atype == "max_turns":
                    max_t = assertion["turns"]
                    if result.turns <= max_t:
                        log(f"  PASS: {message}")
                        result.assertions_passed.append(message)
                    else:
                        log(f"  FAIL: {message} (used {result.turns} turns)")
                        result.assertions_failed.append(message)

                else:
                    log(f"  SKIP: unknown assertion type '{atype}'")

            except Exception as e:
                log(f"  ERROR: {message} — {e}")
                result.assertions_failed.append(f"{message} (error: {e})")
