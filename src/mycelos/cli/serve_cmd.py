"""mycelos serve — start the Mycelos Gateway (HTTP API)."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from mycelos.i18n import t

console = Console()

_LOGO_ASCII = r"""
    ╔════════════════════════════════╗
    ║        ┌─────┐  ┌─────┐        ║
    ║     ┌──┤     ├──┤     ├──┐     ║
    ║   ┌─┤  └──┬──┘  └──┬──┘  ├─┐   ║
    ║   │ └─────┤  ╔══╗  ├─────┘ │   ║
    ║   │ ┌─────┤  ║  ║  ├─────┐ │   ║
    ║   └─┤  ┌──┴──╚══╝──┴──┐  ├─┘   ║
    ║     └──┤   MYCELOS     ├──┘    ║
    ║        └───────────────┘       ║
    ╚════════════════════════════════╝
"""

DEFAULT_PORT = 9100
DEFAULT_HOST = "127.0.0.1"


@click.command()
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
@click.option("--port", type=int, default=DEFAULT_PORT, show_default=True, help="Port to listen on.")
@click.option("--host", type=str, default=DEFAULT_HOST, show_default=True, help="Host to bind to.")
@click.option("--password", type=str, default=None, help="Require Basic Auth password (recommended with --host 0.0.0.0).")
@click.option("--debug", is_flag=True, help="Enable debug logging (intents, models, tokens, events).")
@click.option("--status", "show_status", is_flag=True, help="Check if gateway is running.")
@click.option("--no-scheduler", is_flag=True, help="Disable background scheduler (Huey).")
@click.option(
    "--role",
    type=click.Choice(["all", "gateway", "proxy"]),
    default="all",
    help=(
        "Container/process role. 'all' (default) runs the gateway with an "
        "in-process SecurityProxy; 'gateway' uses an external proxy from "
        "MYCELOS_PROXY_URL; 'proxy' runs ONLY the SecurityProxy on TCP."
    ),
)
@click.option("--proxy-host", default="127.0.0.1", help="Proxy bind host (role=proxy only)")
@click.option("--proxy-port", default=9110, type=int, help="Proxy bind port (role=proxy only)")
@click.option("--dry-run", is_flag=True, help="Validate configuration and exit.")
def serve_cmd(data_dir: Path, port: int, host: str, password: str | None, debug: bool, show_status: bool, no_scheduler: bool, role: str, proxy_host: str, proxy_port: int, dry_run: bool) -> None:
    """Start the Mycelos Gateway (HTTP API).

    The gateway exposes the chat, config, and health endpoints
    over HTTP with SSE streaming. Channels (Slack, Telegram, Web UI)
    connect to the gateway instead of running chat directly.
    """
    # --- Proxy role: run only the SecurityProxy on TCP ---
    if role == "proxy":
        import os as _os
        from pathlib import Path as _Path
        key_path = _Path(data_dir) / ".master_key"
        if not key_path.exists():
            click.echo(
                f"Error: .master_key not found in {data_dir}. "
                "The gateway container or install script must create it first.",
                err=True,
            )
            raise click.exceptions.Exit(code=2)
        token = _os.environ.get("MYCELOS_PROXY_TOKEN", "").strip()
        if not token:
            click.echo(
                "Error: MYCELOS_PROXY_TOKEN must be set. "
                "Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'",
                err=True,
            )
            raise click.exceptions.Exit(code=2)
        if dry_run:
            click.echo(f"Proxy ready (dry-run): would bind {proxy_host}:{proxy_port}")
            return
        _os.environ["MYCELOS_MASTER_KEY"] = key_path.read_text().strip()
        _os.environ["MYCELOS_DB_PATH"] = str(_Path(data_dir) / "mycelos.db")
        import uvicorn
        from mycelos.security.proxy_server import create_proxy_app
        uvicorn.run(create_proxy_app(), host=proxy_host, port=proxy_port, log_level="warning")
        return

    # --- Gateway role without external proxy URL: warn, fall back to in-process ---
    if role == "gateway":
        import os as _os
        if not _os.environ.get("MYCELOS_PROXY_URL"):
            click.echo(
                "Note: --role gateway without MYCELOS_PROXY_URL falls back to "
                "an in-process proxy (same as --role all)."
            )

    # --- Dry run: validate config + exit before binding ---
    if dry_run:
        click.echo(f"Gateway ready (dry-run): would bind {host}:{port}")
        return

    if show_status:
        _show_status(port)
        return

    # Fall back to MYCELOS_PASSWORD env var if --password flag not set
    if not password:
        import os
        password = os.environ.get("MYCELOS_PASSWORD") or None

    # Verify initialized
    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        console.print(
            f"[red]{t('common.error')}:[/red] {t('common.not_initialized', path=data_dir)}"
        )
        raise SystemExit(1)

    # ASCII logo banner
    console.print(f"\n[cyan]{_LOGO_ASCII}[/cyan]")
    console.print(f"[bold green]{t('serve.title')}[/bold green]")
    console.print(f"  {t('serve.data', path=data_dir)}")
    console.print(f"  {t('serve.url', host=host, port=port)}")
    console.print(f"  {t('serve.docs', host=host, port=port)}")
    if debug:
        console.print(f"  {t('serve.debug_on')}")
    if no_scheduler:
        console.print(f"  {t('serve.scheduler_off')}")
    else:
        console.print(f"  {t('serve.scheduler_on')}")

    # Password protection
    if password:
        console.print(f"  [green]Auth: Basic Auth enabled (password protected)[/green]")
    elif host not in ("127.0.0.1", "::1", "localhost"):
        console.print(f"  [yellow]WARNING: No password set — anyone on the network can access Mycelos![/yellow]")
        console.print(f"  [yellow]  Add --password <secret> for Basic Auth protection.[/yellow]")

    console.print()
    console.print(f"  {t('serve.endpoints')}")
    console.print(f"    {t('serve.endpoint_chat')}")
    console.print(f"    {t('serve.endpoint_health')}")
    console.print(f"    {t('serve.endpoint_config')}")
    console.print(f"    {t('serve.endpoint_sessions')}")
    console.print()
    console.print(f"[dim]{t('serve.press_ctrl_c')}[/dim]\n")

    import uvicorn
    from mycelos.gateway.server import create_app

    app = create_app(data_dir, debug=debug, no_scheduler=no_scheduler, host=host, password=password)
    log_level = "debug" if debug else "info"
    uvicorn.run(app, host=host, port=port, log_level=log_level)


def _show_status(port: int) -> None:
    """Check if gateway is running."""
    if is_gateway_running(port):
        console.print(f"[green]{t('serve.running', port=port)}[/green]")
        try:
            import httpx
            resp = httpx.get(f"http://localhost:{port}/api/health", timeout=2)
            data = resp.json()
            console.print(f"  {t('serve.uptime', seconds=data.get('uptime_seconds', '?'))}")
            console.print(f"  {t('serve.generation', id=data.get('generation_id', '?'))}")
        except Exception:
            pass
    else:
        console.print(f"[yellow]{t('serve.not_running', port=port)}[/yellow]")
        console.print(t("serve.start_with"))


def is_gateway_running(port: int = DEFAULT_PORT) -> bool:
    """Check if the gateway is reachable."""
    try:
        import httpx
        resp = httpx.get(f"http://localhost:{port}/api/health", timeout=1)
        return resp.status_code == 200
    except Exception:
        return False
