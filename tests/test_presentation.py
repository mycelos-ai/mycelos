"""Tests for the Presentation Layer (Two-Layer Execution, Section 10)."""

import json

import pytest

from mycelos.execution.presentation import PresentationLayer


@pytest.fixture
def layer() -> PresentationLayer:
    return PresentationLayer(token_budget=100)  # Small budget for testing


def test_short_text_unchanged(layer: PresentationLayer) -> None:
    text = "Hello world"
    result = layer.present(text)
    assert result.content == text
    assert result.was_truncated is False


def test_long_text_head_tail(layer: PresentationLayer) -> None:
    text = "A" * 1000
    result = layer.present(text, content_type="text")
    assert result.was_truncated is True
    assert "omitted" in result.content
    assert result.content.startswith("A")


def test_small_json_unchanged() -> None:
    layer = PresentationLayer(token_budget=1000)
    data = {"key": "value", "count": 42}
    content = json.dumps(data)
    result = layer.present(content)
    assert result.content_type == "json"
    assert result.was_truncated is False


def test_json_array_truncation() -> None:
    layer = PresentationLayer(token_budget=200)
    data = {"items": list(range(100))}
    content = json.dumps(data)
    result = layer.present(content)
    assert "more items" in result.content


def test_json_deep_nesting_collapsed() -> None:
    layer = PresentationLayer(token_budget=200)
    data = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
    content = json.dumps(data)
    result = layer.present(content)
    parsed = json.loads(result.content)
    assert "..." in str(parsed)


def test_short_csv_unchanged() -> None:
    layer = PresentationLayer(token_budget=1000)
    csv = "name,age\nAlice,30\nBob,25"
    result = layer.present(csv, content_type="csv")
    assert result.was_truncated is False


def test_long_csv_truncated() -> None:
    layer = PresentationLayer(token_budget=1000)
    header = "id,name,value"
    rows = [f"{i},item_{i},{i*10}" for i in range(100)]
    csv = header + "\n" + "\n".join(rows)
    result = layer.present(csv, content_type="csv")
    assert result.was_truncated is True
    assert "rows omitted" in result.content
    assert header in result.content


def test_binary_never_sent_to_llm() -> None:
    layer = PresentationLayer()
    result = layer.present(b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000)
    assert result.content_type == "binary"
    assert "Binary data" in result.content


def test_traceback_preserved() -> None:
    layer = PresentationLayer(token_budget=1000)
    tb = 'Traceback (most recent call last):\n  File "test.py", line 1\nValueError: bad input'
    result = layer.present(tb)
    assert result.content_type == "traceback"
    assert "ValueError" in result.content
    assert result.was_truncated is False


def test_auto_detect_json() -> None:
    layer = PresentationLayer(token_budget=1000)
    result = layer.present('{"key": "value"}')
    assert result.content_type == "json"


def test_auto_detect_traceback() -> None:
    layer = PresentationLayer(token_budget=1000)
    result = layer.present('Traceback (most recent call last):\n  File "x.py"')
    assert result.content_type == "traceback"


def test_auto_detect_csv() -> None:
    layer = PresentationLayer(token_budget=1000)
    result = layer.present("name,age,city\nAlice,30,Berlin\nBob,25,Munich")
    assert result.content_type == "csv"
