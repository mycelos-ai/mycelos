"""Workflow data models — Workflow and WorkflowStep."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowStep:
    """A single step in a workflow."""

    id: str
    action: str
    agent: str
    policy: str
    model_tier: str = "haiku"
    condition: str | None = None
    on_empty: str | None = None
    inputs: list[dict[str, str]] = field(default_factory=list)
    outputs: list[dict[str, str]] = field(default_factory=list)
    evaluation: dict[str, Any] = field(default_factory=dict)
    max_cost: float | None = None
    notification: dict[str, Any] = field(default_factory=dict)


@dataclass
class Workflow:
    """A complete workflow definition."""

    name: str
    steps: list[WorkflowStep]
    description: str = ""
    goal: str = ""
    version: int = 1
    scope: list[str] = field(default_factory=list)
    mcps: list[str] = field(default_factory=list)
    instructions: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    inputs: list[dict] = field(default_factory=list)
