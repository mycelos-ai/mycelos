"""Tests for Telegram Widget Renderer — Markdown text output."""

from mycelos.widgets import (
    TextBlock, Table, ChoiceBox, Choice, StatusCard,
    ProgressBar, CodeBlock, Confirm, ImageBlock, Compose,
)
from mycelos.widgets.telegram_renderer import TelegramRenderer


def _render(widget) -> str:
    renderer = TelegramRenderer()
    return renderer.render(widget)


class TestTelegramTextBlock:
    def test_plain(self):
        assert _render(TextBlock(text="Hello")) == "Hello"

    def test_bold(self):
        assert _render(TextBlock(text="Title", weight="bold")) == "*Title*"

    def test_italic(self):
        assert _render(TextBlock(text="Note", weight="italic")) == "_Note_"


class TestTelegramTable:
    def test_renders_monospace(self):
        out = _render(Table(headers=["Name", "Status"], rows=[["DB", "OK"]]))
        assert "Name" in out
        assert "DB" in out
        assert "`" in out  # monospace formatting


class TestTelegramChoiceBox:
    def test_renders_numbered(self):
        out = _render(ChoiceBox(
            prompt="Pick",
            options=[Choice("a", "Alpha"), Choice("b", "Beta")],
        ))
        assert "Pick" in out
        assert "1." in out or "1)" in out
        assert "Alpha" in out


class TestTelegramStatusCard:
    def test_renders_emoji_and_facts(self):
        out = _render(StatusCard(title="Backup", facts={"Status": "OK"}, style="success"))
        assert "Backup" in out
        assert "Status" in out


class TestTelegramProgressBar:
    def test_renders_text_bar(self):
        out = _render(ProgressBar(label="Upload", current=50, total=100))
        assert "Upload" in out
        assert "50" in out


class TestTelegramCodeBlock:
    def test_renders_code_block(self):
        out = _render(CodeBlock(code="print('hi')", language="python"))
        assert "print" in out
        assert "```" in out


class TestTelegramConfirm:
    def test_renders_prompt(self):
        out = _render(Confirm(prompt="Delete?"))
        assert "Delete?" in out


class TestTelegramImageBlock:
    def test_renders_alt(self):
        out = _render(ImageBlock(url="https://x.com/i.png", alt="Diagram"))
        assert "Diagram" in out


class TestTelegramCompose:
    def test_joins_children(self):
        out = _render(Compose(children=[
            TextBlock(text="Title", weight="bold"),
            TextBlock(text="Body"),
        ]))
        assert "Title" in out
        assert "Body" in out
