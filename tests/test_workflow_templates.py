"""Tests for workflow templates — requires field, Builder awareness, template loading."""

from __future__ import annotations

import yaml
from pathlib import Path

import pytest


TEMPLATES_DIR = Path(__file__).parent.parent / "artifacts" / "workflows"


class TestTemplateYAMLs:
    """All template YAMLs are valid and have required fields."""

    def _load_all(self) -> list[dict]:
        templates = []
        for yf in sorted(TEMPLATES_DIR.glob("*.yaml")):
            data = yaml.safe_load(yf.read_text())
            data["_file"] = yf.name
            templates.append(data)
        return templates

    def test_templates_exist(self):
        templates = self._load_all()
        assert len(templates) >= 5, f"Expected at least 5 templates, got {len(templates)}"

    def test_all_have_name(self):
        for t in self._load_all():
            assert "name" in t, f"{t['_file']} missing 'name'"

    def test_all_have_description(self):
        for t in self._load_all():
            assert "description" in t, f"{t['_file']} missing 'description'"

    def test_all_have_requires(self):
        for t in self._load_all():
            assert "requires" in t, f"{t['_file']} missing 'requires'"
            req = t["requires"]
            assert "connectors" in req, f"{t['_file']} requires missing 'connectors'"
            assert "tools" in req, f"{t['_file']} requires missing 'tools'"

    def test_all_have_allowed_tools(self):
        for t in self._load_all():
            assert "allowed_tools" in t, f"{t['_file']} missing 'allowed_tools'"

    def test_all_have_plan(self):
        for t in self._load_all():
            assert "plan" in t, f"{t['_file']} missing 'plan'"

    def test_all_have_model(self):
        for t in self._load_all():
            assert "model" in t, f"{t['_file']} missing 'model'"

    def test_email_templates_require_email_connector(self):
        for t in self._load_all():
            if "email" in t.get("tags", []):
                req = t.get("requires", {})
                assert "email" in req.get("connectors", []), \
                    f"{t['_file']} has email tag but doesn't require email connector"

    def test_email_digest_is_read_only(self):
        data = yaml.safe_load((TEMPLATES_DIR / "email-digest.yaml").read_text())
        # Email now routes through the MCP connector, so a read-only
        # template exposes connector_call but nothing that writes.
        assert "email.send" not in data.get("scope", [])
        allowed = set(data.get("allowed_tools", []))
        # connector_call is allowed (read surface); no explicit send tool.
        assert "email_send" not in allowed

    def test_no_template_requires_nonexistent_tool(self):
        """All required tools should be valid tool names."""
        known_tools = {
            "note_list", "note_write", "note_search", "note_read", "note_update",
            "search_web", "search_news", "http_get",
            "connector_call",  # MCP dispatch — used by email templates since the
                               # @n24q02m/better-email-mcp migration (b365963+)
            "filesystem_read", "filesystem_write", "filesystem_list",
            "create_agent", "create_workflow", "handoff", "list_tools",
        }
        for t in self._load_all():
            for tool in t.get("requires", {}).get("tools", []):
                assert tool in known_tools, \
                    f"{t['_file']} requires unknown tool: {tool}"


class TestTemplateDiscovery:
    """Templates are discoverable via list_tools."""

    def test_list_tools_includes_templates(self, tmp_path):
        import os
        from mycelos.app import App
        from mycelos.tools.system import execute_list_tools

        os.environ["MYCELOS_MASTER_KEY"] = "test-key-templates"
        app = App(tmp_path / "data")
        app.initialize()

        result = execute_list_tools({}, {"app": app})
        assert "workflow_templates" in result
        templates = result["workflow_templates"]
        assert len(templates) >= 5

        # Check structure
        for t in templates:
            assert "id" in t
            assert "description" in t
            assert "requires" in t

    def test_template_requires_structure(self, tmp_path):
        import os
        from mycelos.app import App
        from mycelos.tools.system import execute_list_tools

        os.environ["MYCELOS_MASTER_KEY"] = "test-key-templates2"
        app = App(tmp_path / "data")
        app.initialize()

        result = execute_list_tools({}, {"app": app})
        email_digest = next(
            (t for t in result["workflow_templates"] if t["id"] == "email-digest"),
            None,
        )
        assert email_digest is not None
        assert "email" in email_digest["requires"].get("connectors", [])
        # Templates now dispatch all email operations through
        # connector_call against the MCP server.
        assert "connector_call" in email_digest["requires"].get("tools", [])


class TestPrerequisiteChecking:
    """Templates with unmet prerequisites are identified."""

    def test_check_connector_available(self, tmp_path):
        """Check if a required connector is configured."""
        import os
        from mycelos.app import App

        os.environ["MYCELOS_MASTER_KEY"] = "test-key-prereq"
        app = App(tmp_path / "data")
        app.initialize()

        # No email connector → prerequisite not met
        connectors = app.connector_registry.list_connectors(status="active")
        connector_ids = {c["id"] for c in connectors}
        assert "email" not in connector_ids

        # Register email connector → now met
        app.connector_registry.register("email", "Email", "builtin", ["email.read"])
        connectors = app.connector_registry.list_connectors(status="active")
        connector_ids = {c["id"] for c in connectors}
        assert "email" in connector_ids
