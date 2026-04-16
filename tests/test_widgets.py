"""Tests for Widget IR — creation, composition, serialization."""

from mycelos.widgets import (
    TextBlock, Table, ChoiceBox, Choice, StatusCard,
    ProgressBar, CodeBlock, Confirm, ImageBlock, Compose,
)


class TestTextBlock:
    def test_create_plain(self):
        w = TextBlock(text="Hello")
        assert w.text == "Hello"
        assert w.weight == "normal"

    def test_create_bold(self):
        w = TextBlock(text="Title", weight="bold")
        assert w.weight == "bold"

    def test_is_frozen(self):
        import pytest
        w = TextBlock(text="x")
        with pytest.raises(AttributeError):
            w.text = "y"


class TestTable:
    def test_create(self):
        t = Table(headers=["Name", "Status"], rows=[["DB", "OK"]])
        assert len(t.headers) == 2
        assert len(t.rows) == 1

    def test_empty_rows(self):
        t = Table(headers=["A"], rows=[])
        assert t.rows == []


class TestChoiceBox:
    def test_create(self):
        cb = ChoiceBox(
            prompt="Pick one",
            options=[Choice(id="a", label="Alpha"), Choice(id="b", label="Beta")],
        )
        assert len(cb.options) == 2
        assert cb.options[0].id == "a"


class TestStatusCard:
    def test_create(self):
        sc = StatusCard(title="Backup", facts={"Status": "OK"}, style="success")
        assert sc.style == "success"

    def test_default_style(self):
        sc = StatusCard(title="Info", facts={})
        assert sc.style == "info"


class TestProgressBar:
    def test_create(self):
        pb = ProgressBar(label="Upload", current=67, total=100)
        assert pb.current == 67

    def test_percentage(self):
        pb = ProgressBar(label="X", current=50, total=200)
        assert pb.percentage == 25.0

    def test_percentage_zero_total(self):
        pb = ProgressBar(label="X", current=0, total=0)
        assert pb.percentage == 0.0


class TestCodeBlock:
    def test_create(self):
        cb = CodeBlock(code="print('hi')", language="python")
        assert cb.language == "python"

    def test_default_language(self):
        cb = CodeBlock(code="x = 1")
        assert cb.language == "text"


class TestConfirm:
    def test_create(self):
        c = Confirm(prompt="Delete?", danger=True)
        assert c.danger is True

    def test_default_not_danger(self):
        c = Confirm(prompt="Continue?")
        assert c.danger is False


class TestImageBlock:
    def test_create(self):
        ib = ImageBlock(url="https://example.com/img.png", alt="diagram")
        assert ib.caption is None

    def test_with_caption(self):
        ib = ImageBlock(url="https://x.com/i.png", alt="x", caption="Fig 1")
        assert ib.caption == "Fig 1"


class TestCompose:
    def test_create(self):
        c = Compose(children=[
            TextBlock(text="Title", weight="bold"),
            Table(headers=["A"], rows=[["1"]]),
        ])
        assert len(c.children) == 2

    def test_empty(self):
        c = Compose(children=[])
        assert len(c.children) == 0


from mycelos.widgets import widget_to_dict, widget_from_dict


class TestSerialization:
    def test_text_block_roundtrip(self):
        w = TextBlock(text="Hello", weight="bold")
        d = widget_to_dict(w)
        assert d == {"type": "text_block", "text": "Hello", "weight": "bold"}
        w2 = widget_from_dict(d)
        assert w2 == w

    def test_table_roundtrip(self):
        w = Table(headers=["A", "B"], rows=[["1", "2"]])
        d = widget_to_dict(w)
        assert d["type"] == "table"
        assert widget_from_dict(d) == w

    def test_choice_box_roundtrip(self):
        w = ChoiceBox(prompt="Pick", options=[Choice(id="x", label="X")])
        d = widget_to_dict(w)
        assert d["type"] == "choice_box"
        assert d["options"] == [{"id": "x", "label": "X"}]
        assert widget_from_dict(d) == w

    def test_status_card_roundtrip(self):
        w = StatusCard(title="T", facts={"k": "v"}, style="error")
        assert widget_from_dict(widget_to_dict(w)) == w

    def test_progress_bar_roundtrip(self):
        w = ProgressBar(label="L", current=5, total=10)
        assert widget_from_dict(widget_to_dict(w)) == w

    def test_code_block_roundtrip(self):
        w = CodeBlock(code="x=1", language="python")
        assert widget_from_dict(widget_to_dict(w)) == w

    def test_confirm_roundtrip(self):
        w = Confirm(prompt="Sure?", danger=True)
        assert widget_from_dict(widget_to_dict(w)) == w

    def test_image_block_roundtrip(self):
        w = ImageBlock(url="https://x.com/i.png", alt="pic", caption="Fig 1")
        assert widget_from_dict(widget_to_dict(w)) == w

    def test_compose_roundtrip(self):
        w = Compose(children=[
            TextBlock(text="Title", weight="bold"),
            Table(headers=["A"], rows=[["1"]]),
        ])
        d = widget_to_dict(w)
        assert d["type"] == "compose"
        assert len(d["children"]) == 2
        assert widget_from_dict(d) == w

    def test_nested_compose_roundtrip(self):
        w = Compose(children=[
            TextBlock(text="Outer"),
            Compose(children=[
                TextBlock(text="Inner"),
                CodeBlock(code="x=1"),
            ]),
        ])
        d = widget_to_dict(w)
        assert d["children"][1]["type"] == "compose"
        assert widget_from_dict(d) == w

    def test_unknown_type_raises(self):
        import pytest
        with pytest.raises(ValueError, match="Unknown widget type"):
            widget_from_dict({"type": "alien"})


class TestWidgetEventIntegration:
    """Verify widget events are correctly created and contain valid JSON."""

    def test_widget_event_contains_serialized_widget(self):
        from mycelos.chat.events import widget_event
        w = StatusCard(title="Test", facts={"a": "b"}, style="info")
        event = widget_event(w)
        # The event data must be deserializable back to a widget
        from mycelos.widgets import widget_from_dict
        restored = widget_from_dict(event.data["widget"])
        assert restored == w


class TestEndToEnd:
    """Full pipeline: create widget → event → serialize → deserialize → render."""

    def test_compose_through_event_pipeline(self):
        from mycelos.chat.events import widget_event
        from mycelos.widgets import widget_from_dict

        # Agent creates a composed widget
        widget = Compose(children=[
            TextBlock(text="Backup Report", weight="bold"),
            Table(headers=["Server", "Status"], rows=[["DB", "OK"], ["Cache", "Err"]]),
            StatusCard(title="Summary", facts={"Total": "2", "Failed": "1"}, style="warning"),
            ProgressBar(label="Progress", current=1, total=2),
        ])

        # Widget flows through ChatEvent
        event = widget_event(widget)
        assert event.type == "widget"

        # SSE serialization roundtrip
        import json
        sse = event.to_sse()
        assert "event: widget" in sse

        # Deserialize back
        restored = widget_from_dict(event.data["widget"])
        assert len(restored.children) == 4
        assert isinstance(restored.children[0], TextBlock)
        assert isinstance(restored.children[1], Table)
        assert isinstance(restored.children[2], StatusCard)
        assert isinstance(restored.children[3], ProgressBar)

    def test_cli_renderer_full_compose(self):
        from io import StringIO
        from rich.console import Console
        from mycelos.widgets.cli_renderer import CLIRenderer

        widget = Compose(children=[
            TextBlock(text="Status", weight="bold"),
            StatusCard(title="DB", facts={"Uptime": "99.9%"}, style="success"),
            CodeBlock(code="SELECT 1;", language="sql"),
        ])

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        CLIRenderer(console).render(widget)
        out = buf.getvalue()

        assert "Status" in out
        assert "DB" in out
        assert "99.9%" in out
        assert "SELECT" in out

    def test_telegram_renderer_full_compose(self):
        from mycelos.widgets.telegram_renderer import TelegramRenderer

        widget = Compose(children=[
            TextBlock(text="Report", weight="bold"),
            ProgressBar(label="Upload", current=100, total=100),
            Confirm(prompt="Commit?"),
        ])

        out = TelegramRenderer().render(widget)
        assert "*Report*" in out
        assert "100%" in out
        assert "Commit?" in out
