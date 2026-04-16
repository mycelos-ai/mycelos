"""Telegram Widget Renderer — Markdown text for Telegram Bot API."""

from __future__ import annotations

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

_STYLE_EMOJI: dict[str, str] = {
    "info": "ℹ️",
    "success": "✅",
    "warning": "⚠️",
    "error": "❌",
}


class TelegramRenderer:
    """Renders Widget IR to Telegram Markdown text."""

    def render(self, widget: object) -> str:
        return dispatch_render(self, widget)

    def render_text_block(self, widget: TextBlock) -> str:
        if widget.weight == "bold":
            return f"*{widget.text}*"
        if widget.weight == "italic":
            return f"_{widget.text}_"
        return widget.text

    def render_table(self, widget: Table) -> str:
        # Compute column widths
        cols = len(widget.headers)
        widths = [len(h) for h in widget.headers]
        for row in widget.rows:
            for i, cell in enumerate(row[:cols]):
                widths[i] = max(widths[i], len(cell))

        def _fmt_row(cells: list[str]) -> str:
            return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells[:cols]))

        lines = [_fmt_row(widget.headers)]
        lines.append("─" * sum(w + 2 for w in widths))
        for row in widget.rows:
            lines.append(_fmt_row(row))
        return "```\n" + "\n".join(lines) + "\n```"

    def render_choice_box(self, widget: ChoiceBox) -> str:
        lines = [f"*{widget.prompt}*"]
        for i, option in enumerate(widget.options, 1):
            lines.append(f"  {i}. {option.label}")
        return "\n".join(lines)

    def render_status_card(self, widget: StatusCard) -> str:
        emoji = _STYLE_EMOJI.get(widget.style, "ℹ️")
        lines = [f"{emoji} *{widget.title}*"]
        for key, value in widget.facts.items():
            lines.append(f"  {key}: {value}")
        return "\n".join(lines)

    def render_progress_bar(self, widget: ProgressBar) -> str:
        pct = widget.percentage
        filled = int(pct / 10)
        bar = "▓" * filled + "░" * (10 - filled)
        return f"{widget.label}: {bar} {pct:.0f}%"

    def render_code_block(self, widget: CodeBlock) -> str:
        return f"```{widget.language}\n{widget.code}\n```"

    def render_confirm(self, widget: Confirm) -> str:
        prefix = "⚠️ " if widget.danger else ""
        return f"{prefix}*{widget.prompt}*\nAntwort: Ja / Nein"

    def render_image_block(self, widget: ImageBlock) -> str:
        text = f"🖼 {widget.alt}"
        if widget.caption:
            text += f"\n_{widget.caption}_"
        return text

    def render_compose(self, widget: Compose) -> str:
        parts = [self.render(child) for child in widget.children]
        return "\n\n".join(parts)
