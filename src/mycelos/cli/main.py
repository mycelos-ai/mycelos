"""Mycelos CLI entry point."""

import os

import click

from mycelos import __version__
from mycelos.cli.chat_cmd import chat_cmd
from mycelos.cli.config_cmd import config_group
from mycelos.cli.connector_cmd import connector_cmd
from mycelos.cli.demo_cmd import demo_cmd
from mycelos.cli.init_cmd import init_cmd
from mycelos.cli.schedule_cmd import schedule_cmd
from mycelos.cli.model_cmd import model_group
from mycelos.cli.serve_cmd import serve_cmd
from mycelos.cli.sessions_cmd import sessions_cmd
from mycelos.cli.test_cmd import test_cmd
from mycelos.cli.db_cmd import db_cmd
from mycelos.cli.credential_cmd import credential_cmd
from mycelos.cli.reset_cmd import reset_cmd
from mycelos.cli.stop_cmd import stop_cmd
from mycelos.cli.doctor_cmd import doctor_cmd
from mycelos.i18n import set_language


@click.group()
@click.version_option(version=__version__, prog_name="Mycelos")
@click.option(
    "--lang",
    type=click.Choice(["de", "en"]),
    default=None,
    help="CLI language.",
)
@click.pass_context
def cli(ctx: click.Context, lang: str | None):
    """Mycelos — a security-first agent operating system."""
    selected = lang or os.environ.get("MYCELOS_LANG") or "en"
    set_language(selected)
    ctx.ensure_object(dict)
    ctx.obj["lang"] = selected


cli.add_command(init_cmd, "init")
cli.add_command(config_group, "config")
cli.add_command(chat_cmd, "chat")
cli.add_command(model_group, "model")
cli.add_command(serve_cmd, "serve")
cli.add_command(connector_cmd, "connector")
cli.add_command(schedule_cmd, "schedule")
cli.add_command(sessions_cmd, "sessions")
cli.add_command(demo_cmd, "demo")
cli.add_command(test_cmd, "test")
cli.add_command(db_cmd, "db")
cli.add_command(credential_cmd, "credential")
cli.add_command(reset_cmd, "reset")
cli.add_command(stop_cmd, "stop")
cli.add_command(doctor_cmd, "doctor")
