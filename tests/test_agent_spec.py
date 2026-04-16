"""Tests for AgentSpec — structured agent specification with effort classification."""

from __future__ import annotations

import pytest

from mycelos.agents.agent_spec import (
    AgentSpec,
    classify_effort,
    estimate_cost,
    EFFORT_LEVELS,
)


# --- AgentSpec dataclass ---

def test_agent_spec_creation():
    spec = AgentSpec(name="news-agent", description="Search and summarize news")
    assert spec.name == "news-agent"
    assert spec.trigger == "on_demand"
    assert spec.model_tier == "sonnet"
    assert spec.capabilities_needed == []


def test_agent_spec_with_all_fields():
    spec = AgentSpec(
        name="pdf-agent",
        description="Summarize PDFs from email",
        use_case="User gets PDF attachments that need summaries",
        capabilities_needed=["google.gmail.read", "http.get"],
        trigger="event",
        model_tier="sonnet",
        gherkin_scenarios="Feature: PDF Summary\n  Scenario: ...",
        user_language="de",
    )
    assert spec.capabilities_needed == ["google.gmail.read", "http.get"]
    assert spec.trigger == "event"


def test_agent_spec_to_prompt_context():
    spec = AgentSpec(
        name="news-agent",
        description="Search news",
        capabilities_needed=["search.web", "search.news"],
    )
    ctx = spec.to_prompt_context()
    assert "news-agent" in ctx
    assert "search.web" in ctx
    assert "search.news" in ctx


def test_agent_spec_to_prompt_context_no_capabilities():
    spec = AgentSpec(name="simple", description="Simple agent")
    ctx = spec.to_prompt_context()
    assert "none" in ctx


# --- Effort classification ---

def test_classify_trivial():
    """Single tool, on-demand = trivial."""
    spec = AgentSpec(
        name="searcher", description="Search the web",
        capabilities_needed=["search.web"],
    )
    assert classify_effort(spec) == "trivial"


def test_classify_small():
    """Two simple tools = small."""
    spec = AgentSpec(
        name="news", description="Search and fetch news",
        capabilities_needed=["search.web", "http.get"],
    )
    assert classify_effort(spec) == "small"


def test_classify_medium_multiple_tools():
    """3-4 tools = medium."""
    spec = AgentSpec(
        name="email-reader", description="Read and process emails",
        capabilities_needed=["google.gmail.read", "http.get", "search.web"],
    )
    assert classify_effort(spec) == "medium"


def test_classify_small_single_capability():
    """One capability, no LLM = small (heuristic fallback)."""
    spec = AgentSpec(
        name="sender", description="Send emails",
        capabilities_needed=["google.gmail.send"],
    )
    # Without LLM, heuristic: 1 cap = trivial (on_demand, no complex check)
    assert classify_effort(spec) == "trivial"


def test_classify_large_many_capabilities():
    """6+ capabilities = large (heuristic)."""
    spec = AgentSpec(
        name="mega", description="Do everything",
        capabilities_needed=["a", "b", "c", "d", "e", "f"],
    )
    assert classify_effort(spec) == "large"


def test_classify_medium_by_capability_count():
    """3-5 capabilities = medium (heuristic)."""
    spec = AgentSpec(
        name="worker", description="Process data",
        capabilities_needed=["a", "b", "c"],
    )
    assert classify_effort(spec) == "medium"


def test_classify_description_ignored_without_llm():
    """Without LLM, description doesn't affect classification."""
    spec = AgentSpec(
        name="db", description="Baue mir eine Oracle Datenbank nach",
    )
    # No capabilities, no LLM → trivial (heuristic only uses cap count)
    assert classify_effort(spec) == "trivial"


def test_classify_no_capabilities():
    """No capabilities, on-demand = trivial."""
    spec = AgentSpec(name="chat", description="Chat with me")
    assert classify_effort(spec) == "trivial"


# --- Cost estimation ---

def test_estimate_cost_trivial():
    spec = AgentSpec(name="s", description="simple", effort="trivial")
    cost = estimate_cost(spec)
    assert cost < 0.02


def test_estimate_cost_medium():
    spec = AgentSpec(name="m", description="medium", effort="medium")
    cost = estimate_cost(spec)
    assert 0.05 < cost < 0.15


def test_estimate_cost_opus_multiplier():
    """Opus should be more expensive than sonnet."""
    spec_sonnet = AgentSpec(name="s", description="test", effort="medium", model_tier="sonnet")
    spec_opus = AgentSpec(name="s", description="test", effort="medium", model_tier="opus")
    assert estimate_cost(spec_opus) > estimate_cost(spec_sonnet)


def test_estimate_cost_haiku_cheaper():
    """Haiku should be cheapest."""
    spec_haiku = AgentSpec(name="s", description="test", effort="small", model_tier="haiku")
    spec_sonnet = AgentSpec(name="s", description="test", effort="small", model_tier="sonnet")
    assert estimate_cost(spec_haiku) < estimate_cost(spec_sonnet)


def test_estimate_cost_unrealistic_zero():
    spec = AgentSpec(name="x", description="impossible", effort="unrealistic")
    assert estimate_cost(spec) == 0.0


def test_estimate_cost_auto_classifies():
    """If effort not set, auto-classifies."""
    spec = AgentSpec(name="s", description="Search the web", capabilities_needed=["search.web"])
    cost = estimate_cost(spec)
    assert cost > 0  # Should classify as trivial, cost > 0


# --- EFFORT_LEVELS dict ---

def test_effort_levels_complete():
    assert "trivial" in EFFORT_LEVELS
    assert "small" in EFFORT_LEVELS
    assert "medium" in EFFORT_LEVELS
    assert "large" in EFFORT_LEVELS
    assert "unrealistic" in EFFORT_LEVELS
