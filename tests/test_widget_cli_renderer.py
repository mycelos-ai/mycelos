"""Tests for CLI Widget Renderer — Rich output verification."""

from io import StringIO

from rich.console import Console

from mycelos.widgets import (
    TextBlock, Table, ChoiceBox, Choice, StatusCard,
    ProgressBar, CodeBlock, Confirm, ImageBlock, Compose,
)
from mycelos.widgets.cli_renderer import CLIRenderer


def _render_to_text(widget) -> str:
    """Render a widget to plain text via Rich Console."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    renderer = CLIRenderer(console)
    renderer.render(widget)
    return buf.getvalue()


class TestCLITextBlock:
    def test_plain(self):
        out = _render_to_text(TextBlock(text="Hello"))
        assert "Hello" in out

    def test_bold(self):
        out = _render_to_text(TextBlock(text="Title", weight="bold"))
        assert "Title" in out


class TestCLITable:
    def test_renders_headers_and_rows(self):
        out = _render_to_text(Table(
            headers=["Name", "Status"],
            rows=[["DB", "OK"], ["Cache", "Err"]],
        ))
        assert "Name" in out
        assert "DB" in out
        assert "Err" in out

    def test_empty_table(self):
        out = _render_to_text(Table(headers=["A"], rows=[]))
        assert "A" in out


class TestCLIChoiceBox:
    def test_renders_numbered_options(self):
        out = _render_to_text(ChoiceBox(
            prompt="Pick one",
            options=[Choice("a", "Alpha"), Choice("b", "Beta")],
        ))
        assert "Pick one" in out
        assert "1" in out
        assert "Alpha" in out
        assert "2" in out
        assert "Beta" in out


class TestCLIStatusCard:
    def test_renders_title_and_facts(self):
        out = _render_to_text(StatusCard(
            title="Backup", facts={"Status": "OK", "Time": "3m"}, style="success",
        ))
        assert "Backup" in out
        assert "Status" in out
        assert "OK" in out

    def test_error_style(self):
        out = _render_to_text(StatusCard(title="Fail", facts={}, style="error"))
        assert "Fail" in out


class TestCLIProgressBar:
    def test_renders_label_and_percentage(self):
        out = _render_to_text(ProgressBar(label="Upload", current=67, total=100))
        assert "Upload" in out
        assert "67" in out


class TestCLICodeBlock:
    def test_renders_code(self):
        out = _render_to_text(CodeBlock(code="print('hello')", language="python"))
        assert "print" in out


class TestCLIConfirm:
    def test_renders_prompt(self):
        out = _render_to_text(Confirm(prompt="Delete everything?", danger=True))
        assert "Delete everything?" in out


class TestCLIImageBlock:
    def test_renders_alt_text(self):
        out = _render_to_text(ImageBlock(url="https://x.com/i.png", alt="Diagram"))
        assert "Diagram" in out

    def test_renders_caption(self):
        out = _render_to_text(ImageBlock(url="https://x.com/i.png", alt="D", caption="Fig 1"))
        assert "Fig 1" in out


class TestCLICompose:
    def test_renders_all_children(self):
        out = _render_to_text(Compose(children=[
            TextBlock(text="Header", weight="bold"),
            Table(headers=["X"], rows=[["1"]]),
        ]))
        assert "Header" in out
        assert "X" in out
        assert "1" in out


class TestCLIRendererProtocol:
    def test_satisfies_widget_renderer_protocol(self):
        from mycelos.widgets.renderer import WidgetRenderer
        console = Console(file=StringIO(), force_terminal=True, width=80)
        assert isinstance(CLIRenderer(console), WidgetRenderer)
