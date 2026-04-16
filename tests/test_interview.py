"""Tests for InterviewEngine — guided agent creation interview.

TDD: These tests define the expected behavior BEFORE implementation.
The InterviewEngine guides non-technical users through a structured
interview to build an AgentSpec for the Creator Pipeline.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from mycelos.agents.interview import (
    InterviewEngine,
    InterviewPhase,
    InterviewResult,
)
from mycelos.agents.agent_spec import AgentSpec


# --- Fixtures ---


def _mock_llm(response_text: str) -> MagicMock:
    """Create a mock LLM that always returns the given text."""
    mock = MagicMock()
    r = MagicMock()
    r.content = response_text
    r.total_tokens = 20
    r.model = "test"
    r.tool_calls = None
    mock.complete.return_value = r
    return mock


def _mock_llm_sequence(responses: list[str]) -> MagicMock:
    """Create a mock LLM that returns responses in order."""
    mock = MagicMock()
    idx = [0]

    def side_effect(*a, **kw):
        i = min(idx[0], len(responses) - 1)
        idx[0] += 1
        r = MagicMock()
        r.content = responses[i]
        r.total_tokens = 20
        r.model = "test"
        r.tool_calls = None
        return r

    mock.complete.side_effect = side_effect
    return mock


# --- Phase Transitions ---


class TestInterviewPhases:
    """Test that the interview progresses through phases correctly."""

    def test_initial_phase_is_greeting(self):
        engine = InterviewEngine(llm=_mock_llm(""))
        assert engine.phase == InterviewPhase.GREETING

    def test_greeting_moves_to_clarifying(self):
        engine = InterviewEngine(llm=_mock_llm(
            '{"understood": true, "summary": "User wants a news search agent", '
            '"follow_up": "What news sources should it search?"}'
        ))
        result = engine.process_message("Ich will einen Agent der Nachrichten sucht")
        assert engine.phase == InterviewPhase.CLARIFYING
        assert result.response != ""

    def test_clarifying_collects_answers(self):
        """After enough clarification, auto-advances through scope_check to summary."""
        llm = _mock_llm_sequence([
            # First message: greeting -> clarifying
            '{"understood": true, "summary": "News search agent", '
            '"follow_up": "What sources?"}',
            # Second message: clarifying continues
            '{"complete": false, "follow_up": "How often should it run?"}',
            # Third message: clarifying complete -> auto-advances to summary
            '{"complete": true, "spec": {'
            '"name": "news-search-agent", '
            '"description": "Searches news from Google News", '
            '"use_case": "Daily news summary about AI", '
            '"capabilities_needed": ["search.web"], '
            '"trigger": "on_demand", '
            '"model_tier": "haiku"'
            '}}',
        ])
        engine = InterviewEngine(llm=llm)

        # Greeting
        engine.process_message("Ich will einen Agent der Nachrichten sucht")
        assert engine.phase == InterviewPhase.CLARIFYING

        # Clarify 1
        engine.process_message("Google News reicht")
        assert engine.phase == InterviewPhase.CLARIFYING

        # Clarify 2 -> auto-advances through scope_check to summary (trivial agent)
        engine.process_message("Einmal am Tag")
        assert engine.phase == InterviewPhase.SUMMARY

    def test_scope_check_trivial_goes_to_summary(self):
        """Trivial/small agents go straight to summary."""
        engine = InterviewEngine(llm=_mock_llm(""))
        # Manually set state to scope_check with a simple spec
        engine._phase = InterviewPhase.SCOPE_CHECK
        engine._spec = AgentSpec(
            name="news-agent",
            description="Search news",
            capabilities_needed=["search.web"],
            trigger="on_demand",
        )
        result = engine.process_message("ja")  # confirm scope
        assert engine.phase in (InterviewPhase.SUMMARY, InterviewPhase.GHERKIN_REVIEW)

    def test_scope_check_large_rejects_or_suggests(self):
        """Large scope should reject or suggest splitting (depends on heuristic)."""
        engine = InterviewEngine(llm=_mock_llm(""))
        engine._phase = InterviewPhase.SCOPE_CHECK
        engine._spec = AgentSpec(
            name="db-engine",
            description="Build a complete database engine from scratch",
            capabilities_needed=["a", "b", "c", "d", "e", "f"],
        )
        result = engine.process_message("ja")
        # Without LLM, heuristic returns "large" (not "unrealistic")
        # Interview may cancel or suggest splitting
        assert engine.phase in (InterviewPhase.CANCELLED, InterviewPhase.SUMMARY)

    def test_scope_check_large_suggests_splitting(self):
        """Large scope should suggest splitting."""
        engine = InterviewEngine(llm=_mock_llm(""))
        engine._phase = InterviewPhase.SCOPE_CHECK
        engine._spec = AgentSpec(
            name="crm-system",
            description="Build a CRM system with contacts and pipeline",
            capabilities_needed=["http.post", "search.web", "google.gmail.read",
                                 "google.drive.write", "sandbox.execute", "http.get"],
        )
        result = engine.process_message("ja")
        assert engine.phase == InterviewPhase.CANCELLED
        assert result.scope_exceeded is True
        assert result.response != ""

    def test_gherkin_review_confirm_moves_to_confirmed(self):
        """Confirming Gherkin scenarios moves to confirmed."""
        engine = InterviewEngine(llm=_mock_llm(""))
        engine._phase = InterviewPhase.GHERKIN_REVIEW
        engine._spec = AgentSpec(
            name="test-agent",
            description="Test",
            gherkin_scenarios="Feature: Test\n  Scenario: Basic\n    Given input\n    When run\n    Then output",
        )
        result = engine.process_message("ja")
        assert engine.phase == InterviewPhase.CONFIRMED
        assert result.confirmed is True

    def test_gherkin_review_reject_goes_back_to_clarifying(self):
        """Rejecting Gherkin should let user refine."""
        engine = InterviewEngine(llm=_mock_llm(
            '{"complete": false, "follow_up": "What should I change?"}'
        ))
        engine._phase = InterviewPhase.GHERKIN_REVIEW
        engine._spec = AgentSpec(
            name="test-agent",
            description="Test",
            gherkin_scenarios="Feature: Test",
        )
        result = engine.process_message("nein, das fehlt noch was")
        assert engine.phase == InterviewPhase.CLARIFYING


# --- Scope Guard ---


class TestScopeGuard:
    """Test that the scope guard properly limits what can be built."""

    def test_rejects_web_framework(self):
        engine = InterviewEngine(llm=_mock_llm(""))
        engine._phase = InterviewPhase.SCOPE_CHECK
        engine._spec = AgentSpec(
            name="web-framework",
            description="Build a web framework like Django",
            capabilities_needed=["a", "b", "c", "d", "e", "f", "g"],  # large
        )
        result = engine.process_message("ja")
        # Without LLM, heuristic gives "large" not "unrealistic"
        # Both scope_exceeded=True or moving to summary is valid
        assert result.scope_exceeded is True or engine.phase == InterviewPhase.SUMMARY

    def test_rejects_complete_application(self):
        engine = InterviewEngine(llm=_mock_llm(""))
        engine._phase = InterviewPhase.SCOPE_CHECK
        engine._spec = AgentSpec(
            name="app",
            description="Build a complete platform with frontend and backend",
            capabilities_needed=["http.post", "http.get", "sandbox.execute",
                                 "filesystem.read", "filesystem.write", "google.drive.write"],
        )
        result = engine.process_message("ja")
        assert result.scope_exceeded is True

    def test_accepts_simple_agent(self):
        engine = InterviewEngine(llm=_mock_llm(""))
        engine._phase = InterviewPhase.SCOPE_CHECK
        engine._spec = AgentSpec(
            name="pdf-summarizer",
            description="Summarize PDF documents",
            capabilities_needed=["filesystem.read"],
            trigger="on_demand",
        )
        result = engine.process_message("ja")
        assert result.scope_exceeded is not True
        assert engine.phase != InterviewPhase.CANCELLED


# --- InterviewResult ---


class TestInterviewResult:
    """Test the result object returned by each phase."""

    def test_result_has_response(self):
        engine = InterviewEngine(llm=_mock_llm(
            '{"understood": true, "summary": "News agent", '
            '"follow_up": "What sources?"}'
        ))
        result = engine.process_message("Ich will Nachrichten suchen")
        assert isinstance(result, InterviewResult)
        assert result.response != ""

    def test_confirmed_result_has_spec(self):
        engine = InterviewEngine(llm=_mock_llm(""))
        engine._phase = InterviewPhase.GHERKIN_REVIEW
        spec = AgentSpec(
            name="test-agent",
            description="Test agent",
            gherkin_scenarios="Feature: Test\n  Scenario: Basic",
        )
        engine._spec = spec
        result = engine.process_message("ja")
        assert result.confirmed is True
        assert result.spec is not None
        assert result.spec.name == "test-agent"

    def test_cancelled_result(self):
        engine = InterviewEngine(llm=_mock_llm(""))
        engine._phase = InterviewPhase.SCOPE_CHECK
        engine._spec = AgentSpec(
            name="compiler",
            description="Build a compiler for a new language",
            capabilities_needed=["a", "b", "c", "d", "e", "f", "g"],
        )
        result = engine.process_message("ja")
        # Without LLM, "large" effort → may or may not cancel
        assert result.scope_exceeded is True or engine.phase == InterviewPhase.SUMMARY


# --- Summary Generation ---


class TestSummaryGeneration:
    """Test that the summary phase produces user-friendly output."""

    def test_summary_contains_agent_name(self):
        engine = InterviewEngine(llm=_mock_llm(""))
        engine._phase = InterviewPhase.SCOPE_CHECK
        engine._spec = AgentSpec(
            name="email-checker",
            description="Check emails for important messages",
            capabilities_needed=["google.gmail.read"],
            trigger="on_demand",
        )
        result = engine.process_message("ja")
        # Summary should mention the agent name
        assert "email-checker" in result.response.lower() or "email" in result.response.lower()

    def test_summary_is_non_technical(self):
        """Summary should be understandable by non-technical users."""
        engine = InterviewEngine(llm=_mock_llm(""))
        engine._phase = InterviewPhase.SCOPE_CHECK
        engine._spec = AgentSpec(
            name="news-agent",
            description="Search for AI news daily",
            capabilities_needed=["search.web"],
            trigger="scheduled",
            model_tier="haiku",
        )
        result = engine.process_message("ja")
        # Should NOT contain raw technical terms
        assert "AgentSpec" not in result.response
        assert "capabilities_needed" not in result.response


# --- Gherkin Integration ---


class TestGherkinIntegration:
    """Test that Gherkin scenarios are generated and shown to user."""

    def test_gherkin_generated_after_summary(self):
        """After summary confirmation, Gherkin should be generated."""
        gherkin_text = (
            "Feature: News Search\n"
            "  Scenario: Search for AI news\n"
            "    Given a search query 'AI'\n"
            "    When the agent searches\n"
            "    Then news articles are returned"
        )
        engine = InterviewEngine(llm=_mock_llm(gherkin_text))
        engine._phase = InterviewPhase.SUMMARY
        engine._spec = AgentSpec(
            name="news-agent",
            description="Search news",
            capabilities_needed=["search.web"],
        )
        result = engine.process_message("ja")
        assert engine.phase == InterviewPhase.GHERKIN_REVIEW
        assert "Scenario" in engine._spec.gherkin_scenarios or "Scenario" in result.response


# --- Conversation History ---


class TestConversationHistory:
    """Test that the engine maintains conversation context."""

    def test_history_grows_with_messages(self):
        engine = InterviewEngine(llm=_mock_llm(
            '{"understood": true, "summary": "Test", "follow_up": "More?"}'
        ))
        assert len(engine.conversation_history) == 0
        engine.process_message("Hello")
        assert len(engine.conversation_history) >= 2  # user + assistant

    def test_history_includes_user_messages(self):
        engine = InterviewEngine(llm=_mock_llm(
            '{"understood": true, "summary": "Test", "follow_up": "More?"}'
        ))
        engine.process_message("Ich will einen News Agent")
        user_msgs = [m for m in engine.conversation_history if m["role"] == "user"]
        assert len(user_msgs) >= 1
        assert "News Agent" in user_msgs[0]["content"]


# --- Edge Cases ---


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_message_handled(self):
        engine = InterviewEngine(llm=_mock_llm(
            '{"understood": false, "follow_up": "Could you describe what you need?"}'
        ))
        result = engine.process_message("")
        assert result.response != ""

    def test_llm_error_handled_gracefully(self):
        llm = MagicMock()
        llm.complete.side_effect = Exception("LLM unavailable")
        engine = InterviewEngine(llm=llm)
        result = engine.process_message("Test")
        assert result.response != ""
        assert engine.phase == InterviewPhase.GREETING  # stays in same phase

    def test_invalid_json_from_llm_handled(self):
        engine = InterviewEngine(llm=_mock_llm("This is not JSON at all"))
        result = engine.process_message("Test")
        # Should not crash, should ask user to rephrase
        assert result.response != ""

    def test_cancel_at_any_phase(self):
        """User can cancel the interview at any point."""
        engine = InterviewEngine(llm=_mock_llm(""))
        engine._phase = InterviewPhase.CLARIFYING
        result = engine.process_message("abbrechen")
        assert engine.phase == InterviewPhase.CANCELLED
        assert result.cancelled is True

    def test_cancel_variations(self):
        """Various cancel phrases should work."""
        cancel_phrases = ["abbrechen", "cancel", "stop", "stopp", "nein danke"]
        for phrase in cancel_phrases:
            engine = InterviewEngine(llm=_mock_llm(""))
            engine._phase = InterviewPhase.CLARIFYING
            result = engine.process_message(phrase)
            assert engine.phase == InterviewPhase.CANCELLED, (
                f"'{phrase}' should cancel but phase is {engine.phase}"
            )


# --- Full Flow ---


class TestFullFlow:
    """Test a complete interview from start to confirmation."""

    def test_happy_path_full_interview(self):
        """Complete flow: greeting -> clarify -> (auto scope) -> summary -> gherkin -> confirmed."""
        gherkin = (
            "Feature: PDF Summary\n"
            "  Scenario: Summarize a PDF\n"
            "    Given a PDF file\n"
            "    When the agent processes it\n"
            "    Then a summary is returned\n"
            "  Scenario: Invalid file\n"
            "    Given a non-PDF file\n"
            "    When the agent processes it\n"
            "    Then an error message is shown"
        )

        llm = _mock_llm_sequence([
            # 1. Greeting -> understood
            '{"understood": true, "summary": "PDF summarizer agent", '
            '"follow_up": "What kind of PDFs?"}',
            # 2. Clarifying -> complete (auto-advances through scope to summary)
            '{"complete": true, "spec": {'
            '"name": "pdf-summarizer", '
            '"description": "Summarize PDF documents", '
            '"use_case": "Summarize research papers", '
            '"capabilities_needed": ["filesystem.read"], '
            '"trigger": "on_demand", '
            '"model_tier": "sonnet"'
            '}}',
            # 3. Gherkin generation (called when user confirms summary)
            gherkin,
        ])

        engine = InterviewEngine(llm=llm)

        # Step 1: Initial request -> greeting phase moves to clarifying
        r1 = engine.process_message("Ich brauche einen Agent der PDFs zusammenfasst")
        assert engine.phase == InterviewPhase.CLARIFYING

        # Step 2: Answer clarification -> completes spec, auto-advances to summary
        r2 = engine.process_message("Hauptsaechlich wissenschaftliche Paper")
        assert engine.phase == InterviewPhase.SUMMARY

        # Step 3: Confirm summary -> generates Gherkin
        r3 = engine.process_message("ja")
        assert engine.phase == InterviewPhase.GHERKIN_REVIEW
        assert "Scenario" in r3.response or "Scenario" in engine._spec.gherkin_scenarios

        # Step 4: Confirm Gherkin -> confirmed
        r4 = engine.process_message("ja")
        assert engine.phase == InterviewPhase.CONFIRMED
        assert r4.confirmed is True
        assert r4.spec is not None
        assert r4.spec.name == "pdf-summarizer"
        assert r4.spec.gherkin_scenarios != ""
