"""mycelos stop — stop the background gateway server."""

from __future__ import annotations

import click
from rich.console import Console

from mycelos.i18n import t

console = Console()


@click.command()
@click.option("--port", type=int, default=9100, help="Gateway port.")
def stop_cmd(port: int) -> None:
    """Stop the Mycelos Gateway if running."""
    from mycelos.cli.serve_cmd import is_gateway_running

    if not is_gateway_running(port):
        console.print("[dim]Server is not running.[/dim]")
        return

    try:
        import httpx
        resp = httpx.post(f"http://localhost:{port}/api/shutdown", timeout=5)
        if resp.status_code == 200:
            console.print("[green]Server stopped.[/green]")
            return
    except Exception:
        pass

    # Fallback: find and kill the process
    try:
        import subprocess
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True,
        )
        pids = result.stdout.strip().split("\n")
        if pids and pids[0]:
            import signal
            for pid in pids:
                try:
                    import os
                    os.kill(int(pid.strip()), signal.SIGTERM)
                except (ValueError, ProcessLookupError):
                    pass
            console.print(f"[green]Server stopped (killed process on port {port}).[/green]")
        else:
            console.print("[yellow]Could not find server process.[/yellow]")
    except Exception as e:
        console.print(f"[red]Failed to stop server: {e}[/red]")
