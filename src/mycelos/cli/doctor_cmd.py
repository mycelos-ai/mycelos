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
        "connectors": checks.check_connectors,
        "reminders": checks.check_reminders,
        "schedules": checks.check_schedules,
        "organizer": checks.check_organizer,
        "update": checks.check_update_available,
    }.get(category)

    if not check_fn:
        console.print(f"  [red]Unknown category: {category}[/red]")
        console.print(f"  Available: storage, sqlite_vec, credentials, telegram, connectors, reminders, schedules, organizer, update")
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


def _run_why(app, question: str) -> None:
    """Multi-turn diagnostic dialogue via the Doctor agent on the gateway.

    Opens an interactive REPL and pins the session to target_agent_id=doctor
    so every turn is handled by DoctorHandler (with diagnostic tools), not
    by Mycelos. The user keeps replying with new info until the symptom is
    resolved or they exit.
    """
    from mycelos.cli.serve_cmd import DEFAULT_PORT, is_gateway_running

    if not is_gateway_running():
        console.print(
            "\n[yellow]Gateway not running.[/yellow] "
            "Start it with [bold]mycelos serve[/bold] in another terminal, "
            "then retry [bold]mycelos doctor --why[/bold].\n"
        )
        return

    base_url = f"http://localhost:{DEFAULT_PORT}"
    console.print(f"\n[bold]Mycelos Doctor[/bold] — Diagnosing: \"{question}\"")
    console.print("[dim](type 'exit' or Ctrl-D to leave the diagnostic session)[/dim]\n")

    session_id = _doctor_chat_turn(base_url, question, session_id=None)
    if session_id is None:
        return

    while True:
        try:
            follow_up = click.prompt("  You", default="", show_default=False).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Doctor session ended.[/dim]\n")
            return
        if not follow_up:
            continue
        if follow_up.lower() in {"exit", "quit", "q"}:
            console.print("[dim]Doctor session ended.[/dim]\n")
            return
        _doctor_chat_turn(base_url, follow_up, session_id=session_id)


def _doctor_chat_turn(base_url: str, message: str, session_id: str | None) -> str | None:
    """Send one turn to the gateway pinned to the doctor agent.

    Returns the session_id (new on first turn, unchanged on follow-ups) or
    None if the request failed.
    """
    import httpx
    from rich.markdown import Markdown

    payload = {
        "message": message,
        "session_id": session_id,
        "channel": "terminal",
        "target_agent_id": "doctor",
    }

    try:
        with httpx.stream(
            "POST", f"{base_url}/api/chat", json=payload, timeout=120,
        ) as resp:
            current_event_type: str | None = None
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    current_event_type = line[7:].strip()
                elif line.startswith("data: ") and current_event_type:
                    import json as _json
                    try:
                        data = _json.loads(line[6:])
                    except Exception:
                        continue
                    if current_event_type == "session":
                        session_id = data.get("session_id", session_id)
                    elif current_event_type == "agent":
                        console.print(f"\n[bold magenta]{data.get('agent', 'Doctor')}>[/bold magenta]")
                    elif current_event_type in ("text", "system-response"):
                        console.print(Markdown(data.get("content", "")))
                    elif current_event_type == "error":
                        console.print(f"[red]{data.get('message', 'unknown error')}[/red]")
                    elif current_event_type == "done":
                        console.print()
                    current_event_type = None
    except httpx.ConnectError:
        console.print("[red]Gateway not reachable — start it with `mycelos serve`.[/red]")
        return None
    except Exception as exc:
        console.print(f"[red]Gateway error: {exc}[/red]")
        return session_id

    return session_id


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
