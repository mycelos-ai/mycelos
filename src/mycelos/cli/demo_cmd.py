"""Mycelos Demo — fast, non-interactive walkthrough of Mycelos's key features.

No API keys required. All output is simulated.
Supports German and English via --lang flag.
Use --fast to skip all sleep timers.
"""

from __future__ import annotations

import time

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


console = Console()
_FAST = False


# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------

_TEXTS: dict[str, dict[str, str]] = {
    "welcome_title": {"de": "Willkommen bei Mycelos!", "en": "Welcome to Mycelos!"},
    "welcome_sub": {
        "de": "Dein persoenlicher KI-Assistent mit Enterprise-Sicherheit.",
        "en": "Your personal AI assistant with enterprise-grade security.",
    },
    # Value props per section
    "init_value": {
        "de": "Ein Key, ein Befehl — in 2 Minuten startklar. Wir erkennen deinen Provider automatisch.",
        "en": "One key, one command — ready in 2 minutes. We auto-detect your provider.",
    },
    "init_api_prompt": {"de": "  API-Schluessel eingeben: ", "en": "  Paste your API key: "},
    "init_detected": {
        "de": "Anthropic erkannt — Claude Opus, Sonnet, Haiku konfiguriert",
        "en": "Anthropic detected — Claude Opus, Sonnet, Haiku configured",
    },
    "init_verified": {"de": "Verbindung verifiziert", "en": "Connection verified"},
    "init_ready": {"de": "Mycelos ist bereit!", "en": "Mycelos is ready!"},
    "security_value": {
        "de": "Wir isolieren deine Credentials vom LLM-Layer — durch einen eigenen Sicherheitsprozess.",
        "en": "We isolate your credentials from the LLM layer — through a dedicated security process.",
    },
    "security_note": {
        "de": "Credentials verlassen den Proxy niemals — kein Agent sieht sie je.",
        "en": "Credentials never leave the proxy — no agent ever sees them.",
    },
    "rollback_value": {
        "de": "Jede Aenderung an deinem System ist ein Snapshot. Etwas kaputt? Ein Befehl, alles zurueck.",
        "en": "Every change to your system is a snapshot. Something broke? One command, everything back.",
    },
    "kb_value": {
        "de": "Dein persoenliches Gedaechtnis. Ideen, Aufgaben, Entscheidungen — alles durchsuchbar, alles verlinkt.",
        "en": "Your personal memory. Ideas, tasks, decisions — all searchable, all linked.",
    },
    "channel_value": {
        "de": "Ueberall erreichbar — vom Handy, Terminal oder Browser. Mit Sprachnachrichten.",
        "en": "Reachable everywhere — from phone, terminal, or browser. With voice messages.",
    },
    "workflow_value": {
        "de": "Wiederkehrende Aufgaben werden automatisch erkannt und als Workflows vorgeschlagen.",
        "en": "Recurring tasks are automatically detected and suggested as workflows.",
    },
    "permission_value": {
        "de": "Du entscheidest — Agent fuer Agent, Zugriff fuer Zugriff. Volle Kontrolle.",
        "en": "You decide — agent by agent, access by access. Full control.",
    },
    "gamification_value": {
        "de": "Das System waechst mit dir. Smarte Tipps helfen dir, alles zu entdecken.",
        "en": "The system grows with you. Smart hints help you discover everything.",
    },
    "kb_user_1": {
        "de": 'Du: "Geniale Idee — eine App die Rezepte aus Kuehlschrank-Fotos generiert!"',
        "en": 'You: "Great idea — an app that generates recipes from fridge photos!"',
    },
    "kb_saved": {
        "de": "Notiz erstellt: notes/rezept-app-idee.md",
        "en": "Note created: notes/recipe-app-idea.md",
    },
    "kb_tagged": {
        "de": "Getaggt: #projektidee #app #ki",
        "en": "Tagged: #project-idea #app #ai",
    },
    "kb_user_2": {
        "de": 'Du: "Was fuer Projektideen hatte ich nochmal?"',
        "en": 'You: "What project ideas did I have again?"',
    },
    "kb_found": {
        "de": 'Gefunden: "Rezept-App aus Kuehlschrank-Fotos" (Aehnlichkeit: 0.94)',
        "en": 'Found: "Recipe app from fridge photos" (similarity: 0.94)',
    },
    "kb_answer": {
        "de": "Mycelos findet deine Idee sofort — auch Wochen spaeter",
        "en": "Mycelos finds your idea instantly — even weeks later",
    },
    "voice_flow": {
        "de": "Sprachnachricht → Whisper API → Text → Chat",
        "en": "Voice Message → Whisper API → Text → Chat",
    },
    "file_flow": {
        "de": "PDF-Upload → Textextraktion → LLM-Analyse → KB-Notiz",
        "en": "PDF Upload → Text Extraction → LLM Analysis → KB Note",
    },
    "proxy_note": {
        "de": "Alles ueber SecurityProxy — Credentials niemals exponiert",
        "en": "All through SecurityProxy — credentials never exposed",
    },
    "workflow_match": {
        "de": '"Ideen fuer mein Projekt brainstormen"',
        "en": '"Brainstorm ideas for my project"',
    },
    "workflow_planner": {
        "de": "Planner findet Workflow → brainstorming-interview",
        "en": "Planner matches → brainstorming-interview workflow",
    },
    "workflow_result": {
        "de": "Strukturierte Fragen → KB-Notiz mit organisierten Ideen",
        "en": "Structured questions → KB note with organized ideas",
    },
    "perm_title": {"de": "Berechtigung erforderlich", "en": "Permission Required"},
    "perm_opt_1": {"de": "1. Erlauben fuer Creator (diese Sitzung)", "en": "1. Allow for Creator (this session)"},
    "perm_opt_2": {"de": "2. Erlauben fuer Creator (immer)", "en": "2. Allow for Creator (always)"},
    "perm_opt_3": {"de": "3. Erlauben fuer alle Agents (immer)", "en": "3. Allow for all agents (always)"},
    "perm_opt_4": {"de": "4. Ablehnen", "en": "4. Deny"},
    "perm_opt_5": {"de": "5. Niemals fuer Creator erlauben", "en": "5. Never allow for Creator"},
    "gamification_sub": {
        "de": "Dein Level waechst mit deiner Nutzung. Smarte Hinweise begleiten dich.",
        "en": "Your level grows with your usage. Smart hints guide you to the next level.",
    },
    "start_ready": {"de": "Bereit zum Ausprobieren?", "en": "Ready to try?"},
    "demo_complete": {"de": "Demo abgeschlossen!", "en": "Demo complete!"},
    "demo_cancelled": {"de": "Demo beendet.", "en": "Demo cancelled."},
}


def _t(key: str, lang: str) -> str:
    return _TEXTS.get(key, {}).get(lang, _TEXTS.get(key, {}).get("en", key))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pause(seconds: float = 1.0) -> None:
    if not _FAST:
        time.sleep(seconds)


def _wait_for_key() -> None:
    """Wait for user to press a key before continuing."""
    if _FAST:
        return
    try:
        console.print("\n  [dim]Press Enter to continue...[/dim]", end="")
        input()
    except (EOFError, KeyboardInterrupt):
        pass


def _typing_print(text: str, *, delay: float = 0.025) -> None:
    """Print with typing animation. Rich markup is rendered correctly."""
    if _FAST:
        console.print(text, highlight=False)
        return
    # Strip Rich markup for the typing animation, then print formatted
    import re
    plain = re.sub(r'\[/?[^\]]+\]', '', text)
    for ch in plain:
        import sys
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write('\r')
    sys.stdout.flush()
    # Now print the formatted version over it
    console.print(text, highlight=False)


def _step_done(text: str) -> None:
    console.print(f"  [green]✓[/green] {text}")


def _section_header(number: int, title: str) -> None:
    console.print()
    console.print(f"  [bold cyan]── {number}. {title} ──[/bold cyan]")
    console.print()


# ---------------------------------------------------------------------------
# Section 1 — Welcome
# ---------------------------------------------------------------------------

def _welcome(lang: str) -> None:
    console.print()
    title = Text("M Y C E L O S", style="bold white")
    subtitle = Text("Your AI grows with you.", style="italic")
    content = Text()
    content.append("\n")
    content.append("  M Y C E L O S\n", style="bold white")
    content.append("  Your AI grows with you.\n" if lang == "en" else "  Deine KI waechst mit dir.\n", style="italic")
    content.append("\n")
    console.print(Panel(content, border_style="bold cyan", width=50))
    console.print()
    console.print(f"  [bold white]{_t('welcome_title', lang)}[/bold white]")
    console.print(f"  [dim]{_t('welcome_sub', lang)}[/dim]")
    _pause(1.5)


# ---------------------------------------------------------------------------
# Section 2 — Init Simulation
# ---------------------------------------------------------------------------

def _init_simulation(lang: str) -> None:
    _section_header(2, "Setup" if lang == "en" else "Einrichtung")
    console.print(f"  [italic]{_t('init_value', lang)}[/italic]")
    console.print()
    console.print("  [bold]$ mycelos init[/bold]")
    console.print()
    _pause(0.5)
    console.print(_t("init_api_prompt", lang), end="")
    _typing_print("sk-ant-***...", delay=0.04)
    _pause(0.5)
    _step_done(_t("init_detected", lang))
    _pause(0.3)
    _step_done(_t("init_verified", lang))
    _pause(0.3)
    _step_done(f"[bold]{_t('init_ready', lang)}[/bold]")
    _pause(1.0)


# ---------------------------------------------------------------------------
# Section 3 — Security Architecture
# ---------------------------------------------------------------------------

def _security_architecture(lang: str) -> None:
    _section_header(3, "Security Architecture" if lang == "en" else "Sicherheitsarchitektur")
    console.print(f"  [italic]{_t('security_value', lang)}[/italic]")
    console.print()
    content = Text()
    content.append("\n  Gateway Process    ", style="bold white")
    content.append("SecurityProxy\n", style="bold yellow")
    content.append("  ├─ ChatService     ", style="white")
    content.append("├─ Master Key 🔒\n", style="yellow")
    content.append("  ├─ Policies        ", style="white")
    content.append("├─ Credentials\n", style="yellow")
    content.append("  ├─ Capabilities    ", style="white")
    content.append("├─ SSRF Filter\n", style="yellow")
    content.append("  └─ No secrets!     ", style="white")
    content.append("└─ All network I/O\n\n", style="yellow")
    content.append("  Unix Socket (no TCP, no network)\n", style="dim")
    content.append(f"  {_t('security_note', lang)}", style="dim italic")
    console.print(Panel(content, title="[bold red]🔒 Security Architecture[/bold red]",
                        border_style="red", padding=(0, 2)))
    _pause(2.0)


# ---------------------------------------------------------------------------
# Section 4 — Config Rollback
# ---------------------------------------------------------------------------

def _config_rollback(lang: str) -> None:
    _section_header(4, "Config Rollback")
    console.print(f"  [italic]{_t('rollback_value', lang)}[/italic]")
    console.print()

    if lang == "de":
        console.print("  [bold]$ mycelos config list[/bold]")
        _pause(0.5)
        console.print("  [dim]  Gen 5  (aktiv)  Telegram Bot hinzugefuegt[/dim]")
        console.print("  [dim]  Gen 4           OpenAI Key konfiguriert[/dim]")
        console.print("  [dim]  Gen 3           GitHub Connector[/dim]")
        console.print("  [dim]  Gen 2           Erster Agent erstellt[/dim]")
        console.print("  [dim]  Gen 1           Ersteinrichtung[/dim]")
        _pause(0.8)
        console.print()
        console.print("  [bold]$ mycelos config rollback 3[/bold]")
        _pause(0.5)
        console.print("  [green]✓ Zurueck zu Gen 3 — Telegram + OpenAI entfernt, GitHub bleibt[/green]")
        _pause(0.5)
        console.print()
        console.print("  [dim]Connectors, Agents, Policies, Workflows — alles versioniert.[/dim]")
        console.print("  [dim]Wie bei NixOS: aendere alles, rolle alles zurueck.[/dim]")
    else:
        console.print("  [bold]$ mycelos config list[/bold]")
        _pause(0.5)
        console.print("  [dim]  Gen 5  (active)  Added Telegram bot[/dim]")
        console.print("  [dim]  Gen 4            Configured OpenAI key[/dim]")
        console.print("  [dim]  Gen 3            GitHub connector[/dim]")
        console.print("  [dim]  Gen 2            First agent created[/dim]")
        console.print("  [dim]  Gen 1            Initial setup[/dim]")
        _pause(0.8)
        console.print()
        console.print("  [bold]$ mycelos config rollback 3[/bold]")
        _pause(0.5)
        console.print("  [green]✓ Rolled back to Gen 3 — Telegram + OpenAI removed, GitHub stays[/green]")
        _pause(0.5)
        console.print()
        console.print("  [dim]Connectors, agents, policies, workflows — all versioned.[/dim]")
        console.print("  [dim]Like NixOS: change anything, roll back everything.[/dim]")

    _pause(1.5)


# ---------------------------------------------------------------------------
# Section 5 — Knowledge Base
# ---------------------------------------------------------------------------

def _knowledge_base(lang: str) -> None:
    _section_header(5, "Knowledge Base" if lang == "en" else "Wissensdatenbank")
    console.print(f"  [italic]{_t('kb_value', lang)}[/italic]")
    console.print()
    _typing_print(f"  [bold cyan]{_t('kb_user_1', lang)}[/bold cyan]", delay=0.02)
    _pause(0.4)
    _step_done(_t("kb_saved", lang))
    _step_done(_t("kb_tagged", lang))
    _pause(0.7)
    console.print()
    _typing_print(f"  [bold cyan]{_t('kb_user_2', lang)}[/bold cyan]", delay=0.02)
    _pause(0.4)
    console.print(f"  [green]🔍[/green] {_t('kb_found', lang)}")
    console.print(f"  [dim]→ {_t('kb_answer', lang)}[/dim]")
    _pause(1.5)


# ---------------------------------------------------------------------------
# Section 5 — Multi-Channel
# ---------------------------------------------------------------------------

def _multi_channel(lang: str) -> None:
    _section_header(6, "Multi-Channel")
    console.print(f"  [italic]{_t('channel_value', lang)}[/italic]")
    console.print()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold", width=4)
    table.add_column(style="white")
    if lang == "de":
        table.add_row("📱", "[bold]Telegram[/bold]  — Chat + Sprache + Dateien")
        table.add_row("🖥️ ", "[bold]Terminal[/bold]  — Reichhaltiges CLI mit Tab-Vervollstaendigung")
        table.add_row("🌐", "[bold]Web[/bold]       — Browser-UI mit Sprachaufnahme")
    else:
        table.add_row("📱", "[bold]Telegram[/bold]  — Chat + Voice + Files")
        table.add_row("🖥️ ", "[bold]Terminal[/bold]  — Rich CLI with tab completion")
        table.add_row("🌐", "[bold]Web[/bold]       — Browser UI with voice recording")
    console.print(table)
    _pause(1.0)

    # MCP highlight within the same section
    console.print()
    if lang == "de":
        console.print("  [bold]Tausende Konnektoren[/bold] via Model Context Protocol (MCP):")
        console.print("  [dim]GitHub, Slack, Notion, Google Drive, Filesystem, Datenbanken, ...[/dim]")
        console.print("  [dim]Jeder MCP-Server funktioniert: /connector add-custom <name> <command>[/dim]")
    else:
        console.print("  [bold]Thousands of connectors[/bold] via Model Context Protocol (MCP):")
        console.print("  [dim]GitHub, Slack, Notion, Google Drive, Filesystem, databases, ...[/dim]")
        console.print("  [dim]Any MCP server works: /connector add-custom <name> <command>[/dim]")
    _pause(1.0)


# ---------------------------------------------------------------------------
# Section 6 — Voice & Files
# ---------------------------------------------------------------------------

def _voice_and_files(lang: str) -> None:
    _section_header(7, "Voice & Files" if lang == "en" else "Sprache & Dateien")
    console.print(f"  🎤 {_t('voice_flow', lang)}")
    _pause(0.4)
    console.print(f"  📄 {_t('file_flow', lang)}")
    _pause(0.4)
    console.print(f"  [bold red]🔒[/bold red] [dim]{_t('proxy_note', lang)}[/dim]")
    _pause(1.5)


# ---------------------------------------------------------------------------
# Section 7 — Workflows
# ---------------------------------------------------------------------------

def _workflows(lang: str) -> None:
    _section_header(8, "Workflows")
    console.print(f"  [italic]{_t('workflow_value', lang)}[/italic]")
    console.print()
    label = "Built-in Workflows:" if lang == "en" else "Eingebaute Workflows:"
    console.print(f"  [bold]{label}[/bold]")
    console.print()
    items = [
        ("📋", "brainstorming-interview", "Structured idea collection", "Strukturierte Ideensammlung"),
        ("🔍", "research-summary", "Web search + summarize", "Websuche + Zusammenfassung"),
        ("☀️ ", "daily-briefing", "Tasks + news overview", "Aufgaben + Nachrichten"),
        ("🎓", "onboarding", "Guided first-time setup", "Begleitete Ersteinrichtung"),
    ]
    for icon, name, en_desc, de_desc in items:
        desc = en_desc if lang == "en" else de_desc
        console.print(f"  {icon} [bold cyan]{name}[/bold cyan] [dim]— {desc}[/dim]")
        _pause(0.3)
    console.print()
    _typing_print(f"  [bold cyan]{_t('workflow_match', lang)}[/bold cyan]", delay=0.02)
    _pause(0.4)
    console.print(f"  [dim]→ {_t('workflow_planner', lang)}[/dim]")
    console.print(f"  [dim]→ {_t('workflow_result', lang)}[/dim]")
    _pause(1.5)


# ---------------------------------------------------------------------------
# Section 8 — Permission System
# ---------------------------------------------------------------------------

def _permission_system(lang: str) -> None:
    _section_header(9, "Permission System" if lang == "en" else "Berechtigungssystem")
    console.print(f"  [italic]{_t('permission_value', lang)}[/italic]")
    console.print()
    opts = [_t(f"perm_opt_{i}", lang) for i in range(1, 6)]
    content = Text()
    content.append("\n  [Creator-Agent] filesystem_read\n", style="bold yellow")
    content.append("  /Users/stefan/Documents\n\n", style="dim")
    for i, opt in enumerate(opts):
        content.append(f"  {opt}\n", style="bold white" if i == 0 else "white")
    title_label = _t("perm_title", lang)
    console.print(Panel(content, title=f"[bold yellow]⚠️  {title_label}[/bold yellow]",
                        border_style="yellow", padding=(0, 1)))
    _pause(1.5)


# ---------------------------------------------------------------------------
# Section 9 — Gamification
# ---------------------------------------------------------------------------

def _gamification(lang: str) -> None:
    _section_header(10, "Your Progress" if lang == "en" else "Dein Fortschritt")
    console.print(f"  [italic]{_t('gamification_value', lang)}[/italic]")
    console.print()
    levels = Text("  ")
    levels.append("🌱 Newcomer", style="dim")
    levels.append("  →  ", style="white")
    levels.append("🔍 Explorer", style="white")
    levels.append("  →  ", style="white")
    levels.append("🔧 Builder", style="white")
    levels.append("  →  ", style="white")
    levels.append("🏗️  Architect", style="bold white")
    levels.append("  →  ", style="white")
    levels.append("⚡ Power User", style="bold yellow")
    levels.append("  →  ", style="white")
    levels.append("🧙 Guru", style="bold magenta")
    console.print(levels)
    console.print()
    console.print(f"  [dim]{_t('gamification_sub', lang)}[/dim]")
    _pause(1.5)


# ---------------------------------------------------------------------------
# Section 10 — Getting Started
# ---------------------------------------------------------------------------

def _getting_started(lang: str) -> None:
    _section_header(11, "Getting Started" if lang == "en" else "Loslegen")

    if lang == "de":
        console.print("  [bold white]Mycelos ist kostenlos und Open Source.[/bold white]")
        console.print()
        console.print("  [bold cyan]pip install mycelos[/bold cyan]   [dim]— Mycelos installieren[/dim]")
        _pause(0.3)
        console.print("  [bold cyan]mycelos init[/bold cyan]         [dim]— System einrichten[/dim]")
        _pause(0.3)
        console.print("  [bold cyan]mycelos chat[/bold cyan]         [dim]— Loslegen[/dim]")
        console.print()
        console.print("  [bold blue]https://github.com/mycelos-ai/mycelos[/bold blue]")
        console.print()
        console.print("  [bold]Hilf uns, Mycelos besser zu machen:[/bold]")
        console.print("  ⭐ Gib uns einen Star auf GitHub")
        console.print("  🐛 Melde Bugs oder schlage Features vor")
        console.print("  🤝 Beitraege sind willkommen — PRs, Docs, Ideen")
    else:
        console.print("  [bold white]Mycelos is free and open source.[/bold white]")
        console.print()
        console.print("  [bold cyan]pip install mycelos[/bold cyan]   [dim]— Install Mycelos[/dim]")
        _pause(0.3)
        console.print("  [bold cyan]mycelos init[/bold cyan]         [dim]— Initialize[/dim]")
        _pause(0.3)
        console.print("  [bold cyan]mycelos serve[/bold cyan]        [dim]— Start server (CLI + Web UI)[/dim]")
        console.print()
        console.print("  [bold blue]https://github.com/mycelos-ai/mycelos[/bold blue]")
        console.print()
        console.print("  [bold]Help us make Mycelos a success:[/bold]")
        console.print("  ⭐ Star us on GitHub")
        console.print("  🐛 Report bugs or suggest features")
        console.print("  🤝 Contributions welcome — PRs, docs, ideas")

    _pause(1.0)


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

@click.command("demo")
@click.option("--fast", is_flag=True, default=False, help="Skip sleep timers.")
@click.option("--lang", type=click.Choice(["de", "en"]), default=None,
              help="Demo language (de/en).")
def demo_cmd(fast: bool, lang: str | None) -> None:
    """Fast non-interactive demo — Mycelos in 60 seconds, no API key needed."""
    global _FAST
    if fast:
        _FAST = True

    if lang is None:
        console.print("\n  [bold]Sprache / Language:[/bold]\n")
        console.print("    (1) Deutsch")
        console.print("    (2) English")
        console.print()
        choice = click.prompt("  ", type=click.IntRange(1, 2), default=2)
        lang = "de" if choice == 1 else "en"

    try:
        _welcome(lang)
        _wait_for_key()
        _init_simulation(lang)
        _wait_for_key()
        _security_architecture(lang)
        _wait_for_key()
        _config_rollback(lang)
        _wait_for_key()
        _knowledge_base(lang)
        _wait_for_key()
        _multi_channel(lang)
        _voice_and_files(lang)
        _wait_for_key()
        _workflows(lang)
        _wait_for_key()
        _permission_system(lang)
        _wait_for_key()
        _gamification(lang)
        _wait_for_key()
        _getting_started(lang)
        console.print()
        console.print(f"  [bold green]{_t('demo_complete', lang)}[/bold green]")
        console.print()
    except KeyboardInterrupt:
        console.print(f"\n  [dim]{_t('demo_cancelled', lang)}[/dim]")
