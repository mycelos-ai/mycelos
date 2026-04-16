"""Tests for User Flow — provider detection, init, onboarding."""

import pytest
from mycelos.cli.detect_provider import detect_provider


class TestProviderDetection:
    def test_anthropic_key(self):
        r = detect_provider("sk-ant-api03-abc123")
        assert r.provider == "anthropic"
        assert r.default_model == "anthropic/claude-sonnet-4-6"
        assert r.env_var == "ANTHROPIC_API_KEY"
        assert not r.is_url

    def test_openai_key(self):
        r = detect_provider("sk-proj-abc123")
        assert r.provider == "openai"
        assert r.default_model == "openai/gpt-4o"

    def test_openrouter_key(self):
        r = detect_provider("sk-or-abc123")
        assert r.provider == "openrouter"
        assert "openrouter" in r.default_model

    def test_google_key(self):
        r = detect_provider("AIzaSyAbc123")
        assert r.provider == "gemini"
        assert "gemini" in r.default_model

    def test_ollama_localhost(self):
        r = detect_provider("http://localhost:11434")
        assert r.provider == "ollama"
        assert r.is_url
        assert r.server_url == "http://localhost:11434"

    def test_ollama_remote(self):
        r = detect_provider("http://192.168.1.50:11434")
        assert r.provider == "ollama"
        assert r.server_url == "http://192.168.1.50:11434"

    def test_https_url(self):
        r = detect_provider("https://my-ollama.example.com")
        assert r.provider == "ollama"
        assert r.is_url

    def test_unknown_key(self):
        r = detect_provider("some-random-string")
        assert r.provider is None
        assert r.needs_manual_selection

    def test_empty_string(self):
        r = detect_provider("")
        assert r.provider is None
        assert r.needs_manual_selection

    def test_whitespace_stripped(self):
        r = detect_provider("  sk-ant-api03-abc123  ")
        assert r.provider == "anthropic"

    def test_url_trailing_slash_stripped(self):
        r = detect_provider("http://localhost:11434/")
        assert r.server_url == "http://localhost:11434"

    def test_sk_or_not_confused_with_sk(self):
        """sk-or- must match OpenRouter, not OpenAI."""
        r = detect_provider("sk-or-v1-abc123")
        assert r.provider == "openrouter"

    def test_sk_ant_not_confused_with_sk(self):
        """sk-ant- must match Anthropic, not OpenAI."""
        r = detect_provider("sk-ant-api03-abc123")
        assert r.provider == "anthropic"


class TestSimplifiedInit:
    def test_check_connectivity_success(self):
        from mycelos.cli.init_cmd import _check_connectivity
        from unittest.mock import MagicMock
        mock_broker = MagicMock()
        mock_broker.complete.return_value = MagicMock(content="Hello")
        success, msg = _check_connectivity(mock_broker)
        assert success is True
        assert "Hello" in msg

    def test_check_connectivity_failure(self):
        from mycelos.cli.init_cmd import _check_connectivity
        from unittest.mock import MagicMock
        mock_broker = MagicMock()
        mock_broker.complete.side_effect = Exception("Invalid API key")
        success, msg = _check_connectivity(mock_broker)
        assert success is False
        assert "Invalid" in msg

    def test_check_connectivity_empty_response(self):
        from mycelos.cli.init_cmd import _check_connectivity
        from unittest.mock import MagicMock
        mock_broker = MagicMock()
        mock_broker.complete.return_value = MagicMock(content="")
        success, msg = _check_connectivity(mock_broker)
        assert success is False


class TestOnboarding:
    def test_onboarding_workflow_in_templates(self):
        from mycelos.workflows.templates import BUILTIN_WORKFLOWS
        ids = [w["id"] for w in BUILTIN_WORKFLOWS]
        assert "onboarding" in ids

    def test_onboarding_workflow_has_steps(self):
        import json
        from mycelos.workflows.templates import BUILTIN_WORKFLOWS
        onboarding = next(w for w in BUILTIN_WORKFLOWS if w["id"] == "onboarding")
        steps = json.loads(onboarding["steps"])
        assert len(steps) >= 3
        assert steps[0]["id"] == "greeting"

    def test_onboarding_not_completed_initially(self):
        import os, tempfile
        from pathlib import Path
        from mycelos.app import App
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-ob"
            app = App(Path(tmp))
            app.initialize()
            result = app.memory.get("default", "system", "onboarding_completed")
            assert result is None

    def test_onboarding_can_be_completed(self):
        import os, tempfile
        from pathlib import Path
        from mycelos.app import App
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-ob2"
            app = App(Path(tmp))
            app.initialize()
            app.memory.set("default", "system", "onboarding_completed", "true")
            result = app.memory.get("default", "system", "onboarding_completed")
            assert result  # truthy — may be stored as bool True or string "true"

    def test_onboarding_workflow_seeded_on_init(self):
        import os, tempfile
        from pathlib import Path
        from mycelos.app import App
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-ob3"
            app = App(Path(tmp))
            app.initialize()
            wf = app.workflow_registry.get("onboarding")
            assert wf is not None
            tags = wf.get("tags", [])
            tags_str = str(tags).lower()
            assert "onboarding" in tags_str or "onboarding" in wf.get("id", "")
