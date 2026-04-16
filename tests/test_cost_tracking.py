"""Tests for LLM cost tracking — usage table + /cost command."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App
from mycelos.chat.slash_commands import handle_slash_command
from mycelos.llm.broker import LiteLLMBroker


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-cost"
        a = App(Path(tmp))
        a.initialize()
        yield a


# --- LLM Usage Table ---

def test_llm_usage_table_exists(app):
    app.storage.execute(
        "INSERT INTO llm_usage (model, input_tokens, output_tokens, total_tokens, cost) "
        "VALUES (?, ?, ?, ?, ?)",
        ("test-model", 100, 50, 150, 0.001),
    )
    row = app.storage.fetchone("SELECT * FROM llm_usage ORDER BY id DESC LIMIT 1")
    assert row is not None
    assert row["model"] == "test-model"
    assert row["cost"] == 0.001


# --- Broker tracks usage ---

def test_broker_tracks_to_db(app):
    """LLM Broker should write usage to llm_usage table."""
    broker = LiteLLMBroker(
        default_model="test-model",
        storage=app.storage,
    )

    # Mock litellm.completion
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hello!"
    mock_response.choices[0].message.tool_calls = None
    mock_response.usage.total_tokens = 100
    mock_response.usage.prompt_tokens = 70
    mock_response.usage.completion_tokens = 30

    with patch("litellm.completion", return_value=mock_response):
        with patch("litellm.model_cost", {"test-model": {
            "input_cost_per_token": 0.000003,
            "output_cost_per_token": 0.000015,
        }}):
            broker.complete([{"role": "user", "content": "Hi"}])

    rows = app.storage.fetchall("SELECT * FROM llm_usage")
    assert len(rows) >= 1
    assert rows[-1]["model"] == "test-model"
    assert rows[-1]["input_tokens"] == 70
    assert rows[-1]["output_tokens"] == 30
    assert rows[-1]["total_tokens"] == 100
    assert rows[-1]["cost"] > 0


def test_broker_calculates_cost(app):
    broker = LiteLLMBroker(default_model="test-model", storage=app.storage)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Test"
    mock_response.choices[0].message.tool_calls = None
    mock_response.usage.total_tokens = 1000
    mock_response.usage.prompt_tokens = 800
    mock_response.usage.completion_tokens = 200

    with patch("litellm.completion", return_value=mock_response):
        with patch("litellm.model_cost", {"test-model": {
            "input_cost_per_token": 0.000003,  # $3/1M
            "output_cost_per_token": 0.000015,  # $15/1M
        }}):
            broker.complete([{"role": "user", "content": "Test"}])

    row = app.storage.fetchone("SELECT * FROM llm_usage ORDER BY id DESC LIMIT 1")
    # 800 * 0.000003 + 200 * 0.000015 = 0.0024 + 0.003 = 0.0054
    assert abs(row["cost"] - 0.0054) < 0.0001


def test_broker_accumulates_total_cost(app):
    broker = LiteLLMBroker(default_model="test-model", storage=app.storage)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hi"
    mock_response.choices[0].message.tool_calls = None
    mock_response.usage.total_tokens = 100
    mock_response.usage.prompt_tokens = 80
    mock_response.usage.completion_tokens = 20

    with patch("litellm.completion", return_value=mock_response):
        with patch("litellm.model_cost", {"test-model": {
            "input_cost_per_token": 0.000003,
            "output_cost_per_token": 0.000015,
        }}):
            broker.complete([{"role": "user", "content": "1"}])
            broker.complete([{"role": "user", "content": "2"}])

    assert broker.total_cost > 0
    assert broker.total_tokens == 200

    rows = app.storage.fetchall("SELECT * FROM llm_usage")
    assert len(rows) >= 2


def test_broker_without_storage_still_works():
    """Broker should work fine without storage (no tracking)."""
    broker = LiteLLMBroker(default_model="test-model")  # No storage

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hi"
    mock_response.choices[0].message.tool_calls = None
    mock_response.usage.total_tokens = 50
    mock_response.usage.prompt_tokens = 40
    mock_response.usage.completion_tokens = 10

    with patch("litellm.completion", return_value=mock_response):
        result = broker.complete([{"role": "user", "content": "Hi"}])

    assert result.content == "Hi"
    assert result.total_tokens == 50


# --- /cost Slash Command ---

def test_cost_no_usage(app):
    result = handle_slash_command(app, "/cost")
    assert "No LLM usage" in result or "no" in result.lower()


def test_cost_with_usage(app):
    app.storage.execute(
        "INSERT INTO llm_usage (model, input_tokens, output_tokens, total_tokens, cost, purpose) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("claude-sonnet-4-6", 1000, 500, 1500, 0.0075, "chat"),
    )
    result = handle_slash_command(app, "/cost today")
    assert "$" in result
    assert "1,500" in result or "1500" in result
    assert "claude-sonnet" in result


def test_cost_by_model(app):
    app.storage.execute(
        "INSERT INTO llm_usage (model, total_tokens, cost, purpose) VALUES (?, ?, ?, ?)",
        ("model-a", 100, 0.001, "chat"),
    )
    app.storage.execute(
        "INSERT INTO llm_usage (model, total_tokens, cost, purpose) VALUES (?, ?, ?, ?)",
        ("model-b", 200, 0.005, "planning"),
    )
    result = handle_slash_command(app, "/cost all")
    assert "model-a" in result
    assert "model-b" in result


def test_cost_by_purpose(app):
    app.storage.execute(
        "INSERT INTO llm_usage (model, total_tokens, cost, purpose) VALUES (?, ?, ?, ?)",
        ("m1", 100, 0.001, "chat"),
    )
    app.storage.execute(
        "INSERT INTO llm_usage (model, total_tokens, cost, purpose) VALUES (?, ?, ?, ?)",
        ("m1", 200, 0.002, "classification"),
    )
    result = handle_slash_command(app, "/cost all")
    assert "chat" in result
    assert "classification" in result


def test_cost_periods(app):
    # Just verify all periods work without error
    for period in ("today", "week", "month", "all"):
        result = handle_slash_command(app, f"/cost {period}")
        assert isinstance(result, str)


def test_cost_help(app):
    result = handle_slash_command(app, "/cost invalid")
    assert "Usage" in result
