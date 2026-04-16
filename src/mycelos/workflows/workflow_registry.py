"""WorkflowRegistry — CRUD for reusable workflow definitions in SQLite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from mycelos.protocols import StorageBackend


def _safe_json_dumps(value: Any) -> str:
    """Serialize to JSON, guarding against already-serialized strings."""
    if isinstance(value, str):
        # Check if it's already valid JSON
        try:
            json.loads(value)
            return value  # already serialized
        except (json.JSONDecodeError, ValueError):
            pass
    return json.dumps(value)


class WorkflowRegistry:
    """Manages workflow definitions in the workflows table."""

    def __init__(self, storage: StorageBackend, notifier=None) -> None:
        self._storage = storage
        self._notifier = notifier

    def register(
        self,
        workflow_id: str,
        name: str,
        steps: list[dict],
        description: str | None = None,
        goal: str | None = None,
        scope: list[str] | None = None,
        tags: list[str] | None = None,
        created_by: str = "system",
        plan: str | None = None,
        model: str | None = None,
        allowed_tools: list[str] | None = None,
        inputs: list[dict] | None = None,
        success_criteria: str | None = None,
        notification_mode: str | None = None,
    ) -> None:
        """Register a new workflow definition."""
        self._storage.execute(
            """INSERT INTO workflows
               (id, name, steps, description, goal, scope, tags, created_by,
                plan, model, allowed_tools, inputs, success_criteria, notification_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                workflow_id,
                name,
                json.dumps(steps),
                description,
                goal,
                json.dumps(scope) if scope else None,
                json.dumps(tags) if tags else None,
                created_by,
                plan,
                model or "haiku",
                _safe_json_dumps(allowed_tools) if allowed_tools else "[]",
                _safe_json_dumps(inputs) if inputs else "[]",
                success_criteria,
                notification_mode or "result_only",
            ),
        )
        if self._notifier:
            self._notifier.notify_change(f"Workflow registered: {workflow_id}", "workflow_register")

    def get(self, workflow_id: str) -> dict | None:
        """Get a workflow definition by ID with parsed JSON fields."""
        row = self._storage.fetchone(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        )
        if row is None:
            return None
        return self._parse_row(row)

    def list_workflows(
        self, status: str | None = None, tag: str | None = None
    ) -> list[dict]:
        """List workflow definitions, optionally filtered by status and/or tag."""
        if status:
            rows = self._storage.fetchall(
                "SELECT * FROM workflows WHERE status = ? ORDER BY name",
                (status,),
            )
        else:
            rows = self._storage.fetchall(
                "SELECT * FROM workflows ORDER BY name"
            )
        result = [self._parse_row(r) for r in rows]
        if tag:
            result = [w for w in result if tag in (w.get("tags") or [])]
        return result

    def update(
        self,
        workflow_id: str,
        steps: list[dict] | None = None,
        description: str | None = None,
        goal: str | None = None,
        scope: list[str] | None = None,
        tags: list[str] | None = None,
        plan: str | None = None,
        model: str | None = None,
        allowed_tools: list[str] | None = None,
        inputs: list[dict] | None = None,
        success_criteria: str | None = None,
        notification_mode: str | None = None,
    ) -> None:
        """Update a workflow. Increments version automatically."""
        existing = self.get(workflow_id)
        if existing is None:
            raise ValueError(f"Workflow '{workflow_id}' not found")
        new_version = existing["version"] + 1
        updates = [
            "version = ?",
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
        ]
        params: list[Any] = [new_version]
        if steps is not None:
            updates.append("steps = ?")
            params.append(json.dumps(steps))
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if goal is not None:
            updates.append("goal = ?")
            params.append(goal)
        if scope is not None:
            updates.append("scope = ?")
            params.append(json.dumps(scope))
        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags))
        if plan is not None:
            updates.append("plan = ?")
            params.append(plan)
        if model is not None:
            updates.append("model = ?")
            params.append(model)
        if allowed_tools is not None:
            updates.append("allowed_tools = ?")
            params.append(_safe_json_dumps(allowed_tools))
        if inputs is not None:
            updates.append("inputs = ?")
            params.append(_safe_json_dumps(inputs))
        if success_criteria is not None:
            updates.append("success_criteria = ?")
            params.append(success_criteria)
        if notification_mode is not None:
            updates.append("notification_mode = ?")
            params.append(notification_mode)
        params.append(workflow_id)
        self._storage.execute(
            f"UPDATE workflows SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        if self._notifier:
            self._notifier.notify_change(f"Workflow updated: {workflow_id}", "workflow_update")

    def deprecate(self, workflow_id: str) -> None:
        """Mark a workflow as deprecated."""
        self._storage.execute(
            "UPDATE workflows SET status = 'deprecated', "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (workflow_id,),
        )
        if self._notifier:
            self._notifier.notify_change(f"Workflow deprecated: {workflow_id}", "workflow_deprecate")

    def remove(self, workflow_id: str) -> None:
        """Remove a workflow definition."""
        self._storage.execute(
            "DELETE FROM workflows WHERE id = ?", (workflow_id,)
        )
        if self._notifier:
            self._notifier.notify_change(f"Workflow removed: {workflow_id}", "workflow_remove")

    def import_from_yaml(self, yaml_path: Path) -> str:
        """Import a workflow from a YAML file. Returns workflow_id."""
        data = yaml.safe_load(yaml_path.read_text())
        workflow_id = data.get("name", yaml_path.stem)
        steps = data.get("steps", [])
        existing = self.get(workflow_id)
        if existing:
            self.update(
                workflow_id,
                steps=steps,
                description=data.get("description"),
                goal=data.get("goal"),
                scope=data.get("scope"),
                tags=data.get("tags"),
                plan=data.get("plan"),
                model=data.get("model"),
                allowed_tools=data.get("allowed_tools"),
                inputs=data.get("inputs"),
            )
        else:
            self.register(
                workflow_id=workflow_id,
                name=data.get("name", workflow_id),
                steps=steps,
                description=data.get("description"),
                goal=data.get("goal"),
                scope=data.get("scope"),
                tags=data.get("tags"),
                plan=data.get("plan"),
                model=data.get("model"),
                allowed_tools=data.get("allowed_tools"),
                inputs=data.get("inputs"),
            )
        return workflow_id

    def export_to_yaml(self, workflow_id: str) -> str:
        """Export a workflow definition as YAML string."""
        wf = self.get(workflow_id)
        if wf is None:
            raise ValueError(f"Workflow '{workflow_id}' not found")
        export = {
            "name": wf["name"],
            "description": wf.get("description") or "",
            "goal": wf.get("goal") or "",
            "version": wf["version"],
            "scope": wf.get("scope") or [],
            "steps": wf["steps"],
            "tags": wf.get("tags") or [],
        }
        return yaml.dump(export, default_flow_style=False, allow_unicode=True)

    def _parse_row(self, row: dict) -> dict:
        """Parse JSON fields from a DB row."""
        result = dict(row)
        if result.get("steps"):
            result["steps"] = json.loads(result["steps"])
        if result.get("scope"):
            result["scope"] = json.loads(result["scope"])
        if result.get("tags"):
            result["tags"] = json.loads(result["tags"])
        if result.get("allowed_tools"):
            try:
                result["allowed_tools"] = json.loads(result["allowed_tools"])
            except (json.JSONDecodeError, TypeError):
                result["allowed_tools"] = []
        else:
            result["allowed_tools"] = []
        if result.get("inputs"):
            try:
                result["inputs"] = json.loads(result["inputs"])
            except (json.JSONDecodeError, TypeError):
                result["inputs"] = []
        else:
            result["inputs"] = []
        return result
