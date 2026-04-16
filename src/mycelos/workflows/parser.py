"""Workflow YAML Parser — loads and validates workflow definitions."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mycelos.workflows.models import Workflow, WorkflowStep


class WorkflowParser:
    """Parses workflow YAML files into Workflow objects."""

    def parse_file(self, path: Path) -> Workflow:
        """Parse a single YAML file into a Workflow.

        Args:
            path: Path to the YAML file.

        Returns:
            Parsed Workflow object.

        Raises:
            ValueError: If the YAML content is invalid.
        """
        return self.parse_string(path.read_text())

    def parse_string(self, yaml_str: str) -> Workflow:
        """Parse a YAML string into a Workflow.

        Args:
            yaml_str: Raw YAML content.

        Returns:
            Parsed Workflow object.

        Raises:
            ValueError: If the YAML content is invalid.
        """
        data = yaml.safe_load(yaml_str)
        if not isinstance(data, dict):
            raise ValueError("Workflow YAML must be a mapping")
        return self._build_workflow(data)

    def load_directory(self, directory: Path) -> list[Workflow]:
        """Load all .yaml files from a directory.

        Args:
            directory: Directory containing YAML workflow files.

        Returns:
            List of parsed Workflow objects, sorted by filename.
        """
        return [self.parse_file(p) for p in sorted(directory.glob("*.yaml"))]

    def _build_workflow(self, data: dict[str, Any]) -> Workflow:
        """Build a Workflow from parsed YAML data.

        Args:
            data: Dictionary from YAML parsing.

        Returns:
            Workflow object.

        Raises:
            ValueError: If required fields are missing.
        """
        if "name" not in data:
            raise ValueError("Workflow must have a 'name' field")
        if "steps" not in data or not data["steps"]:
            raise ValueError("Workflow must have non-empty 'steps'")
        steps = [self._build_step(s) for s in data["steps"]]
        return Workflow(
            name=data["name"],
            steps=steps,
            description=data.get("description", ""),
            goal=data.get("goal", ""),
            version=data.get("version", 1),
            scope=data.get("scope", []),
            mcps=data.get("mcps", []),
            instructions=data.get("instructions", ""),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )

    def _build_step(self, data: dict[str, Any]) -> WorkflowStep:
        """Build a WorkflowStep from parsed YAML data.

        Args:
            data: Dictionary from YAML parsing.

        Returns:
            WorkflowStep object.

        Raises:
            ValueError: If required fields are missing.
        """
        if "id" not in data:
            raise ValueError("Step must have 'id'")
        if "agent" not in data:
            raise ValueError("Step must have 'agent'")
        if "policy" not in data:
            raise ValueError("Step must have 'policy'")
        return WorkflowStep(
            id=data["id"],
            action=data.get("action", ""),
            agent=data["agent"],
            policy=data["policy"],
            model_tier=data.get("model_tier", "haiku"),
            condition=data.get("condition"),
            on_empty=data.get("on_empty"),
            inputs=data.get("inputs", []),
            outputs=data.get("outputs", []),
            evaluation=data.get("evaluation", {}),
            max_cost=data.get("max_cost"),
            notification=data.get("notification", {}),
        )
