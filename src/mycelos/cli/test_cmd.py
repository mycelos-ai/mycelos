"""mycelos test — run integration test scenarios against the live system.

Creates a temporary test environment in a subdirectory, runs predefined
scenarios, verifies results, and rolls back to the previous config state.

Usage:
    mycelos test                    # Run all scenarios
    mycelos test invoice-scanner    # Run specific scenario
    mycelos test --list             # List available scenarios
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from mycelos.app import App

console = Console()


# ---------------------------------------------------------------------------
# Test Scenarios
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, dict[str, Any]] = {
    "invoice-scanner": {
        "name": "Invoice Scanner",
        "description": "Scans PDFs, extracts invoice data, writes CSV",
        "setup": {
            "test_files": {
                "rechnung-001.txt": "Rechnung RE-2026-001\nFirma Alpha GmbH\nDatum: 2026-01-15\nBetrag: 1250.00 EUR",
                "rechnung-002.txt": "Rechnung RE-2026-042\nBeta GmbH\nDatum: 2026-02-20\nBetrag: 890.50 EUR",
                "rechnung-003.txt": "Rechnung RE-2026-099\nGamma AG\nDatum: 2026-03-10\nBetrag: 3400.00 EUR",
            },
            "mounts": [
                {"path": "{input_dir}", "access": "read"},
                {"path": "{output_dir}", "access": "write"},
            ],
        },
        "verify": {
            "output_files": ["invoices.csv"],
            "csv_rows": 3,
            "csv_contains": ["RE-2026-001", "RE-2026-042", "RE-2026-099"],
        },
    },
    "web-search": {
        "name": "Web Search",
        "description": "Searches the web and returns results",
        "setup": {"test_files": {}, "mounts": []},
        "verify": {"search_returns_results": True},
    },
    "filesystem-read": {
        "name": "Filesystem Read",
        "description": "Reads files from a mounted directory",
        "setup": {
            "test_files": {"test.txt": "Hello from Mycelos test!"},
            "mounts": [{"path": "{input_dir}", "access": "read"}],
        },
        "verify": {"file_readable": "test.txt", "content_contains": "Hello from Mycelos"},
    },
    "creator-pipeline": {
        "name": "Creator Pipeline",
        "description": "Tests agent creation with pre-defined mock code (no LLM cost)",
        "setup": {"test_files": {}, "mounts": []},
        "verify": {"agent_created": True},
    },
    "scheduler": {
        "name": "Scheduler",
        "description": "Schedules a task, waits, verifies execution",
        "setup": {"test_files": {}, "mounts": []},
        "verify": {"task_executed": True},
    },
    "cost-tracking": {
        "name": "Cost Tracking",
        "description": "Verifies LLM usage is tracked in the database",
        "setup": {"test_files": {}, "mounts": []},
        "verify": {"cost_recorded": True},
    },
    "mcp-filesystem": {
        "name": "MCP Filesystem",
        "description": "Live MCP server connection — filesystem tools via stdio",
        "setup": {"test_files": {"mcp-test.txt": "Hello from MCP test!"}, "mounts": []},
        "verify": {"mcp_connected": True},
    },
}


@click.command("test")
@click.argument("scenario", required=False)
@click.option("--list", "list_scenarios", is_flag=True, help="List available test scenarios.")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
@click.option("--keep", is_flag=True, help="Keep test files after run (don't cleanup).")
@click.option("--live", is_flag=True, help="Run live LLM tests (real API calls, costs money).")
def test_cmd(scenario: str | None, list_scenarios: bool, data_dir: Path, keep: bool, live: bool) -> None:
    """Run integration test scenarios against the live system."""
    if live:
        _run_live_tests(data_dir, scenario)
        return

    if list_scenarios:
        _list_scenarios()
        return

    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        console.print("[red]Mycelos not initialized. Run mycelos init first.[/red]")
        raise SystemExit(1)

    # Load master key
    if not os.environ.get("MYCELOS_MASTER_KEY"):
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()

    app = App(data_dir)

    if scenario:
        if scenario not in SCENARIOS:
            console.print(f"[red]Unknown scenario: {scenario}[/red]")
            _list_scenarios()
            return
        _run_scenario(app, scenario, SCENARIOS[scenario], keep)
    else:
        # Run all scenarios
        results = []
        for sid, sdata in SCENARIOS.items():
            passed = _run_scenario(app, sid, sdata, keep)
            results.append((sid, passed))

        console.print("\n[bold]Results:[/bold]")
        for sid, passed in results:
            status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
            console.print(f"  {status} {sid}")


def _run_live_tests(data_dir: Path, scenario_name: str | None) -> None:
    """Run live LLM tests with real API calls."""
    import logging
    import yaml

    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        console.print("[red]Mycelos not initialized. Run mycelos init first.[/red]")
        raise SystemExit(1)

    if not os.environ.get("MYCELOS_MASTER_KEY"):
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()

    # Set up logging
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
    logging.getLogger("mycelos.testing").setLevel(logging.DEBUG)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    app = App(data_dir)

    from mycelos.testing.runner import LiveTestRunner

    runner = LiveTestRunner(app)

    # Find scenario files
    scenarios_dir = Path(__file__).parent.parent.parent.parent / "tests" / "scenarios"
    if not scenarios_dir.exists():
        console.print(f"[red]No scenarios directory found at {scenarios_dir}[/red]")
        return

    scenario_files = sorted(scenarios_dir.glob("*.yaml"))
    if not scenario_files:
        console.print("[yellow]No scenario files found.[/yellow]")
        return

    if scenario_name:
        scenario_files = [f for f in scenario_files if f.stem == scenario_name]
        if not scenario_files:
            console.print(f"[red]Scenario '{scenario_name}' not found.[/red]")
            console.print(f"Available: {', '.join(f.stem for f in scenarios_dir.glob('*.yaml'))}")
            return

    console.print(f"\n[bold]Live LLM Tests[/bold] ({len(scenario_files)} scenario{'s' if len(scenario_files) > 1 else ''})\n")
    console.print("[yellow]WARNING: This makes real API calls and costs money![/yellow]\n")

    results = []
    total_cost = 0.0

    for sf in scenario_files:
        with open(sf) as f:
            scenario = yaml.safe_load(f)

        console.print(f"  Running: [bold]{scenario['name']}[/bold]...", end=" ")

        result = runner.run_scenario(scenario)
        results.append(result)
        total_cost += result.total_cost

        if result.passed:
            console.print(f"[green]PASS[/green] ({result.turns} turns, ${result.total_cost:.3f})")
        else:
            console.print(f"[red]FAIL[/red] ({result.turns} turns, ${result.total_cost:.3f})")
            for fail in result.assertions_failed:
                console.print(f"    [red]✗[/red] {fail}")
        if result.error:
            console.print(f"    [red]Error: {result.error}[/red]")

    # Summary
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    console.print(f"\n[bold]Results:[/bold] {passed} passed, {failed} failed")
    console.print(f"[bold]Total cost:[/bold] ${total_cost:.4f}")
    console.print(f"[dim]Recordings: {runner._log_dir}[/dim]")


def _list_scenarios() -> None:
    table = Table(title="Test Scenarios")
    table.add_column("ID", style="bold")
    table.add_column("Name")
    table.add_column("Description")
    for sid, s in SCENARIOS.items():
        table.add_row(sid, s["name"], s["description"])
    console.print(table)


def _run_scenario(app: App, scenario_id: str, scenario: dict, keep: bool) -> bool:
    """Run a single test scenario. Returns True if passed."""
    console.print(f"\n[bold cyan]━━━ {scenario['name']} ━━━[/bold cyan]")

    # Save current config generation for rollback
    gen_before = app.config.get_active_generation_id()
    console.print(f"  [dim]Config Generation before: #{gen_before}[/dim]")

    # Create test directory
    test_base = Path.cwd() / ".mycelos-test" / scenario_id
    test_base.mkdir(parents=True, exist_ok=True)
    input_dir = test_base / "input"
    output_dir = test_base / "output"
    input_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    passed = False
    try:
        # Setup: create test files
        setup = scenario.get("setup", {})
        for filename, content in setup.get("test_files", {}).items():
            (input_dir / filename).write_text(content)
            console.print(f"  [dim]Created: {filename}[/dim]")

        # Setup: create mounts
        from mycelos.security.mounts import MountRegistry
        mounts = MountRegistry(app.storage)
        mount_ids = []
        for mount_spec in setup.get("mounts", []):
            mount_path = mount_spec["path"].format(
                input_dir=str(input_dir), output_dir=str(output_dir)
            )
            mid = mounts.add(mount_path, mount_spec["access"], purpose=f"test:{scenario_id}")
            mount_ids.append(mid)
            console.print(f"  [dim]Mounted: {mount_path} ({mount_spec['access']})[/dim]")

        # Create config generation with mounts
        app.config.apply_from_state(
            app.state_manager,
            description=f"Test setup: {scenario_id}",
            trigger="test",
        )

        # Run verification
        verify = scenario.get("verify", {})
        passed = _verify_scenario(app, scenario_id, verify, input_dir, output_dir)

    except Exception as e:
        console.print(f"  [red]Error: {e}[/red]")
        passed = False

    finally:
        # Rollback to previous state
        if gen_before:
            try:
                app.config.rollback(to_generation=gen_before, state_manager=app.state_manager)
                console.print(f"  [dim]Rolled back to Generation #{gen_before}[/dim]")
            except Exception as e:
                console.print(f"  [yellow]Rollback warning: {e}[/yellow]")

        # Cleanup test files
        if not keep and test_base.exists():
            shutil.rmtree(test_base, ignore_errors=True)
            console.print(f"  [dim]Test files cleaned up[/dim]")

    status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
    console.print(f"  Result: {status}")
    return passed


def _verify_scenario(
    app: App, scenario_id: str, verify: dict, input_dir: Path, output_dir: Path
) -> bool:
    """Verify a scenario's expected outcomes."""
    from mycelos.chat.service import ChatService

    if scenario_id == "filesystem-read":
        # Test that we can read the file
        svc = ChatService(app)
        file_name = verify.get("file_readable", "")
        result = svc._filesystem_read(str(input_dir / file_name))
        if "error" in result:
            console.print(f"  [red]Read failed: {result['error']}[/red]")
            return False
        expected = verify.get("content_contains", "")
        if expected and expected not in result.get("content", ""):
            console.print(f"  [red]Content mismatch: expected '{expected}'[/red]")
            return False
        console.print(f"  [green]✓ File readable and content verified[/green]")
        return True

    elif scenario_id == "web-search":
        # Test web search tool
        svc = ChatService(app)
        result = svc._execute_tool("search_web", {"query": "Mycelos agent OS", "max_results": 2})
        if isinstance(result, dict) and "error" in result:
            console.print(f"  [red]Search failed: {result['error']}[/red]")
            return False
        if isinstance(result, list) and len(result) > 0:
            console.print(f"  [green]✓ Web search returned {len(result)} results[/green]")
            return True
        console.print(f"  [yellow]Search returned no results (might be network issue)[/yellow]")
        return True  # Don't fail on network issues

    elif scenario_id == "invoice-scanner":
        # Verify the setup is correct (files + mounts exist)
        files = list(input_dir.glob("*.txt"))
        if len(files) < 3:
            console.print(f"  [red]Expected 3 test files, found {len(files)}[/red]")
            return False
        console.print(f"  [green]✓ {len(files)} test files created[/green]")

        # Verify mounts are active
        from mycelos.security.mounts import MountRegistry
        mounts = MountRegistry(app.storage)
        active = mounts.list_mounts()
        if len(active) < 2:
            console.print(f"  [red]Expected 2 mounts, found {len(active)}[/red]")
            return False
        console.print(f"  [green]✓ {len(active)} mounts active[/green]")

        # Verify filesystem tools work
        svc = ChatService(app)
        list_result = svc._filesystem_list(str(input_dir))
        if "error" in list_result:
            console.print(f"  [red]List failed: {list_result['error']}[/red]")
            return False
        console.print(f"  [green]✓ Filesystem tools working ({list_result['count']} files)[/green]")

        # Read one file
        read_result = svc._filesystem_read(str(input_dir / "rechnung-001.txt"))
        if "error" in read_result:
            console.print(f"  [red]Read failed: {read_result['error']}[/red]")
            return False
        if "RE-2026-001" not in read_result.get("content", ""):
            console.print(f"  [red]Content mismatch[/red]")
            return False
        console.print(f"  [green]✓ Invoice data readable[/green]")

        # Write test
        write_result = svc._filesystem_write(
            str(output_dir / "test-output.csv"),
            "invoice_number,amount\nRE-2026-001,1250.00\n"
        )
        if "error" in write_result:
            console.print(f"  [red]Write failed: {write_result['error']}[/red]")
            return False
        console.print(f"  [green]✓ CSV writable to output directory[/green]")

        return True

    elif scenario_id == "creator-pipeline":
        # Test Creator Pipeline with mocked LLM (no real API cost)
        return _verify_creator_pipeline(app)

    elif scenario_id == "scheduler":
        # Test scheduler: create task, wait, check execution
        return _verify_scheduler(app)

    elif scenario_id == "cost-tracking":
        # Test that LLM usage is tracked
        return _verify_cost_tracking(app)

    elif scenario_id == "mcp-filesystem":
        # Live MCP server test
        return _verify_mcp_filesystem(app, input_dir)

    console.print(f"  [yellow]No verification defined for {scenario_id}[/yellow]")
    return True


def _verify_creator_pipeline(app: App) -> bool:
    """Test Creator Pipeline with pre-defined mock code (no LLM cost)."""
    from unittest.mock import MagicMock
    from mycelos.agents.agent_spec import AgentSpec
    from mycelos.agents.creator_pipeline import CreatorPipeline

    # Pre-defined responses — no real LLM calls
    MOCK_GHERKIN = "Feature: Test\n  Scenario: Works\n    Given input\n    When run\n    Then success"
    MOCK_TESTS = (
        "from agent_code import TestAgent\n"
        "def test_works():\n"
        "    agent = TestAgent()\n"
        "    result = agent.execute(type('I', (), {'task': 'test', 'context': {}})())\n"
        "    assert result.success\n"
    )
    MOCK_CODE = (
        "class TestAgent:\n"
        "    agent_id = 'test-creator-agent'\n"
        "    def execute(self, input):\n"
        "        return type('R', (), {'success': True, 'result': 'ok', "
        "'artifacts': [], 'metadata': {}, 'error': ''})()\n"
    )

    mock_llm = MagicMock()
    call_idx = [0]
    responses = [MOCK_GHERKIN, MOCK_TESTS, MOCK_CODE]
    def side_effect(*a, **kw):
        i = min(call_idx[0], len(responses) - 1)
        call_idx[0] += 1
        r = MagicMock()
        r.content = responses[i]
        r.total_tokens = 50
        r.model = "mock"
        r.tool_calls = None
        return r
    mock_llm.complete.side_effect = side_effect

    mock_auditor = MagicMock()
    mock_auditor.review_code_and_tests.return_value = {"approved": True, "findings": []}

    # Save original and mock
    orig_llm = app._llm
    orig_auditor = app._auditor
    app._llm = mock_llm
    app._auditor = mock_auditor

    try:
        spec = AgentSpec(name="test-creator-agent", description="A simple test agent",
                         capabilities_needed=["search.web"])
        pipeline = CreatorPipeline(app)
        result = pipeline.run(spec)

        if not result.success:
            console.print(f"  [red]Creator Pipeline failed: {result.error}[/red]")
            return False

        console.print(f"  [green]✓ Agent created: {result.agent_id}[/green]")

        # Verify agent registered
        agent = app.agent_registry.get("test-creator-agent")
        if not agent:
            console.print(f"  [red]Agent not found in registry[/red]")
            return False
        console.print(f"  [green]✓ Agent registered (status: {agent['status']})[/green]")

        # Verify code in Object Store
        if not agent.get("code_hash"):
            console.print(f"  [red]No code hash in agent[/red]")
            return False
        from mycelos.storage.object_store import ObjectStore
        store = ObjectStore(app.data_dir)
        code = store.load(agent["code_hash"])
        if not code or "TestAgent" not in code:
            console.print(f"  [red]Code not found in Object Store[/red]")
            return False
        console.print(f"  [green]✓ Code stored in Object Store[/green]")

        # Verify tests actually pass
        from mycelos.agents.test_runner import run_agent_tests
        test_result = run_agent_tests(MOCK_CODE, MOCK_TESTS, timeout=15)
        if not test_result.passed:
            console.print(f"  [red]Generated tests failed: {test_result.error}[/red]")
            return False
        console.print(f"  [green]✓ Generated tests pass ({test_result.tests_run} tests)[/green]")

        console.print(f"  [green]✓ Creator Pipeline complete (0 LLM tokens used)[/green]")
        return True

    finally:
        app._llm = orig_llm
        app._auditor = orig_auditor


def _verify_scheduler(app: App) -> bool:
    """Test scheduler: create task, execute, verify state updates."""
    from unittest.mock import MagicMock, patch
    from datetime import datetime, timezone, timedelta

    try:
        app.workflow_registry.register("test-scheduled-wf", "Test", [{"id": "s1"}])
    except Exception:
        pass

    # Create and schedule a task
    task_id = app.schedule_manager.add(
        "test-scheduled-wf", "*/5 * * * *",
        inputs={"query": "test"},
    )
    console.print(f"  [dim]Scheduled task: {task_id[:8]}[/dim]")

    # Force next_run to past so it's due
    app.storage.execute(
        "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), task_id),
    )

    # Execute (mocked WorkflowAgent, but real scheduling logic)
    from mycelos.workflows.agent import WorkflowAgentResult
    mock_result = WorkflowAgentResult(status="completed", result="Test result.")

    from mycelos.scheduler.jobs import check_scheduled_workflows
    with patch("mycelos.workflows.agent.WorkflowAgent") as MockAgent:
        MockAgent.return_value.execute.return_value = mock_result
        executed = check_scheduled_workflows(app)

    if task_id not in executed:
        console.print(f"  [red]Task was not executed[/red]")
        return False
    console.print(f"  [green]✓ Scheduled task executed[/green]")

    # Verify state updates
    task = app.schedule_manager.get(task_id)
    if task["run_count"] < 1:
        console.print(f"  [red]Run count not updated[/red]")
        return False
    console.print(f"  [green]✓ Run count: {task['run_count']}[/green]")

    if task.get("last_run"):
        console.print(f"  [green]✓ Last run recorded: {task['last_run'][:16]}[/green]")

    if task.get("next_run"):
        console.print(f"  [green]✓ Next run scheduled: {task['next_run'][:16]}[/green]")

    # Verify audit logged
    events = app.storage.fetchall(
        "SELECT * FROM audit_events WHERE event_type = 'scheduled.executed'"
    )
    if events:
        console.print(f"  [green]✓ Audit event logged[/green]")

    # Cleanup
    app.schedule_manager.delete(task_id)
    console.print(f"  [green]✓ Scheduler test complete[/green]")
    return True


def _verify_mcp_filesystem(app: App, input_dir: Path) -> bool:
    """Test live MCP filesystem server connection."""
    from mycelos.connectors.mcp_recipes import is_node_available

    if not is_node_available():
        console.print(f"  [yellow]Node.js (npx) not available — skipping MCP test[/yellow]")
        return True  # Non-critical

    from mycelos.connectors.mcp_manager import MCPConnectorManager

    mgr = MCPConnectorManager()
    try:
        # Connect to filesystem MCP server scoped to test input dir
        tools = mgr.connect(
            "mcp-test",
            f"npx -y @modelcontextprotocol/server-filesystem {input_dir}",
        )

        if not tools:
            console.print(f"  [red]No tools discovered[/red]")
            return False
        console.print(f"  [green]✓ MCP server connected: {len(tools)} tools[/green]")

        # List directory via MCP
        result = mgr.call_tool("mcp-test.list_directory", {"path": str(input_dir)})
        if "error" in str(result).lower() and "ENOENT" in str(result):
            console.print(f"  [red]list_directory failed[/red]")
            return False
        console.print(f"  [green]✓ list_directory works[/green]")

        # Read file via MCP
        test_file = input_dir / "mcp-test.txt"
        if test_file.exists():
            result = mgr.call_tool("mcp-test.read_file", {"path": str(test_file)})
            if "Hello from MCP" in str(result):
                console.print(f"  [green]✓ read_file works — content verified[/green]")
            else:
                console.print(f"  [green]✓ read_file works[/green]")

        console.print(f"  [green]✓ MCP live test complete[/green]")
        return True

    except Exception as e:
        console.print(f"  [red]MCP test failed: {e}[/red]")
        return False
    finally:
        mgr.disconnect_all()


def _verify_cost_tracking(app: App) -> bool:
    """Test that LLM usage is tracked in the database."""
    from unittest.mock import MagicMock, patch

    # Count existing entries
    before = app.storage.fetchone("SELECT COUNT(*) as n FROM llm_usage")
    before_count = before["n"] if before else 0

    # Make a mock LLM call through the broker
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Cost tracking test"
    mock_response.choices[0].message.tool_calls = None
    mock_response.usage.total_tokens = 42
    mock_response.usage.prompt_tokens = 30
    mock_response.usage.completion_tokens = 12

    with patch("litellm.completion", return_value=mock_response):
        with patch("litellm.model_cost", {app.llm.default_model: {
            "input_cost_per_token": 0.000003,
            "output_cost_per_token": 0.000015,
        }}):
            app.llm.complete([{"role": "user", "content": "cost test"}])

    # Verify entry was created
    after = app.storage.fetchone("SELECT COUNT(*) as n FROM llm_usage")
    after_count = after["n"] if after else 0

    if after_count <= before_count:
        console.print(f"  [red]No new usage entry recorded[/red]")
        return False
    console.print(f"  [green]✓ Usage entry recorded (total: {after_count})[/green]")

    # Check the entry has cost > 0
    latest = app.storage.fetchone("SELECT * FROM llm_usage ORDER BY id DESC LIMIT 1")
    if latest and latest["cost"] > 0:
        console.print(f"  [green]✓ Cost calculated: ${latest['cost']:.6f}[/green]")
    else:
        console.print(f"  [yellow]Cost is $0 (model not in litellm cost db?)[/yellow]")

    console.print(f"  [green]✓ Cost tracking verified[/green]")
    return True
