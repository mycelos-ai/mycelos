"""CLI Widget Renderer — renders widgets as Rich console output."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table as RichTable
from rich.text import Text

from mycelos.i18n import t
from mycelos.widgets.renderer import dispatch_render
from mycelos.widgets.types import (
    ChoiceBox,
    CodeBlock,
    Compose,
    Confirm,
    ImageBlock,
    ProgressBar,
    StatusCard,
    Table,
    TextBlock,
)

# StatusCard style → Rich border color
_STYLE_COLORS: dict[str, str] = {
    "info": "blue",
    "success": "green",
    "warning": "yellow",
    "error": "red",
}


class CLIRenderer:
    """Renders Widget IR to Rich console output."""

    def __init__(self, console: Console) -> None:
        self.console = console

    def render(self, widget: object) -> None:
        """Render any widget to the console."""
        dispatch_render(self, widget)

    def render_text_block(self, widget: TextBlock) -> None:
        style = {"bold": "bold", "italic": "italic"}.get(widget.weight, "")
        self.console.print(Text(widget.text, style=style))

    def render_table(self, widget: Table) -> None:
        table = RichTable()
        for header in widget.headers:
            table.add_column(header)
        for row in widget.rows:
            table.add_row(*row)
        self.console.print(table)

    def render_choice_box(self, widget: ChoiceBox) -> None:
        self.console.print(Text(widget.prompt, style="bold"))
        for i, option in enumerate(widget.options, 1):
            self.console.print(f"  [cyan][{i}][/cyan] {option.label}")

    def render_status_card(self, widget: StatusCard) -> None:
        color = _STYLE_COLORS.get(widget.style, "blue")
        lines: list[str] = []
        for key, value in widget.facts.items():
            lines.append(f"[bold]{key}:[/bold] {value}")
        content = "\n".join(lines) if lines else ""
        self.console.print(Panel(content, title=widget.title, border_style=color))

    def render_progress_bar(self, widget: ProgressBar) -> None:
        pct = widget.percentage
        filled = int(pct / 5)  # 20 chars wide
        bar = "█" * filled + "░" * (20 - filled)
        self.console.print(f"{widget.label}: {bar} {pct:.0f}%")

    def render_code_block(self, widget: CodeBlock) -> None:
        syntax = Syntax(widget.code, widget.language, theme="monokai")
        self.console.print(syntax)

    def render_confirm(self, widget: Confirm) -> None:
        style = "bold red" if widget.danger else "bold"
        self.console.print(Text(f"{widget.prompt} {t('widgets.confirm_yes_no')}", style=style))

    def render_image_block(self, widget: ImageBlock) -> None:
        text = t("widgets.image_alt", alt=widget.alt)
        if widget.caption:
            text += f"\n{widget.caption}"
        self.console.print(Text(text, style="dim"))

    def render_compose(self, widget: Compose) -> None:
        for child in widget.children:
            self.render(child)
