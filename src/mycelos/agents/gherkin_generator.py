"""Gherkin Generator — creates user-facing acceptance scenarios from AgentSpec.

Gherkin is the shared language between non-technical users and the system.
Users confirm scenarios (not code), and tests are derived from them.
"""

from __future__ import annotations

import re
from typing import Any

from mycelos.agents.agent_spec import AgentSpec


GHERKIN_PROMPT = """\
Create Gherkin acceptance scenarios for this agent:

{spec_context}

Rules:
- Write in {language} (use Given/When/Then keywords in English, descriptions in {language})
- Maximum 5-7 scenarios
- Each scenario describes ONE concrete case
- Avoid technical details — a non-technical user must understand it
- Include at least one error/edge case scenario
- Use the format: Feature / Scenario / Given-When-Then

Respond ONLY with the Gherkin text, no other output.
"""


def generate_gherkin(
    spec: AgentSpec,
    llm: Any,
    model: str | None = None,
) -> str:
    """Generate Gherkin scenarios from an AgentSpec via LLM.

    Args:
        spec: The agent specification.
        llm: LLM broker instance.
        model: Optional model override.

    Returns:
        Gherkin feature text.
    """
    language_map = {"de": "German", "en": "English", "fr": "French"}
    language = language_map.get(spec.user_language, spec.user_language)

    prompt = GHERKIN_PROMPT.format(
        spec_context=spec.to_prompt_context(),
        language=language,
    )

    response = llm.complete(
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": f"Create Gherkin scenarios for: {spec.description}",
            },
        ],
        model=model,
    )

    return response.content.strip()


def parse_gherkin_scenarios(gherkin_text: str) -> list[dict[str, Any]]:
    """Parse Gherkin text into a list of scenario dicts.

    Returns:
        List of dicts with 'title' and 'steps' keys.
    """
    scenarios: list[dict[str, Any]] = []
    current_scenario: dict[str, Any] | None = None

    for line in gherkin_text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Match "Scenario: title" or "Scenario Outline: title"
        scenario_match = re.match(r"Scenario(?:\s+Outline)?:\s*(.+)", line)
        if scenario_match:
            if current_scenario:
                scenarios.append(current_scenario)
            current_scenario = {
                "title": scenario_match.group(1).strip(),
                "steps": [],
            }
            continue

        # Match Given/When/Then/And/But steps
        step_match = re.match(r"(Given|When|Then|And|But)\s+(.+)", line)
        if step_match and current_scenario is not None:
            current_scenario["steps"].append(
                {
                    "keyword": step_match.group(1),
                    "text": step_match.group(2).strip(),
                }
            )

    if current_scenario:
        scenarios.append(current_scenario)

    return scenarios


def format_for_user(scenarios: list[dict[str, Any]]) -> str:
    """Format parsed scenarios as a user-friendly numbered list.

    Returns a string like:
      1. Email mit PDF-Anhang erkennen
      2. PDF zusammenfassen
      3. Zusammenfassung speichern
      4. Email ohne PDF ignorieren
    """
    if not scenarios:
        return "No scenarios generated."

    lines = []
    for i, scenario in enumerate(scenarios, 1):
        lines.append(f"  {i}. {scenario['title']}")
    return "\n".join(lines)
