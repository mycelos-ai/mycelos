"""mycelos doctor — diagnose system issues, check health, suggest fixes."""

from __future__ import annotations

import os
from pathlib import Path
from mycelos.cli import default_data_dir

import click
from rich.console import Console

from mycelos.i18n import t

console = Console()

STATUS_ICONS = {
    "ok": "[green]OK[/green]",
    "warning": "[yellow]WARN[/yellow]",
    "error": "[red]ERROR[/red]",
    "not configured": "[dim]--[/dim]",
    "unknown": "[dim]?[/dim]",
}


@click.command()
@click.option("--data-dir", type=click.Path(path_type=Path), default=default_data_dir)
@click.option("--check", type=str, default=None, help="Check specific category: reminders, schedules, config, telegram")
@click.option("--why", "why_question", type=str, default=None, is_flag=False, flag_value="", help="LLM diagnosis: describe what's not working (interactive if no value)")
@click.option("--fix", is_flag=True, help="Auto-fix issues where possible")
def doctor_cmd(data_dir: Path, check: str | None, why_question: str | None, fix: bool) -> None:
    """Diagnose system issues and suggest fixes."""
    from mycelos.app import App

    if not data_dir.exists():
        console.print("[red]Mycelos not initialized.[/red] Run: mycelos init")
        raise SystemExit(1)

    # Load master key
    if not os.environ.get("MYCELOS_MASTER_KEY"):
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()

    app = App(data_dir)

    # --why mode: LLM-powered diagnosis
    if why_question is not None:
        if not why_question.strip():
            why_question = click.prompt("  What's the problem?", default="")
            if not why_question.strip():
                console.print("[dim]No question provided.[/dim]")
                return
        _run_why(app, why_question)
        return

    # --check mode: specific category
    if check:
        _run_check(app, check, fix)
        return

    # Default: full health check
    _run_full_check(app, fix)


def _run_full_check(app, fix: bool) -> None:
    """Run all health checks and display results."""
    from mycelos.doctor.checks import run_health_checks

    console.print("\n[bold]Mycelos Doctor[/bold] — System Health Check\n")

    # Detect gateway URL
    from mycelos.cli.serve_cmd import is_gateway_running, DEFAULT_PORT
    gateway_url = f"http://localhost:{DEFAULT_PORT}" if is_gateway_running() else None

    results = run_health_checks(app, gateway_url=gateway_url)

    for r in results:
        icon = STATUS_ICONS.get(r["status"], "[dim]?[/dim]")
        console.print(f"  {icon}  [bold]{r['category']}[/bold]: {r['details']}")

    # Summary
    errors = sum(1 for r in results if r["status"] == "error")
    warnings = sum(1 for r in results if r["status"] == "warning")
    console.print()

    if errors:
        console.print(f"  [red]{errors} error(s)[/red], {warnings} warning(s)")
    elif warnings:
        console.print(f"  [yellow]{warnings} warning(s)[/yellow], no errors")
    else:
        console.print("  [green]All checks passed![/green]")

    # Fix suggestions
    if fix and warnings + errors > 0:
        _auto_fix(app, results)

    console.print()
    console.print("  [dim]For detailed diagnosis: mycelos doctor --why \"describe your problem\"[/dim]")
    console.print()


def _run_check(app, category: str, fix: bool) -> None:
    """Run a specific category check."""
    from mycelos.doctor import checks

    console.print(f"\n[bold]Checking: {category}[/bold]\n")

    check_fn = {
        "storage": checks.check_storage,
        "sqlite_vec": checks.check_sqlite_vec,
        "credentials": checks.check_credentials,
        "telegram": checks.check_telegram,
        "reminders": checks.check_reminders,
        "schedules": checks.check_schedules,
        "organizer": checks.check_organizer,
    }.get(category)

    if not check_fn:
        console.print(f"  [red]Unknown category: {category}[/red]")
        console.print(f"  Available: storage, sqlite_vec, credentials, telegram, reminders, schedules, organizer")
        return

    if category == "server":
        from mycelos.cli.serve_cmd import is_gateway_running, DEFAULT_PORT
        gateway_url = f"http://localhost:{DEFAULT_PORT}" if is_gateway_running() else None
        result = checks.check_server(gateway_url)
    else:
        result = check_fn(app)

    icon = STATUS_ICONS.get(result["status"], "[dim]?[/dim]")
    console.print(f"  {icon}  {result['details']}")

    if fix and result["status"] in ("warning", "error"):
        _auto_fix(app, [result])

    console.print()


def _detect_coding_tools() -> list[dict[str, str]]:
    """Detect installed AI coding tools."""
    import shutil
    tools = []
    if shutil.which("claude"):
        tools.append({"name": "Claude Code", "cmd": "claude"})
    if shutil.which("codex"):
        tools.append({"name": "Codex", "cmd": "codex"})
    return tools


def _run_via_coding_tool(tool_cmd: str, question: str, data_dir: Path) -> None:
    """Delegate diagnosis to an external AI coding tool."""
    import subprocess

    prompt = (
        f"Read AGENT.md for full system documentation. Then diagnose this problem:\n\n"
        f"{question}\n\n"
        f"Check the SQLite database at {data_dir}/mycelos.db — use the queries from "
        f"AGENT.md 'Debugging & Diagnostics' section. Look at audit_events, "
        f"knowledge_notes, config_generations, and scheduled_tasks tables.\n\n"
        f"Explain the root cause and how to fix it."
    )

    cmd = [tool_cmd, "-p", prompt, "--allowedTools", "Read,Grep,Glob,Bash"]
    console.print(f"\n[bold]Running {tool_cmd}[/bold] — this may take a few minutes.")
    console.print(f"[dim]The analysis runs in {tool_cmd}'s own interface below.[/dim]\n")
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        console.print(f"[red]{tool_cmd} not found. Falling back to Mycelos LLM.[/red]")
        return
    except KeyboardInterrupt:
        console.print(f"\n[dim]Cancelled.[/dim]")


def _run_why(app, question: str) -> None:
    """LLM-powered diagnosis — offers external tools if available."""
    coding_tools = _detect_coding_tools()

    if coding_tools:
        tool_names = ", ".join(t["name"] for t in coding_tools)
        console.print(f"\n[bold]Mycelos Doctor[/bold] — Analyzing: \"{question}\"\n")
        console.print(f"  [dim]AI coding tool detected: {tool_names}[/dim]")
        console.print(f"  [1] Use {coding_tools[0]['name']} [dim](uses your existing subscription)[/dim]")
        console.print(f"  [2] Use Mycelos LLM [dim](uses your configured API key)[/dim]")

        choice = click.prompt("  Choose", type=click.IntRange(1, 2), default=1)
        if choice == 1:
            _run_via_coding_tool(coding_tools[0]["cmd"], question, app.data_dir)
            return

    # Mycelos LLM diagnosis
    from mycelos.doctor.agent import DoctorAgent

    console.print(f"\n[bold]Mycelos Doctor[/bold] — Analyzing: \"{question}\"\n")

    with console.status("[dim]Thinking...[/dim]"):
        agent = DoctorAgent(app)
        diagnosis = agent.diagnose(question)

    console.print(diagnosis)
    console.print()


def _auto_fix(app, results: list[dict]) -> None:
    """Attempt auto-fixes for issues found."""
    console.print("\n  [bold]Auto-fix:[/bold]")

    for r in results:
        if r["status"] not in ("warning", "error"):
            continue

        if r["category"] == "reminders" and "overdue" in r.get("details", ""):
            console.print("  → Sending overdue reminders + clearing flags...")
            try:
                from mycelos.knowledge.reminder import ReminderService
                rs = ReminderService(app)
                result = rs.check_and_notify()
                console.print(f"    [green]Sent {result['notifications_sent']} notification(s), cleared {result['tasks_found']} reminder flags[/green]")
            except Exception as e:
                console.print(f"    [red]Failed: {e}[/red]")

        elif r["category"] == "server" and "not reachable" in r.get("details", ""):
            console.print("  → Start server with: [bold]mycelos serve[/bold]")
