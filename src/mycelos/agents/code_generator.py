"""Code Generator — creates agent code that must pass the generated tests.

This is the TDD 'green' phase: write code to make tests pass.
If tests fail, the generator is called again with the error output.
"""

from __future__ import annotations

from typing import Any

from mycelos.agents.agent_spec import AgentSpec
from mycelos.agents.test_generator import _spec_to_class_name, _clean_code_output


# The reference agent is included in every code generation prompt so the LLM
# sees a real, working example of the exact patterns to follow.
_REFERENCE_AGENT = '''\
"""Reference Agent — this is a complete, working Mycelos agent.

Every agent you create MUST follow this exact pattern.
"""
import logging
from mycelos.sdk import run, progress
from mycelos.agents.models import AgentInput, AgentOutput

logger = logging.getLogger(__name__)


class GreetingAgent:
    agent_id = "greeting-agent"
    agent_type = "deterministic"
    capabilities_required = []

    def execute(self, input: AgentInput) -> AgentOutput:
        """Generate a greeting. Input: task_goal = person's name."""
        try:
            progress("Generating greeting...")
            name = input.task_goal or "World"
            logger.info("Greeting %s", name)

            greeting = f"Hello, {name}! Welcome to Mycelos."

            progress("Done.")
            return AgentOutput(
                success=True,
                result=greeting,
            )
        except Exception as e:
            logger.error("Greeting failed: %s", e)
            return AgentOutput(success=False, result=None, error=str(e))
'''

# The real interfaces, extracted from the actual source code
_INTERFACES = """\
## Real Interfaces (from mycelos.agents.models)

```python
@dataclass(frozen=True)
class AgentInput:
    task_goal: str                              # What the agent should do
    task_inputs: dict[str, Any] = field(...)    # Structured parameters
    artifacts: list[str] = field(...)           # File paths or references
    context: dict[str, Any] = field(...)        # Session context
    config: dict[str, Any] = field(...)         # Agent-specific config

@dataclass(frozen=True)
class AgentOutput:
    success: bool                               # Did it work?
    result: Any                                 # The main output
    artifacts: list[str] = field(...)           # Generated file paths
    metadata: dict[str, Any] = field(...)       # Extra info
    error: str | None = None                    # Error message if failed
```

## SDK (from mycelos.sdk)

```python
run(tool: str, args: dict) -> Any     # Call external tools through Security Layer
progress(text: str) -> None           # Send progress update to user
```
"""


CODE_GEN_PROMPT = """\
Write Python agent code that passes these tests:

{test_code}

Agent specification:
{spec_context}

## Reference Agent (follow this exact pattern)

{reference_agent}

{interfaces}

## Available Connectors and MCP Tools
{available_connectors}

## Rules
- Follow the Reference Agent pattern exactly
- Import from mycelos.sdk and mycelos.agents.models — these are REAL modules
- AgentInput.task_goal is the main input (NOT .task)
- Return AgentOutput with the fields shown above
- Use run(tool="...", args={{...}}) for ALL external calls (filesystem, LLM, HTTP)
- Use progress() for status updates the user can see
- Add logging: import logging; logger = logging.getLogger(__name__)
- Wrap execute() in try/except, return AgentOutput(success=False, error=...) on failure
- You CAN use any Python stdlib module (json, csv, pathlib, random, etc.)
{dependency_rules}
- The code MUST make the tests pass

Respond ONLY with Python code. No explanations.
"""


CODE_RETRY_PROMPT = """\
The previous code FAILED the tests. Here is the error:

{test_error}

Previous code:
{previous_code}

{interfaces}

Fix the code to make the tests pass. Common issues:
- AgentInput uses .task_goal (not .task)
- AgentOutput fields: success, result, artifacts=[], metadata={{}}, error=None
- Import from agent_code in tests means your class must be importable
- Check that your class name matches what the tests import
- DO NOT import third-party libraries (playwright, pdfplumber, requests, etc.)
  Use run(tool="...") from mycelos.sdk for ALL external operations
- If tests try to patch("agent_code.some_lib"), your code must NOT import that lib

Respond ONLY with the corrected Python code.
"""


def generate_code(
    spec: AgentSpec,
    tests: str,
    llm: Any,
    model: str | None = None,
    previous_code: str | None = None,
    test_error: str | None = None,
    available_connectors: str = "No connectors configured.",
) -> str:
    """Generate agent code that should pass the given tests.

    Args:
        spec: The agent specification.
        tests: The pytest test code to satisfy.
        llm: LLM broker instance.
        model: Optional model override.
        previous_code: Code from a failed attempt (for retry).
        test_error: Error output from the failed test run (for retry).

    Returns:
        Python agent code as string.
    """
    agent_class = _spec_to_class_name(spec.name)
    caps_str = ", ".join(f'"{c}"' for c in spec.capabilities_needed)

    # Dependency-aware rules for code generation
    if spec.dependencies:
        dep_list = ", ".join(spec.dependencies)
        dependency_rules = (
            f"- You MAY import these libraries directly: {dep_list}\n"
            f"  These are installed in the environment.\n"
            f"- Use run(tool=\"...\") for operations NOT covered by these libraries"
        )
    else:
        dependency_rules = (
            "- **DO NOT import third-party libraries directly** (no pdfplumber, requests, etc.)\n"
            "  Instead, use run(tool=\"...\") for external operations:\n"
            "  - PDF text extraction: `run(tool=\"filesystem.read\", args={{\"path\": path, \"mode\": \"text\"}})`\n"
            "  - LLM calls: `run(tool=\"llm.structured_output\", args={{\"prompt\": ..., \"schema\": ...}})`\n"
            "  - HTTP: `run(tool=\"http.get\", args={{\"url\": ...}})`\n"
            "  The agent runs in an isolated subprocess — only mycelos.sdk and stdlib are available"
        )

    if previous_code and test_error:
        prompt = CODE_RETRY_PROMPT.format(
            test_error=test_error[:2000],
            previous_code=previous_code,
            interfaces=_INTERFACES,
        )
    else:
        prompt = CODE_GEN_PROMPT.format(
            test_code=tests,
            spec_context=spec.to_prompt_context(),
            reference_agent=_REFERENCE_AGENT,
            interfaces=_INTERFACES,
            available_connectors=available_connectors,
            dependency_rules=dependency_rules,
        )

    response = llm.complete(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Generate code for agent '{spec.name}'"},
        ],
        model=model,
    )

    return _clean_code_output(response.content)
