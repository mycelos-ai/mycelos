"""mycelos db — quick database inspection for debugging."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from mycelos.app import App

console = Console()


@click.group("db")
def db_cmd() -> None:
    """Inspect Mycelos database (debugging)."""
    pass


def _get_app(data_dir: Path) -> App:
    if not os.environ.get("MYCELOS_MASTER_KEY"):
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()
    return App(data_dir)


@db_cmd.command("connectors")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def connectors_cmd(data_dir: Path) -> None:
    """Show all connectors with capabilities."""
    app = _get_app(data_dir)
    rows = app.storage.fetchall(
        """SELECT c.id, c.name, c.connector_type, c.status, c.description,
                  GROUP_CONCAT(cc.capability, ', ') as capabilities
           FROM connectors c
           LEFT JOIN connector_capabilities cc ON c.id = cc.connector_id
           GROUP BY c.id ORDER BY c.id"""
    )
    table = Table(title="Connectors")
    table.add_column("ID", style="bold")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Capabilities")
    table.add_column("Description", style="dim")
    for r in rows:
        table.add_row(r["id"], r["name"], r["connector_type"],
                       r["status"], r["capabilities"] or "", r["description"] or "")
    console.print(table)


@db_cmd.command("agents")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def agents_cmd(data_dir: Path) -> None:
    """Show all agents with capabilities."""
    app = _get_app(data_dir)
    rows = app.storage.fetchall(
        """SELECT a.id, a.name, a.agent_type, a.status, a.created_by,
                  GROUP_CONCAT(ac.capability, ', ') as capabilities
           FROM agents a
           LEFT JOIN agent_capabilities ac ON a.id = ac.agent_id
           GROUP BY a.id ORDER BY a.id"""
    )
    table = Table(title="Agents")
    table.add_column("ID", style="bold")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Capabilities")
    table.add_column("Created By", style="dim")
    for r in rows:
        table.add_row(r["id"], r["name"], r["agent_type"],
                       r["status"], r["capabilities"] or "", r["created_by"] or "")
    console.print(table)


@db_cmd.command("policies")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def policies_cmd(data_dir: Path) -> None:
    """Show all policies."""
    app = _get_app(data_dir)
    rows = app.storage.fetchall("SELECT * FROM policies ORDER BY user_id, resource")
    table = Table(title="Policies")
    table.add_column("User", style="bold")
    table.add_column("Agent")
    table.add_column("Resource")
    table.add_column("Decision")
    for r in rows:
        table.add_row(r["user_id"], r["agent_id"] or "*", r["resource"], r["decision"])
    console.print(table)


@db_cmd.command("channels")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def channels_cmd(data_dir: Path) -> None:
    """Show channel configurations."""
    app = _get_app(data_dir)
    rows = app.storage.fetchall("SELECT * FROM channels ORDER BY id")
    table = Table(title="Channels")
    table.add_column("ID", style="bold")
    table.add_column("Type")
    table.add_column("Mode")
    table.add_column("Status")
    table.add_column("Allowed Users")
    table.add_column("Config", style="dim")
    for r in rows:
        allowed = r["allowed_users"]
        if isinstance(allowed, str):
            allowed = allowed[:50]
        config = r["config"]
        if isinstance(config, str) and len(config) > 50:
            config = config[:50] + "..."
        table.add_row(r["id"], r["channel_type"], r["mode"],
                       r["status"], str(allowed), str(config))
    console.print(table)


@db_cmd.command("credentials")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def credentials_cmd(data_dir: Path) -> None:
    """Show stored credential services (not the secrets!)."""
    app = _get_app(data_dir)
    rows = app.storage.fetchall(
        "SELECT service, security_rotated, created_at FROM credentials ORDER BY service"
    )
    table = Table(title="Credentials (encrypted)")
    table.add_column("Service", style="bold")
    table.add_column("Rotated")
    table.add_column("Created")
    for r in rows:
        table.add_row(r["service"], str(r["security_rotated"]), r["created_at"] or "")
    console.print(table)


from mycelos.audit_patterns import (
    NOISY_EVENT_TYPES,
    SUSPICIOUS_EVENT_SUFFIXES,
    SUSPICIOUS_EVENT_TYPES as SUSPICIOUS_EVENT_PATTERNS,
)


def _parse_since(value: str | None) -> str | None:
    """Parse --since shorthand (1h, 24h, 7d, 30m) into an ISO UTC cutoff."""
    if not value:
        return None
    from datetime import datetime, timedelta, timezone
    import re as _re
    match = _re.match(r"^(\d+)([smhd])$", value.strip().lower())
    if not match:
        raise click.BadParameter(f"--since must look like '15m', '1h', '24h', '7d' (got {value!r})")
    amount = int(match.group(1))
    unit = match.group(2)
    delta = {
        "s": timedelta(seconds=amount),
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%fZ")


@db_cmd.command("audit")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
@click.option("--type", "event_type", default=None, help="Filter by event type (comma-separated for multiple)")
@click.option("--agent", "agent_id", default=None, help="Filter by agent_id")
@click.option("--since", default=None, help="Filter events newer than e.g. 30m, 1h, 24h, 7d")
@click.option("--suspicious", is_flag=True, help="Only show security-relevant events (tamper, blocks, denies, rotates, …)")
@click.option("--quiet", is_flag=True, help="Hide high-volume noise events (reminder.tick, scheduler.tick, llm.usage, session.heartbeat)")
@click.option("--limit", "max_rows", default=20, help="Max rows to show")
def audit_cmd(
    data_dir: Path,
    event_type: str | None,
    agent_id: str | None,
    since: str | None,
    suspicious: bool,
    quiet: bool,
    max_rows: int,
) -> None:
    """Show recent audit events with filters.

    Examples:
      mycelos db audit --suspicious --since 24h
      mycelos db audit --quiet --since 1h
      mycelos db audit --type tool.blocked,policy.denied --limit 50
      mycelos db audit --agent mycelos --since 1h
    """
    app = _get_app(data_dir)

    conditions: list[str] = []
    params: list = []

    if suspicious:
        placeholders = ",".join("?" * len(SUSPICIOUS_EVENT_PATTERNS))
        like_clauses = " OR ".join(["event_type LIKE ?"] * len(SUSPICIOUS_EVENT_SUFFIXES))
        conditions.append(
            f"(event_type IN ({placeholders}) OR {like_clauses})"
        )
        params.extend(SUSPICIOUS_EVENT_PATTERNS)
        params.extend(f"%{suffix}" for suffix in SUSPICIOUS_EVENT_SUFFIXES)

    if event_type:
        types = [t.strip() for t in event_type.split(",") if t.strip()]
        if types:
            placeholders = ",".join("?" * len(types))
            conditions.append(f"event_type IN ({placeholders})")
            params.extend(types)

    if agent_id:
        conditions.append("agent_id = ?")
        params.append(agent_id)

    if quiet:
        placeholders = ",".join("?" * len(NOISY_EVENT_TYPES))
        conditions.append(f"event_type NOT IN ({placeholders})")
        params.extend(NOISY_EVENT_TYPES)

    cutoff = _parse_since(since)
    if cutoff:
        conditions.append("created_at >= ?")
        params.append(cutoff)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(max_rows)
    rows = app.storage.fetchall(
        f"SELECT * FROM audit_events {where} ORDER BY created_at DESC LIMIT ?",
        tuple(params),
    )

    title = f"Audit Events (last {max_rows})"
    if suspicious:
        title = f"Suspicious Audit Events (last {max_rows})"

    table = Table(title=title)
    table.add_column("Time", style="dim")
    table.add_column("Type", style="bold")
    table.add_column("Agent")
    table.add_column("Details")
    for r in rows:
        details = r["details"] or ""
        if isinstance(details, str) and len(details) > 80:
            details = details[:80] + "..."
        table.add_row(
            (r["created_at"] or "")[-8:],  # just time part
            r["event_type"],
            r["agent_id"] or "",
            details,
        )
    if not rows:
        console.print(f"[dim]No matching events.[/dim]")
    else:
        console.print(table)


@db_cmd.command("memory")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def memory_cmd(data_dir: Path) -> None:
    """Show memory entries."""
    app = _get_app(data_dir)
    rows = app.storage.fetchall(
        "SELECT key, value, created_by, created_at FROM memory_entries ORDER BY key LIMIT 50"
    )
    table = Table(title="Memory Entries")
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_column("By", style="dim")
    for r in rows:
        value = r["value"] or ""
        if len(value) > 60:
            value = value[:60] + "..."
        table.add_row(r["key"], value, r["created_by"] or "")
    console.print(table)


@db_cmd.command("sql")
@click.argument("query")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def sql_cmd(query: str, data_dir: Path) -> None:
    """Run a raw SQL query (read-only)."""
    if not query.strip().upper().startswith("SELECT"):
        console.print("[red]Only SELECT queries allowed.[/red]")
        return
    app = _get_app(data_dir)
    try:
        rows = app.storage.fetchall(query)
        if not rows:
            console.print("[dim]No results.[/dim]")
            return
        table = Table()
        for col in rows[0].keys():
            table.add_column(col)
        for r in rows:
            table.add_row(*[str(v)[:100] if v is not None else "" for v in r.values()])
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@db_cmd.command("context")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def context_cmd(data_dir: Path) -> None:
    """Show what the LLM sees as system context (live)."""
    app = _get_app(data_dir)
    from mycelos.chat.context import build_context
    ctx = build_context(app)
    console.print(ctx)
