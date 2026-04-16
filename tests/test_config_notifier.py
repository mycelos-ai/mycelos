"""Tests for ConfigNotifier and registry config generation."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycelos.config.notifier import ConfigNotifier


class TestConfigNotifier:
    def test_notify_change_calls_apply(self):
        mock_config = MagicMock()
        mock_state = MagicMock()
        mock_audit = MagicMock()
        notifier = ConfigNotifier(mock_config, mock_state, mock_audit)
        notifier.notify_change("test change", "test")
        mock_config.apply_from_state.assert_called_once()

    def test_log_calls_audit(self):
        mock_config = MagicMock()
        mock_state = MagicMock()
        mock_audit = MagicMock()
        notifier = ConfigNotifier(mock_config, mock_state, mock_audit)
        notifier.log("test.event", {"key": "value"})
        mock_audit.log.assert_called_once_with("test.event", details={"key": "value"})

    def test_notify_change_handles_failure(self):
        mock_config = MagicMock()
        mock_config.apply_from_state.side_effect = Exception("DB error")
        mock_state = MagicMock()
        mock_audit = MagicMock()
        notifier = ConfigNotifier(mock_config, mock_state, mock_audit)
        # Should not raise
        notifier.notify_change("test", "test")

    def test_notify_change_passes_description_and_trigger(self):
        mock_config = MagicMock()
        mock_state = MagicMock()
        mock_audit = MagicMock()
        notifier = ConfigNotifier(mock_config, mock_state, mock_audit)
        notifier.notify_change("my description", "my_trigger")
        mock_config.apply_from_state.assert_called_once_with(
            mock_state,
            description="my description",
            trigger="my_trigger",
        )

    def test_log_with_no_details(self):
        mock_config = MagicMock()
        mock_state = MagicMock()
        mock_audit = MagicMock()
        notifier = ConfigNotifier(mock_config, mock_state, mock_audit)
        notifier.log("some.event")
        mock_audit.log.assert_called_once_with("some.event", details=None)


class TestRegistryNotification:
    def test_agent_registry_triggers_config(self):
        from mycelos.app import App
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-nixos"
            app = App(Path(tmp))
            app.initialize()
            gen_before = app.config.get_active_generation_id()
            app.agent_registry.register("test-agent", "Test", "deterministic", ["test.cap"], "system")
            gen_after = app.config.get_active_generation_id()
            assert gen_after > gen_before

    def test_policy_triggers_config(self):
        from mycelos.app import App
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-nixos2"
            app = App(Path(tmp))
            app.initialize()
            gen_before = app.config.get_active_generation_id()
            app.policy_engine.set_policy("default", "test-agent", "test.resource", "always")
            gen_after = app.config.get_active_generation_id()
            assert gen_after > gen_before

    def test_connector_registry_triggers_config(self):
        from mycelos.app import App
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-nixos3"
            app = App(Path(tmp))
            app.initialize()
            gen_before = app.config.get_active_generation_id()
            app.connector_registry.register(
                "test-connector", "Test Connector", "mcp", ["test.read"]
            )
            gen_after = app.config.get_active_generation_id()
            assert gen_after > gen_before

    def test_workflow_registry_triggers_config(self):
        from mycelos.app import App
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-nixos4"
            app = App(Path(tmp))
            app.initialize()
            gen_before = app.config.get_active_generation_id()
            app.workflow_registry.register(
                "test-workflow", "Test Workflow", [{"agent": "system", "action": "noop"}]
            )
            gen_after = app.config.get_active_generation_id()
            assert gen_after > gen_before

    def test_schedule_manager_triggers_config(self):
        from mycelos.app import App
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-nixos5"
            app = App(Path(tmp))
            app.initialize()
            # Register a workflow first
            app.workflow_registry.register(
                "sched-workflow", "Sched Workflow", [{"agent": "system", "action": "noop"}]
            )
            gen_before = app.config.get_active_generation_id()
            app.schedule_manager.add(workflow_id="sched-workflow", schedule="0 8 * * *")
            gen_after = app.config.get_active_generation_id()
            assert gen_after > gen_before

    def test_registry_without_notifier_still_works(self):
        """Registries are backward compatible — no notifier means no config generation."""
        from mycelos.agents.registry import AgentRegistry
        from mycelos.storage.database import SQLiteStorage
        with tempfile.TemporaryDirectory() as tmp:
            storage = SQLiteStorage(Path(tmp) / "test.db")
            storage.initialize()
            registry = AgentRegistry(storage)  # no notifier
            # Should not raise
            registry.register("agent-x", "Agent X", "deterministic", ["cap.read"], "system")
            agent = registry.get("agent-x")
            assert agent is not None
            assert agent["id"] == "agent-x"
