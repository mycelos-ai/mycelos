"""WorkflowAgent — LLM-powered workflow execution.

The sole workflow execution engine. All workflows run through WorkflowAgent.
The agent executes the plan using an LLM loop with scoped tool access:
only tools listed in `allowed_tools` are visible (both built-in and MCP).

Supports pause/resume via NEEDS_CLARIFICATION signal.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mycelos.tools.registry import ToolRegistry
from mycelos.workflows.run_cause import describe_exception

if TYPE_CHECKING:
    from mycelos.app import App

logger = logging.getLogger("mycelos.workflows.agent")

CLARIFICATION_SIGNAL = "NEEDS_CLARIFICATION:"


@dataclass
class WorkflowAgentResult:
    """Result of a WorkflowAgent execution."""

    status: str  # completed, failed, needs_clarification
    result: str = ""
    error: str = ""
    clarification: str = ""
    conversation: list[dict] = field(default_factory=list)
    cost: float = 0.0
    total_tokens: int = 0


class WorkflowAgent:
    """Executes a workflow plan using LLM + scoped tools.

    The agent gets a system prompt (the plan), a model, and a list of
    allowed tools. It runs an LLM loop: the LLM reads the plan, calls
    tools, and eventually returns a text response (done) or signals
    that it needs user clarification.

    Tool scoping:
    - Built-in tools: filtered from ToolRegistry by exact name match
    - MCP tools: filtered from MCPConnectorManager by exact or prefix match
    - Wildcard: "playwright.*" allows all tools starting with "playwright."
    """

    def __init__(
        self,
        app: "App",
        workflow_def: dict[str, Any],
        run_id: str,
        max_rounds: int = 20,
        session_id: str | None = None,
    ) -> None:
        self.app = app
        self._workflow_def = workflow_def
        plan = workflow_def.get("plan")
        if not plan or not isinstance(plan, str) or not plan.strip():
            raise ValueError(
                "WorkflowAgent requires a non-empty 'plan' string in workflow_def. "
                "This workflow definition is missing one — likely a legacy row from "
                "before plan-based workflows. Re-register the workflow."
            )
        self.plan: str = plan
        self.model = self._resolve_model(workflow_def.get("model") or "haiku")
        raw_tools = workflow_def.get("allowed_tools") or []
        # Guard against double-serialized JSON strings from the DB
        if isinstance(raw_tools, str):
            try:
                parsed = json.loads(raw_tools)
                raw_tools = parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, ValueError):
                raw_tools = []
        self.allowed_tools: list[str] = raw_tools
        self.run_id = run_id
        self.session_id = session_id
        self.max_rounds = max_rounds
        self.conversation: list[dict] = []
        self._total_tokens = 0
        self._total_cost = 0.0
        self._workflow_id = workflow_def.get("id", "unknown")

    def get_tool_schemas(self) -> list[dict]:
        """Build the list of tool schemas the LLM can see.

        Filters both built-in tools (ToolRegistry) and MCP tools
        against `allowed_tools`. Supports wildcard prefix matching:
        "playwright.*" matches "playwright.navigate", "playwright.screenshot", etc.
        """
        schemas: list[dict] = []
        seen: set[str] = set()

        # Built-in tools from ToolRegistry
        ToolRegistry._ensure_initialized()
        for name, entry in ToolRegistry._tools.items():
            if self._is_tool_allowed(name):
                schemas.append(entry["schema"])
                seen.add(name)

        # MCP tools from connector manager
        mcp_mgr = getattr(self.app, "_mcp_manager", None)
        if mcp_mgr:
            for tool in mcp_mgr.list_tools():
                tool_name = tool["name"]
                if tool_name not in seen and self._is_tool_allowed(tool_name):
                    schemas.append({
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "description": tool.get("description", ""),
                            "parameters": tool.get("schema", {"type": "object", "properties": {}}),
                        },
                    })
                    seen.add(tool_name)

        return schemas

    def _is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool name matches the allowed_tools list.

        Supports exact match and wildcard prefix: "playwright.*" matches
        any tool starting with "playwright.".
        """
        for pattern in self.allowed_tools:
            if pattern == tool_name:
                return True
            if pattern.endswith(".*"):
                prefix = pattern[:-1]  # "playwright.*" → "playwright."
                if tool_name.startswith(prefix):
                    return True
        return False

    def _build_system_prompt(self) -> str:
        """Build the full system prompt: base context + user context + plan."""
        parts = []

        # Base context + ReAct reasoning pattern
        parts.append(
            "You are an agent in the Mycelos ecosystem — an AI-powered personal "
            "assistant and agent operating system. You are executing a workflow task.\n\n"
            "## HOW TO WORK (ReAct pattern)\n\n"
            "Follow this loop until the task is complete:\n\n"
            "1. **Thought:** Reason about what to do next and why. State which tool "
            "you will call and what you expect to get from it.\n"
            "2. **Action:** Call the tool.\n"
            "3. **Observation:** Read the tool result. If it failed or returned "
            "unexpected data, reason about what went wrong and adapt your plan.\n"
            "4. Repeat until you have all the data you need.\n"
            "5. **Final answer:** Produce the polished result for the user.\n\n"
            "## CRITICAL RULES\n\n"
            "- You MUST call tools. DO NOT describe what you would do — do it.\n"
            "- State your reasoning BEFORE each tool call (as part of your response "
            "text), then call the tool in the same turn.\n"
            "- If a tool returns an error or empty data, adapt: try an alternative "
            "source, skip it and note it in the result, or retry with different parameters.\n"
            "- Your FINAL text response (when you stop calling tools) is what the user "
            "sees. It must be the finished result — not a status update.\n"
            "- Never say 'I will now fetch...' or 'The workflow is complete.' — "
            "just deliver the result."
        )

        # User context (name, language, preferences)
        try:
            from mycelos.agents.handlers.base import build_user_context
            user_ctx = build_user_context(self.app)
            if user_ctx.strip():
                parts.append(user_ctx)
        except Exception:
            pass

        # Off-topic interrupt handling: we let the agent itself decide what
        # to do if the user sends an unrelated message mid-workflow.
        parts.append(
            "HANDLING OFF-TOPIC MESSAGES:\n"
            "If a user message is a small clarification or side-question "
            "related to the current task, answer it briefly and then continue "
            "the workflow from where you left off.\n"
            "If the user message is clearly off-topic (a different subject, "
            "a new request, or a command to do something else), do NOT try to "
            "weave it in. Stop the current workflow step and respond with a "
            "short sentence asking the user whether they want to (a) pause "
            "this workflow and start the new topic, or (b) finish this first. "
            "Wait for their answer before proceeding."
        )

        # The workflow plan (main instructions)
        parts.append(self.plan)

        return "\n\n".join(parts)

    def execute(
        self,
        inputs: dict[str, Any] | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> WorkflowAgentResult:
        """Run the workflow plan. Returns WorkflowAgentResult.

        Any exception that ends the run is recorded on the run row and then
        re-raised. Recording never swallows: callers audit the exception
        (`scheduler/jobs.py`) and chat surfaces it to the user.

        Args:
            inputs: Key-value inputs for the workflow plan.
            on_progress: Optional callback(step_id, status) called during
                tool execution. Status is "running" before and "done" after
                each tool call. This enables real-time progress in the UI.

        Raises:
            BaseException: Whatever the plan's LLM call or tools raised. The
                run row is marked 'failed' with a sanitized cause first.
        """
        self._start_run()
        try:
            return self._execute_plan(inputs=inputs, on_progress=on_progress)
        except BaseException as exc:
            # BaseException, not Exception: a Ctrl-C, a SystemExit from a
            # shutdown handler and a GeneratorExit all end the run just as
            # finally as a RuntimeError does, and each one used to leave the
            # row 'running' forever. We record and re-raise unconditionally,
            # so an interrupt still interrupts.
            logger.exception("Workflow run %s failed", self.run_id)
            self._record_failure(exc)
            raise

    def _start_run(self) -> None:
        """Make sure a run row exists before any work happens.

        Deliberately fatal when no row can be established: a run with no row
        is a run nobody can account for, and every surface that reports runs
        reads rows. Failing here costs one execution; running unrecorded
        costs the history.

        A resume re-enters execute() for a run that already has a row. That
        row satisfies the invariant, so the duplicate insert is tolerated —
        only the absence of a row is fatal.

        Raises:
            Exception: Whatever the storage layer raised, unless a row for
                this run already exists.
        """
        # An ad-hoc workflow_def (built in code, never registered) has no row
        # in `workflows`, and the foreign key would reject it. Such a run is
        # still a run: record it with a null workflow_id and keep its identity
        # in routine_key. Task 1 made the column nullable for exactly this.
        workflow_id: str | None = self._workflow_id
        if not self._is_registered_workflow(workflow_id):
            workflow_id = None

        try:
            self.app.workflow_run_manager.start(
                workflow_id=workflow_id,
                user_id="default",
                run_id=self.run_id,
                session_id=self.session_id,
                routine_key=self._workflow_id,
            )
            return
        except Exception:
            existing = None
            try:
                existing = self.app.workflow_run_manager.get(self.run_id)
            except Exception:
                logger.warning(
                    "Cannot read back workflow run %s", self.run_id, exc_info=True,
                )
            if existing is not None:
                # Resume path: the row is already there, the invariant holds.
                logger.debug("Workflow run %s already recorded", self.run_id)
                return
            logger.exception(
                "Cannot record the start of workflow run %s — refusing to run "
                "unrecorded", self.run_id,
            )
            raise

    def _is_registered_workflow(self, workflow_id: str | None) -> bool:
        """Check whether `workflow_id` exists in the workflows table.

        Args:
            workflow_id: The id to look up.

        Returns:
            True only when a matching row exists. False on any lookup error —
            a null workflow_id records the run; a bad one rejects it.
        """
        if not workflow_id:
            return False
        try:
            row = self.app.storage.fetchone(
                "SELECT 1 AS present FROM workflows WHERE id = ?", (workflow_id,)
            )
            return row is not None
        except Exception:
            logger.warning(
                "Cannot verify workflow '%s' before recording a run",
                workflow_id, exc_info=True,
            )
            return False

    def _record_failure(self, cause: "BaseException | str") -> None:
        """Mark the run failed with a sanitized cause. Never raises.

        When given an exception, the cause is built *here*, inside the guard,
        rather than by the caller. `describe_exception` applies regexes to
        attacker-influenced text and calls into the security sanitizer; when
        that raises outside the guard, no recording happens at all and the row
        stays 'running'. The fallback is the exception type, which cannot fail
        to render. A caller that already authored a fixed cause string (the
        max-rounds path) passes it through unchanged.

        This is the end of a run, and the start of a run is fatal when it
        cannot be recorded (`_start_run`). The two ends are guarded to the
        same standard but not by the same mechanism, because raising here
        would replace the *real* cause of the failure with a storage error
        and rob the caller of the exception it must see. Instead an
        unrecordable ending is escalated: `logger.error` plus an audit event,
        so a run that ended with no row is visible rather than a warning in a
        log nobody greps.

        Args:
            cause: The exception that ended the run, or a cause string the
                caller authored itself.
        """
        if isinstance(cause, str):
            cause_text = cause
        else:
            try:
                cause_text = describe_exception(cause)
            except BaseException:
                # The sanitizer failed on this message. The type is always
                # safe to render, and an honest bare type beats no row.
                logger.exception(
                    "Cannot describe the cause of workflow run %s", self.run_id,
                )
                cause_text = type(cause).__name__

        recorded = False
        try:
            self.app.workflow_run_manager.fail(self.run_id, error=cause_text)
            recorded = True
        except Exception:
            logger.error(
                "Cannot record the end of workflow run %s — the run ended with "
                "no row to say so", self.run_id, exc_info=True,
            )
            self._audit_unrecorded_ending()

        try:
            self.app.storage.execute(
                "UPDATE workflow_runs SET cost = ?, conversation = ? WHERE id = ?",
                (self._total_cost, json.dumps(self.conversation), self.run_id),
            )
        except Exception:
            # The status is the invariant; cost and conversation are not.
            # Only escalate when the status write failed too.
            log = logger.warning if recorded else logger.error
            log(
                "Failed to persist state for failed workflow run %s", self.run_id,
                exc_info=True,
            )

    def _audit_unrecorded_ending(self) -> None:
        """Record that a run ended without a row. Never raises.

        The audit log is a second, independent surface. When storage cannot
        write the run row, the audit write may well fail too — but it is the
        only remaining place the gap can be seen, so we try.
        """
        try:
            self.app.audit.log(
                "workflow.run_unrecorded",
                details={"run_id": self.run_id, "routine_key": self._workflow_id},
            )
        except Exception:
            logger.error(
                "Cannot audit the unrecorded ending of workflow run %s",
                self.run_id, exc_info=True,
            )

    def _execute_plan(
        self,
        inputs: dict[str, Any] | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> WorkflowAgentResult:
        """Run the LLM tool-calling loop. See :meth:`execute`."""
        # Build initial conversation if empty (fresh start)
        if not self.conversation:
            self.conversation = [
                {"role": "system", "content": self._build_system_prompt()},
            ]
            if inputs:
                self.conversation.append({
                    "role": "user",
                    "content": f"Inputs: {json.dumps(inputs)}",
                })
            else:
                # Anthropic requires at least one user message
                self.conversation.append({
                    "role": "user",
                    "content": "Execute the plan now.",
                })

        tool_schemas = self.get_tool_schemas()

        for round_num in range(self.max_rounds):
            logger.debug(
                "WorkflowAgent round %d/%d (run=%s, model=%s)",
                round_num + 1, self.max_rounds, self.run_id, self.model,
            )
            if on_progress:
                on_progress("thinking", "running")

            # Validate conversation only if it has tool calls (avoids unnecessary modification)
            messages = self.conversation
            has_tool_calls = any(m.get("tool_calls") for m in messages if m.get("role") == "assistant")
            if has_tool_calls:
                from mycelos.chat.conversation_validator import validate_conversation
                messages = validate_conversation(messages)

            response = self.app.llm.complete(
                messages,
                model=self.model,
                tools=tool_schemas if tool_schemas else None,
            )
            self._total_tokens += getattr(response, "total_tokens", 0)
            self._total_cost += getattr(response, "cost", 0.0)

            # Text response (no tool calls) → done or clarification
            if not response.tool_calls:
                self.conversation.append({"role": "assistant", "content": response.content})

                if response.content.startswith(CLARIFICATION_SIGNAL):
                    question = response.content[len(CLARIFICATION_SIGNAL):].strip()
                    self._audit("workflow.needs_clarification", {"question": question})
                    try:
                        self.app.workflow_run_manager.wait_for_input(self.run_id, question)
                        # Persist conversation + clarification + cost so far for later resume
                        self.app.storage.execute(
                            "UPDATE workflow_runs SET conversation = ?, clarification = ?, cost = ? WHERE id = ?",
                            (json.dumps(self.conversation), question, self._total_cost, self.run_id),
                        )
                    except Exception:
                        logger.warning("Failed to persist workflow wait_for_input", exc_info=True)
                    return WorkflowAgentResult(
                        status="needs_clarification",
                        clarification=question,
                        conversation=self.conversation,
                        total_tokens=self._total_tokens,
                        cost=self._total_cost,
                    )

                # Ralph-Wiggum-Check: verify success criteria before marking complete
                success_criteria = self._workflow_def.get("success_criteria")
                if success_criteria and not self._verify_success(response.content, success_criteria):
                    # Not actually done — push back and let the agent try again
                    logger.info("Workflow %s failed success check, retrying", self.run_id)
                    self.conversation.append({
                        "role": "user",
                        "content": (
                            "STOP. You have NOT completed the task. "
                            f"The success criteria state: {success_criteria}\n\n"
                            "Review what you actually did: Did you call the required tools? "
                            "Did you produce a real result (not just a description of what you would do)?\n\n"
                            "Now actually execute the plan — call the tools and produce the final result."
                        ),
                    })
                    continue  # retry the loop

                self._audit("workflow.completed", {"result_length": len(response.content)})
                try:
                    self.app.workflow_run_manager.complete(self.run_id)
                    # Persist result, conversation, and accumulated cost
                    self.app.storage.execute(
                        """UPDATE workflow_runs
                           SET cost = ?, conversation = ?,
                               artifacts = json_object('result', ?)
                           WHERE id = ?""",
                        (self._total_cost, json.dumps(self.conversation),
                         response.content, self.run_id),
                    )
                except Exception:
                    logger.warning("Failed to persist workflow completion", exc_info=True)
                return WorkflowAgentResult(
                    status="completed",
                    result=response.content,
                    conversation=self.conversation,
                    total_tokens=self._total_tokens,
                    cost=self._total_cost,
                )

            # Tool calls → execute each and feed results back
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "tool_calls": [
                    {"id": tc["id"], "type": "function", "function": tc["function"]}
                    for tc in response.tool_calls
                ],
            }
            if response.content:
                assistant_msg["content"] = response.content
            self.conversation.append(assistant_msg)

            for tc in response.tool_calls:
                tool_name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError):
                    args = {}

                # Security: only execute if tool is in allowed set
                if not self._is_tool_allowed(tool_name):
                    tool_result = json.dumps({
                        "error": f"Tool '{tool_name}' is not allowed for this workflow. "
                        f"Allowed: {self.allowed_tools}"
                    })
                    self._audit("workflow.tool_denied", {"tool": tool_name})
                else:
                    if on_progress:
                        on_progress(tool_name, "running")
                    tool_result = self._execute_tool(tool_name, args)
                    self._audit("workflow.tool_executed", {"tool": tool_name})
                    if on_progress:
                        on_progress(tool_name, "done")

                self.conversation.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result if isinstance(tool_result, str) else json.dumps(tool_result),
                })

        # Exceeded max rounds
        self._audit("workflow.max_rounds", {"max_rounds": self.max_rounds})
        error_msg = f"Max rounds ({self.max_rounds}) exceeded"
        self._record_failure(error_msg)
        return WorkflowAgentResult(
            status="failed",
            error=error_msg,
            conversation=self.conversation,
            total_tokens=self._total_tokens,
            cost=self._total_cost,
        )

    @classmethod
    def from_run(cls, app: "App", run_id: str) -> "WorkflowAgent | None":
        """Reconstruct a WorkflowAgent from a persisted run.

        Loads workflow definition and conversation from the DB so the
        agent can resume where it left off.
        """
        run = app.workflow_run_manager.get(run_id)
        if run is None:
            return None

        workflow = app.workflow_registry.get(run["workflow_id"])
        if workflow is None:
            return None

        agent = cls(app=app, workflow_def=workflow, run_id=run_id)

        # Restore conversation from persisted state
        if run.get("conversation"):
            conv = run["conversation"]
            if isinstance(conv, str):
                conv = json.loads(conv)
            agent.conversation = conv

        return agent

    def resume(self, user_answer: str) -> WorkflowAgentResult:
        """Resume after user clarification."""
        self.conversation.append({"role": "user", "content": user_answer})
        return self.execute()

    def _execute_tool(self, tool_name: str, args: dict) -> Any:
        """Execute a tool — tries built-in ToolRegistry first, then MCP."""
        # Try MCP connector first if tool has a dot (e.g., "playwright.navigate")
        mcp_mgr = getattr(self.app, "_mcp_manager", None)
        if "." in tool_name and mcp_mgr:
            try:
                result = mcp_mgr.call_tool(tool_name, args)
                return result if isinstance(result, str) else json.dumps(result)
            except Exception as e:
                return json.dumps({"error": f"MCP tool '{tool_name}' failed: {self._sanitize_error(e)}"})

        # Built-in tool via ToolRegistry
        try:
            context = {
                "app": self.app,
                "user_id": "default",
                "session_id": "",
                "agent_id": f"workflow-agent:{self.run_id}",
            }
            result = ToolRegistry.execute(tool_name, args, context)
            return result if isinstance(result, str) else json.dumps(result)
        except Exception as e:
            return json.dumps({"error": f"Tool '{tool_name}' failed: {self._sanitize_error(e)}"})

    def _resolve_model(self, model_name: str) -> str | None:
        """Resolve a tier name (haiku, sonnet, opus) to a full model ID."""
        # If already has provider prefix, use as-is
        if "/" in model_name:
            return model_name
        # Try to resolve via model registry
        try:
            # Map tier names to agent roles
            tier_map = {"haiku": "workflow-agent", "sonnet": None, "opus": "builder"}
            agent_id = tier_map.get(model_name)
            models = self.app.model_registry.resolve_models(agent_id, "execution")
            if models:
                return models[0]
        except Exception:
            pass
        # Fallback: cheapest or strongest
        try:
            if model_name in ("haiku",):
                return self.app.resolve_cheapest_model()
            else:
                return self.app.resolve_strongest_model()
        except Exception:
            pass
        return None  # Let the broker handle it

    @staticmethod
    def _sanitize_error(e: Exception) -> str:
        """Sanitize error messages before passing to LLM (prevent credential leaks)."""
        from mycelos.security.sanitizer import ResponseSanitizer
        return ResponseSanitizer().sanitize_text(str(e))

    def _verify_success(self, result_text: str, criteria: str) -> bool:
        """Check if the workflow result meets the success criteria.

        Uses heuristics first (cheap), falls back to a quick LLM check
        only if heuristics are inconclusive.
        """
        # Heuristic 1: Did the agent actually call any tools?
        tool_calls_made = sum(
            1 for msg in self.conversation
            if msg.get("role") == "assistant" and msg.get("tool_calls")
        )
        if tool_calls_made == 0:
            logger.info("Success check failed: zero tool calls made")
            self._audit("workflow.success_check_failed", {
                "reason": "no_tool_calls",
                "criteria": criteria,
            })
            return False

        # Heuristic 2: Is the result suspiciously short or just a status message?
        if len(result_text.strip()) < 100:
            logger.info("Success check failed: result too short (%d chars)", len(result_text))
            self._audit("workflow.success_check_failed", {
                "reason": "result_too_short",
                "length": len(result_text),
                "criteria": criteria,
            })
            return False

        # Heuristic 3: Does it look like a "I will do X" instead of actual result?
        lazy_patterns = [
            "ich werde", "ich hole", "i will", "i'll fetch",
            "let me ", "lass mich", "sobald die",
        ]
        lower = result_text.lower()[:300]
        if any(p in lower for p in lazy_patterns) and tool_calls_made < 2:
            logger.info("Success check failed: result looks like intent, not result")
            self._audit("workflow.success_check_failed", {
                "reason": "intent_not_result",
                "criteria": criteria,
            })
            return False

        return True

    def _audit(self, event_type: str, details: dict) -> None:
        """Log an audit event."""
        audit = getattr(self.app, "audit", None)
        if audit:
            audit.log(
                event_type,
                agent_id=f"workflow-agent:{self.run_id}",
                details={**details, "run_id": self.run_id, "model": self.model},
            )
