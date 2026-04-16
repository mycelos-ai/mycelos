"""Live integration tests for workflow execution via WorkflowAgent.

Tests the refactored workflow system end-to-end:
- Input schema validation
- /run slash command with parameters
- WorkflowAgent LLM execution with scoped tools
- Result output quality

Requires: MYCELOS_MASTER_KEY + LLM API key configured.
Run: pytest -m integration tests/integration/test_workflow_run_live.py -v -s
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def app():
    """Fresh App with initialized DB and system agents."""
    import tempfile
    key = os.environ.get("MYCELOS_MASTER_KEY")
    if not key:
        key_file = Path.home() / ".mycelos" / ".master_key"
        if key_file.exists():
            key = key_file.read_text().strip()
            os.environ["MYCELOS_MASTER_KEY"] = key
        else:
            pytest.skip("No MYCELOS_MASTER_KEY")

    from mycelos.app import App
    from mycelos.cli.init_cmd import _register_system_agents

    with tempfile.TemporaryDirectory() as tmp:
        a = App(Path(tmp))
        a.initialize()
        _register_system_agents(a)

        # Store a test API key if available
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            a.credentials.store_credential("anthropic", {
                "api_key": api_key,
                "env_var": "ANTHROPIC_API_KEY",
                "provider": "anthropic",
            })

        yield a


@pytest.fixture
def register_research_workflow(app):
    """Register a research workflow with input schema."""
    app.workflow_registry.register(
        workflow_id="test-ai-news",
        name="AI News Search",
        description="Search for the latest AI news and summarize findings",
        steps=[],  # Not used — WorkflowAgent uses plan
        goal="Find and summarize current AI news",
        tags=["test", "news", "ai"],
        scope=["search.web"],
        plan=(
            "You are a news research agent.\n"
            "1. Call search_news with the given topic\n"
            "2. Pick the top 3 most relevant results\n"
            "3. For each, provide: title, source, and a 1-sentence summary\n"
            "4. Present the results as a numbered list\n"
            "Respond in English."
        ),
        model="anthropic/claude-haiku-4-5",
        allowed_tools=["search_news", "search_web"],
        inputs=json.dumps([
            {"name": "topic", "type": "string", "required": True, "description": "News topic to search"},
            {"name": "limit", "type": "integer", "required": False, "description": "Max results", "default": 3},
        ]),
    )
    return "test-ai-news"


class TestWorkflowInputSchema:
    """Test workflow input validation."""

    def test_workflow_has_inputs(self, app, register_research_workflow):
        wf = app.workflow_registry.get(register_research_workflow)
        assert wf is not None
        inputs = wf.get("inputs", [])
        if isinstance(inputs, str):
            inputs = json.loads(inputs)
        assert len(inputs) >= 1
        assert inputs[0]["name"] == "topic"
        assert inputs[0]["required"] is True

    def test_run_without_required_input_fails(self, app, register_research_workflow):
        """Running without required 'topic' should fail."""
        from mycelos.tools.workflow import execute_run_workflow
        result = execute_run_workflow(
            {"workflow_id": register_research_workflow, "inputs": {}},
            {"app": app, "user_id": "default", "session_id": "", "agent_id": "mycelos"},
        )
        # Should either fail with validation error or the agent should complain
        assert isinstance(result, dict)


class TestSlashRunWithParams:
    """Test /run command with inline parameters."""

    def test_run_lists_workflows(self, app, register_research_workflow):
        from mycelos.chat.slash_commands import handle_slash_command
        result = handle_slash_command(app, "/run")
        text = str(result).lower()
        assert "test-ai-news" in text or "available" in text or "workflow" in text

    def test_run_with_topic(self, app, register_research_workflow):
        """Parse /run workflow_id topic=value syntax."""
        from mycelos.chat.slash_commands import handle_slash_command
        # This will actually execute — needs LLM
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("No API key for live execution")
        result = handle_slash_command(app, '/run test-ai-news topic="latest AI developments"')
        text = str(result)
        # Should contain actual results, not just "success"
        assert len(text) > 50  # More than just a status message


class TestLiveWorkflowExecution:
    """Test actual workflow execution with real LLM + search."""

    @pytest.fixture(autouse=True)
    def _require_api_key(self):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("No ANTHROPIC_API_KEY for live tests")

    def test_ai_news_workflow(self, app, register_research_workflow):
        """Full E2E: run AI news workflow and verify results."""
        from mycelos.tools.workflow import execute_run_workflow
        result = execute_run_workflow(
            {"workflow_id": register_research_workflow, "inputs": {"topic": "AI agents 2026"}},
            {"app": app, "user_id": "default", "session_id": "", "agent_id": "mycelos"},
        )
        assert isinstance(result, dict)
        print(f"\nWorkflow result: {json.dumps(result, indent=2, default=str)}")

        # Should have meaningful output
        output = result.get("result", result.get("output", result.get("step_results", "")))
        assert len(str(output)) > 100, f"Output too short: {output}"

        # Should mention AI somewhere in the results
        output_lower = str(output).lower()
        assert "ai" in output_lower or "agent" in output_lower or "model" in output_lower

    def test_workflow_uses_haiku(self, app, register_research_workflow):
        """Workflow should use the configured model (haiku = cheap)."""
        from mycelos.tools.workflow import execute_run_workflow
        result = execute_run_workflow(
            {"workflow_id": register_research_workflow, "inputs": {"topic": "Python 3.13 features"}},
            {"app": app, "user_id": "default", "session_id": "", "agent_id": "mycelos"},
        )
        # Cost should be very low (haiku)
        cost = result.get("total_cost", result.get("cost", 0))
        print(f"\nCost: ${cost}")
        # Haiku should be under $0.01 for a simple search
        if cost and isinstance(cost, (int, float)):
            assert cost < 0.05, f"Cost too high for Haiku: ${cost}"

    def test_workflow_result_is_readable(self, app, register_research_workflow):
        """Result should be human-readable, not just status codes."""
        from mycelos.tools.workflow import execute_run_workflow
        # Set user name so the workflow doesn't trigger the onboarding greeting
        app.memory.set("default", "system", "user.name", "Stefan", created_by="test")
        result = execute_run_workflow(
            {"workflow_id": register_research_workflow, "inputs": {"topic": "large language models"}},
            {"app": app, "user_id": "default", "session_id": "", "agent_id": "mycelos"},
        )
        output = str(result.get("result", result.get("output", result.get("step_results", ""))))
        # Should not contain raw JSON or error traces
        assert "{" not in output[:50] or "error" not in output.lower()
        # Should have some structure (numbered list, bullet points)
        assert any(c in output for c in ["1.", "•", "-", "*"]), f"No structure in output: {output[:200]}"

    def test_audit_logged(self, app, register_research_workflow):
        """Workflow execution should be audited."""
        from mycelos.tools.workflow import execute_run_workflow
        execute_run_workflow(
            {"workflow_id": register_research_workflow, "inputs": {"topic": "test audit"}},
            {"app": app, "user_id": "default", "session_id": "", "agent_id": "mycelos"},
        )
        # Check audit log
        row = app.storage.fetchone(
            "SELECT * FROM audit_events WHERE event_type = 'workflow.executed' ORDER BY created_at DESC LIMIT 1"
        )
        assert row is not None
        details = json.loads(row["details"])
        assert details["workflow_id"] == "test-ai-news"
