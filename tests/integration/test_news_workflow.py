"""Integration test: News Summary Workflow -- real API calls.

Run with: pytest -m integration tests/integration/test_news_workflow.py -v

This test:
1. Parses the news-summary workflow YAML
2. Executes it with real DuckDuckGo search + real HTTP fetch + mock LLM
3. Verifies the pipeline works end-to-end
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mycelos.workflows.parser import WorkflowParser
from mycelos.workflows.models import WorkflowStep
from mycelos.agents.models import AgentOutput
from mycelos.connectors.search_tools import search_news
from mycelos.connectors.http_tools import http_get

WORKFLOW_PATH = Path(__file__).parent.parent.parent / "artifacts" / "workflows" / "news-summary.yaml"


@pytest.mark.integration
def test_news_search_returns_results() -> None:
    """Real DuckDuckGo news search returns actual results."""
    results = search_news("artificial intelligence", max_results=3)
    assert len(results) > 0
    assert "title" in results[0]
    assert "url" in results[0]
    print(f"Found {len(results)} news articles")
    for r in results:
        print(f"  - {r['title'][:60]}... ({r.get('source', 'unknown')})")


@pytest.mark.integration
def test_http_get_real_page() -> None:
    """Real HTTP GET fetches an actual page."""
    result = http_get("https://example.com", timeout=10)
    assert result["status"] == 200
    assert "Example Domain" in result["body"]


@pytest.mark.integration
def test_news_workflow_parse() -> None:
    """Parse the news-summary workflow YAML (legacy format)."""
    pytest.skip("Legacy YAML parser deprecated — workflows now use plan-based format via registry")


@pytest.mark.integration
def test_news_workflow_full_execution() -> None:
    """Full workflow: search -> fetch -> summarize (with mock LLM for summary step)."""
    pytest.skip("WorkflowExecutor removed — test needs rewrite for WorkflowAgent")
    if not WORKFLOW_PATH.exists():
        pytest.skip(f"Workflow file not found: {WORKFLOW_PATH}")

    parser = WorkflowParser()
    wf = parser.parse_file(WORKFLOW_PATH)

    def real_runner(step: WorkflowStep, context: dict[str, Any]) -> AgentOutput:
        """Real runner: actual connectors for search/fetch, mock for LLM summary."""
        if step.id == "search":
            topic = "artificial intelligence 2026"
            results = search_news(topic, max_results=3)
            return AgentOutput(
                success=True,
                result={"articles": results, "topic": topic},
                artifacts=[],
                metadata={"cost": 0.0, "tool": "search.news"},
            )
        elif step.id == "fetch-articles":
            articles = (
                context.get("steps", {})
                .get("search", {})
                .get("result", {})
                .get("articles", [])
            )
            fetched: list[dict[str, Any]] = []
            for article in articles[:2]:  # Fetch top 2
                url = article.get("url", "")
                if url:
                    page = http_get(url, timeout=10)
                    fetched.append({
                        "title": article.get("title", ""),
                        "url": url,
                        "content_length": len(page.get("body", "")),
                        "status": page.get("status", 0),
                    })
            return AgentOutput(
                success=True,
                result={"fetched": fetched},
                artifacts=[],
                metadata={"cost": 0.0, "tool": "http.get"},
            )
        elif step.id == "summarize":
            # Mock the LLM summary step
            articles = (
                context.get("steps", {})
                .get("search", {})
                .get("result", {})
                .get("articles", [])
            )
            summary_lines = ["## News Summary: AI in 2026\n"]
            for a in articles:
                summary_lines.append(f"### {a.get('title', 'Unknown')}")
                summary_lines.append(f"{a.get('snippet', 'No snippet')}\n")
            summary = "\n".join(summary_lines)
            return AgentOutput(
                success=True,
                result=summary,
                artifacts=[],
                metadata={"cost": 0.003, "tool": "llm.complete", "model": "mock"},
            )
        else:
            return AgentOutput(
                success=False,
                result=None,
                artifacts=[],
                metadata={},
                error=f"Unknown step: {step.id}",
            )

    executor = WorkflowExecutor(agent_runner=real_runner)
    result = executor.execute(wf)

    assert result.success is True
    assert len(result.step_results) == 3
    assert result.total_cost >= 0
    print(f"\nWorkflow completed! Cost: ${result.total_cost:.4f}")
    print(f"Steps: {list(result.step_results.keys())}")

    # Check the summary output
    summary = result.step_results["summarize"].result
    assert "##" in summary  # Has markdown headers
    print(f"\n{summary}")
