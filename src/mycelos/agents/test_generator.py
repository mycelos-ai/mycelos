"""Test Generator — creates pytest tests from Gherkin scenarios (TDD).

Tests are generated BEFORE code. The agent code must pass these tests.
"""

from __future__ import annotations

from typing import Any

from mycelos.agents.agent_spec import AgentSpec


TEST_GEN_PROMPT = """\
Create pytest tests based on these Gherkin scenarios:

{gherkin_scenarios}

Agent specification:
{spec_context}

## Real Interfaces (use these EXACTLY)

```python
from mycelos.agents.models import AgentInput, AgentOutput

# AgentInput fields:
#   task_goal: str          — what to do (NOT .task!)
#   task_inputs: dict       — structured parameters
#   artifacts: list[str]    — file paths
#   context: dict           — session context
#   config: dict            — agent config

# AgentOutput fields:
#   success: bool
#   result: Any
#   artifacts: list[str]
#   metadata: dict
#   error: str | None
```

## Rules
- Import the agent as: `from agent_code import {agent_class}`
- Import models: `from mycelos.agents.models import AgentInput, AgentOutput`
- Create inputs with: `AgentInput(task_goal="...")`
- One test function per Scenario

## How external operations work
{dependency_instructions}

- Use `tmp_path` or `tempfile` for file operations
- Test behavior, not implementation
- Include helpful assertion messages

Respond ONLY with Python test code. No explanations.
"""


def generate_tests(
    spec: AgentSpec,
    gherkin: str,
    llm: Any,
    model: str | None = None,
) -> str:
    """Generate pytest tests from Gherkin scenarios.

    Args:
        spec: The agent specification.
        gherkin: Confirmed Gherkin scenario text.
        llm: LLM broker instance.
        model: Optional model override.

    Returns:
        Python test code as string.
    """
    agent_class = _spec_to_class_name(spec.name)

    # Adjust instructions based on whether agent has real dependencies
    if spec.dependencies:
        dep_list = ", ".join(spec.dependencies)
        dependency_instructions = (
            f"This agent uses real Python libraries: {dep_list}.\n"
            f"The agent imports and uses these libraries DIRECTLY (not via sdk.run).\n"
            f"In tests, use the REAL libraries — do NOT mock them.\n\n"
            f"IMPORTANT: A `create_sample_pdf` pytest fixture is available in conftest.py.\n"
            f"Use it to create test PDF files:\n"
            f"```python\n"
            f"def test_extract(create_sample_pdf):\n"
            f"    pdf_path = create_sample_pdf(\"Hello World\", pages=1)\n"
            f"    # pdf_path is a string path to a real PDF file with that text\n"
            f"    # For multi-page: create_sample_pdf(\"Content\", pages=3)\n"
            f"```\n"
            f"Do NOT import fpdf2, reportlab, or any other PDF-creation library.\n"
            f"The create_sample_pdf fixture handles PDF creation using pure Python."
        )
    else:
        dependency_instructions = (
            "The agent code uses `mycelos.sdk.run(tool=\"...\", args={{...}})` for ALL external\n"
            "operations (HTTP, browser, file I/O, LLM calls). The agent NEVER imports\n"
            "third-party libraries directly.\n\n"
            "In tests, mock `mycelos.sdk.run` to return expected results:\n"
            "```python\n"
            "from unittest.mock import patch, MagicMock\n\n"
            "@pytest.fixture\n"
            "def mock_sdk_run():\n"
            "    with patch(\"mycelos.sdk.run\") as mock_run:\n"
            "        mock_run.return_value = {{\"status\": \"ok\", \"content\": \"mocked result\"}}\n"
            "        yield mock_run\n"
            "```\n\n"
            "NEVER write `patch(\"agent_code.async_playwright\")` or similar — the agent\n"
            "does not import these libraries."
        )

    prompt = TEST_GEN_PROMPT.format(
        gherkin_scenarios=gherkin,
        spec_context=spec.to_prompt_context(),
        agent_class=agent_class,
        dependency_instructions=dependency_instructions,
    )

    response = llm.complete(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Generate tests for agent '{spec.name}'"},
        ],
        model=model,
    )

    return _clean_code_output(response.content)


def _spec_to_class_name(name: str) -> str:
    """Convert agent name to PascalCase class name.

    'pdf-summarizer' -> 'PdfSummarizer'
    'news_agent' -> 'NewsAgent'
    """
    parts = name.replace("-", "_").split("_")
    return "".join(p.capitalize() for p in parts if p)


def _clean_code_output(content: str) -> str:
    """Strip markdown code fences if present."""
    content = content.strip()
    if content.startswith("```python"):
        content = content[len("```python") :].strip()
    elif content.startswith("```"):
        content = content[3:].strip()
    if content.endswith("```"):
        content = content[:-3].strip()
    return content
