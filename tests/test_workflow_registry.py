"""Tests for WorkflowRegistry — CRUD for reusable workflow definitions."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from mycelos.storage.database import SQLiteStorage
from mycelos.workflows.workflow_registry import WorkflowRegistry


@pytest.fixture
def storage() -> SQLiteStorage:
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStorage(Path(tmp) / "test.db")
        s.initialize()
        yield s


@pytest.fixture
def registry(storage: SQLiteStorage) -> WorkflowRegistry:
    return WorkflowRegistry(storage)


SAMPLE_STEPS = [{"id": "s1", "agent": "search", "input": "query"}]


# --- register + get ---


def test_register_and_get(registry: WorkflowRegistry) -> None:
    registry.register("wf-search", "Search Workflow", steps=SAMPLE_STEPS)
    wf = registry.get("wf-search")
    assert wf is not None
    assert wf["id"] == "wf-search"
    assert wf["name"] == "Search Workflow"
    assert wf["steps"] == SAMPLE_STEPS
    assert wf["version"] == 1
    assert wf["status"] == "active"
    assert wf["created_by"] == "system"


def test_register_with_all_fields(registry: WorkflowRegistry) -> None:
    registry.register(
        workflow_id="wf-full",
        name="Full Workflow",
        steps=SAMPLE_STEPS,
        description="A full workflow with all fields",
        goal="Find and summarize news",
        scope=["search.web", "llm.summarize"],
        tags=["news", "daily"],
        created_by="user:stefan",
    )
    wf = registry.get("wf-full")
    assert wf is not None
    assert wf["description"] == "A full workflow with all fields"
    assert wf["goal"] == "Find and summarize news"
    assert wf["scope"] == ["search.web", "llm.summarize"]
    assert wf["tags"] == ["news", "daily"]
    assert wf["created_by"] == "user:stefan"


def test_get_nonexistent_returns_none(registry: WorkflowRegistry) -> None:
    assert registry.get("does-not-exist") is None


# --- list_workflows ---


def test_list_workflows_all(registry: WorkflowRegistry) -> None:
    registry.register("wf-a", "Alpha", steps=SAMPLE_STEPS)
    registry.register("wf-b", "Beta", steps=SAMPLE_STEPS)
    workflows = registry.list_workflows()
    assert len(workflows) == 2
    names = [w["name"] for w in workflows]
    assert names == ["Alpha", "Beta"]  # ordered by name


def test_list_workflows_by_status(registry: WorkflowRegistry) -> None:
    registry.register("wf-a", "Alpha", steps=SAMPLE_STEPS)
    registry.register("wf-b", "Beta", steps=SAMPLE_STEPS)
    registry.deprecate("wf-b")
    active = registry.list_workflows(status="active")
    assert len(active) == 1
    assert active[0]["id"] == "wf-a"
    deprecated = registry.list_workflows(status="deprecated")
    assert len(deprecated) == 1
    assert deprecated[0]["id"] == "wf-b"


def test_list_workflows_by_tag(registry: WorkflowRegistry) -> None:
    registry.register("wf-a", "Alpha", steps=SAMPLE_STEPS, tags=["news"])
    registry.register("wf-b", "Beta", steps=SAMPLE_STEPS, tags=["code"])
    registry.register("wf-c", "Gamma", steps=SAMPLE_STEPS, tags=["news", "code"])
    news = registry.list_workflows(tag="news")
    assert len(news) == 2
    ids = {w["id"] for w in news}
    assert ids == {"wf-a", "wf-c"}


def test_list_workflows_by_status_and_tag(registry: WorkflowRegistry) -> None:
    registry.register("wf-a", "Alpha", steps=SAMPLE_STEPS, tags=["news"])
    registry.register("wf-b", "Beta", steps=SAMPLE_STEPS, tags=["news"])
    registry.deprecate("wf-b")
    result = registry.list_workflows(status="active", tag="news")
    assert len(result) == 1
    assert result[0]["id"] == "wf-a"


# --- update ---


def test_update_increments_version(registry: WorkflowRegistry) -> None:
    registry.register("wf-1", "Original", steps=SAMPLE_STEPS)
    new_steps = [{"id": "s1", "agent": "updated"}]
    registry.update("wf-1", steps=new_steps)
    wf = registry.get("wf-1")
    assert wf["version"] == 2
    assert wf["steps"] == new_steps
    assert wf["updated_at"] is not None


def test_update_multiple_fields(registry: WorkflowRegistry) -> None:
    registry.register("wf-1", "Original", steps=SAMPLE_STEPS)
    registry.update(
        "wf-1",
        description="Updated desc",
        goal="New goal",
        scope=["new.scope"],
        tags=["updated"],
    )
    wf = registry.get("wf-1")
    assert wf["version"] == 2
    assert wf["description"] == "Updated desc"
    assert wf["goal"] == "New goal"
    assert wf["scope"] == ["new.scope"]
    assert wf["tags"] == ["updated"]


def test_update_nonexistent_raises(registry: WorkflowRegistry) -> None:
    with pytest.raises(ValueError, match="not found"):
        registry.update("nope", steps=[])


def test_update_twice_increments_to_three(registry: WorkflowRegistry) -> None:
    registry.register("wf-1", "Original", steps=SAMPLE_STEPS)
    registry.update("wf-1", description="v2")
    registry.update("wf-1", description="v3")
    wf = registry.get("wf-1")
    assert wf["version"] == 3


# --- deprecate ---


def test_deprecate_changes_status(registry: WorkflowRegistry) -> None:
    registry.register("wf-1", "To Deprecate", steps=SAMPLE_STEPS)
    registry.deprecate("wf-1")
    wf = registry.get("wf-1")
    assert wf["status"] == "deprecated"
    assert wf["updated_at"] is not None


# --- remove ---


def test_remove_deletes_workflow(registry: WorkflowRegistry) -> None:
    registry.register("wf-1", "To Remove", steps=SAMPLE_STEPS)
    registry.remove("wf-1")
    assert registry.get("wf-1") is None


def test_remove_nonexistent_no_error(registry: WorkflowRegistry) -> None:
    registry.remove("nope")  # should not raise


# --- import_from_yaml ---


def test_import_from_yaml_creates_workflow(
    registry: WorkflowRegistry, tmp_path: Path
) -> None:
    yaml_data = {
        "name": "yaml-wf",
        "description": "From YAML",
        "goal": "Test import",
        "scope": ["read.file"],
        "tags": ["import"],
        "steps": [{"id": "s1", "agent": "reader"}],
    }
    yaml_file = tmp_path / "workflow.yaml"
    yaml_file.write_text(yaml.dump(yaml_data))

    result_id = registry.import_from_yaml(yaml_file)
    assert result_id == "yaml-wf"

    wf = registry.get("yaml-wf")
    assert wf is not None
    assert wf["description"] == "From YAML"
    assert wf["goal"] == "Test import"
    assert wf["scope"] == ["read.file"]
    assert wf["tags"] == ["import"]
    assert wf["steps"] == [{"id": "s1", "agent": "reader"}]


def test_import_from_yaml_updates_existing(
    registry: WorkflowRegistry, tmp_path: Path
) -> None:
    registry.register("yaml-wf", "Original", steps=SAMPLE_STEPS)

    yaml_data = {
        "name": "yaml-wf",
        "description": "Updated via YAML",
        "steps": [{"id": "s1", "agent": "updated-reader"}],
    }
    yaml_file = tmp_path / "workflow.yaml"
    yaml_file.write_text(yaml.dump(yaml_data))

    result_id = registry.import_from_yaml(yaml_file)
    assert result_id == "yaml-wf"

    wf = registry.get("yaml-wf")
    assert wf["version"] == 2
    assert wf["description"] == "Updated via YAML"
    assert wf["steps"] == [{"id": "s1", "agent": "updated-reader"}]


def test_import_from_yaml_uses_stem_as_fallback_id(
    registry: WorkflowRegistry, tmp_path: Path
) -> None:
    yaml_data = {"steps": [{"id": "s1", "agent": "test"}]}
    yaml_file = tmp_path / "my-workflow.yaml"
    yaml_file.write_text(yaml.dump(yaml_data))

    result_id = registry.import_from_yaml(yaml_file)
    assert result_id == "my-workflow"
    assert registry.get("my-workflow") is not None


# --- export_to_yaml ---


def test_export_to_yaml_round_trip(
    registry: WorkflowRegistry, tmp_path: Path
) -> None:
    registry.register(
        "wf-export",
        "Export Test",
        steps=SAMPLE_STEPS,
        description="For export",
        goal="Test round-trip",
        scope=["search.web"],
        tags=["export"],
    )
    yaml_str = registry.export_to_yaml("wf-export")
    data = yaml.safe_load(yaml_str)
    assert data["name"] == "Export Test"
    assert data["description"] == "For export"
    assert data["goal"] == "Test round-trip"
    assert data["version"] == 1
    assert data["scope"] == ["search.web"]
    assert data["steps"] == SAMPLE_STEPS
    assert data["tags"] == ["export"]


def test_export_nonexistent_raises(registry: WorkflowRegistry) -> None:
    with pytest.raises(ValueError, match="not found"):
        registry.export_to_yaml("nope")


# --- _parse_row ---


def test_parse_row_handles_json_fields(registry: WorkflowRegistry) -> None:
    row = {
        "id": "wf-1",
        "name": "Test",
        "steps": '[{"id": "s1"}]',
        "scope": '["a", "b"]',
        "tags": '["t1"]',
        "version": 1,
        "status": "active",
    }
    parsed = registry._parse_row(row)
    assert parsed["steps"] == [{"id": "s1"}]
    assert parsed["scope"] == ["a", "b"]
    assert parsed["tags"] == ["t1"]


def test_parse_row_handles_null_json_fields(registry: WorkflowRegistry) -> None:
    row = {
        "id": "wf-1",
        "name": "Test",
        "steps": '[{"id": "s1"}]',
        "scope": None,
        "tags": None,
        "version": 1,
        "status": "active",
    }
    parsed = registry._parse_row(row)
    assert parsed["steps"] == [{"id": "s1"}]
    assert parsed["scope"] is None
    assert parsed["tags"] is None
