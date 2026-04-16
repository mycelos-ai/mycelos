"""Tests for Gherkin Generator — creates acceptance scenarios from AgentSpec."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mycelos.agents.agent_spec import AgentSpec
from mycelos.agents.gherkin_generator import (
    GHERKIN_PROMPT,
    format_for_user,
    generate_gherkin,
    parse_gherkin_scenarios,
)


SAMPLE_GHERKIN = """\
Feature: PDF Email Summarizer
  Als Benutzer moechte ich PDF-Anhaenge zusammengefasst bekommen.

  Scenario: Email mit PDF-Anhang erkennen
    Given eine neue Email mit einem PDF-Anhang ist eingetroffen
    When der Agent die Email verarbeitet
    Then soll er den PDF-Anhang erkennen

  Scenario: PDF zusammenfassen
    Given ein PDF-Dokument wurde erkannt
    When der Agent das PDF verarbeitet
    Then soll eine Zusammenfassung erstellt werden
    And die Zusammenfassung soll maximal 500 Woerter lang sein

  Scenario: Email ohne PDF ignorieren
    Given eine neue Email ohne PDF-Anhang
    When der Agent die Email verarbeitet
    Then soll er sie ignorieren
"""


# --- generate_gherkin ---


def test_generate_gherkin_calls_llm() -> None:
    spec = AgentSpec(name="pdf-agent", description="Summarize PDFs from email")
    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(content=SAMPLE_GHERKIN)

    result = generate_gherkin(spec, mock_llm)

    mock_llm.complete.assert_called_once()
    assert "Feature" in result
    assert "Scenario" in result


def test_generate_gherkin_uses_spec_context() -> None:
    spec = AgentSpec(
        name="news-agent",
        description="Search news",
        capabilities_needed=["search.web"],
        user_language="en",
    )
    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(
        content=(
            "Feature: News\n"
            "  Scenario: Find\n"
            "    Given query\n"
            "    When search\n"
            "    Then results"
        )
    )

    generate_gherkin(spec, mock_llm)

    # Check that prompt contains spec info
    call_args = mock_llm.complete.call_args
    messages = call_args.kwargs.get("messages") or call_args[0][0]
    system_msg = messages[0]["content"]
    assert "news-agent" in system_msg or "Search news" in system_msg


def test_generate_gherkin_with_model_override() -> None:
    spec = AgentSpec(name="test", description="test")
    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(content="Feature: Test")

    generate_gherkin(spec, mock_llm, model="claude-haiku-4-5")

    call_args = mock_llm.complete.call_args
    assert call_args.kwargs.get("model") == "claude-haiku-4-5"


def test_generate_gherkin_language_mapping() -> None:
    """Verify that user_language codes are mapped to full language names."""
    for lang_code, lang_name in [("de", "German"), ("en", "English"), ("fr", "French")]:
        spec = AgentSpec(name="t", description="t", user_language=lang_code)
        mock_llm = MagicMock()
        mock_llm.complete.return_value = MagicMock(content="Feature: T")

        generate_gherkin(spec, mock_llm)

        call_args = mock_llm.complete.call_args
        messages = call_args.kwargs.get("messages") or call_args[0][0]
        system_msg = messages[0]["content"]
        assert lang_name in system_msg


def test_generate_gherkin_strips_whitespace() -> None:
    spec = AgentSpec(name="t", description="t")
    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(content="  Feature: T  \n\n")

    result = generate_gherkin(spec, mock_llm)
    assert result == "Feature: T"


# --- parse_gherkin_scenarios ---


def test_parse_scenarios() -> None:
    scenarios = parse_gherkin_scenarios(SAMPLE_GHERKIN)
    assert len(scenarios) == 3
    assert scenarios[0]["title"] == "Email mit PDF-Anhang erkennen"
    assert scenarios[1]["title"] == "PDF zusammenfassen"
    assert scenarios[2]["title"] == "Email ohne PDF ignorieren"


def test_parse_scenario_steps() -> None:
    scenarios = parse_gherkin_scenarios(SAMPLE_GHERKIN)
    steps = scenarios[0]["steps"]
    assert len(steps) == 3
    assert steps[0]["keyword"] == "Given"
    assert steps[1]["keyword"] == "When"
    assert steps[2]["keyword"] == "Then"


def test_parse_scenario_with_and() -> None:
    scenarios = parse_gherkin_scenarios(SAMPLE_GHERKIN)
    steps = scenarios[1]["steps"]
    assert len(steps) == 4
    assert steps[3]["keyword"] == "And"


def test_parse_empty_text() -> None:
    assert parse_gherkin_scenarios("") == []


def test_parse_no_scenarios() -> None:
    assert parse_gherkin_scenarios("Feature: Test\n  Some description") == []


def test_parse_scenario_outline() -> None:
    gherkin = (
        "Scenario Outline: Test with <param>\n"
        "  Given a <param>\n"
        "  When action\n"
        "  Then result\n"
    )
    scenarios = parse_gherkin_scenarios(gherkin)
    assert len(scenarios) == 1
    assert scenarios[0]["title"] == "Test with <param>"


def test_parse_scenario_with_but() -> None:
    gherkin = (
        "Scenario: Edge case\n"
        "  Given a condition\n"
        "  When action\n"
        "  Then result\n"
        "  But not this\n"
    )
    scenarios = parse_gherkin_scenarios(gherkin)
    assert len(scenarios[0]["steps"]) == 4
    assert scenarios[0]["steps"][3]["keyword"] == "But"


# --- format_for_user ---


def test_format_for_user() -> None:
    scenarios = parse_gherkin_scenarios(SAMPLE_GHERKIN)
    formatted = format_for_user(scenarios)
    assert "1." in formatted
    assert "2." in formatted
    assert "3." in formatted
    assert "PDF-Anhang" in formatted


def test_format_for_user_empty() -> None:
    assert "No scenarios" in format_for_user([])


# --- Prompt template ---


def test_gherkin_prompt_has_placeholders() -> None:
    assert "{spec_context}" in GHERKIN_PROMPT
    assert "{language}" in GHERKIN_PROMPT
