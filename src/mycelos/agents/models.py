"""Agent data models — AgentInput and AgentOutput.

These are the data contracts between the system and agents.
Every agent receives AgentInput and returns AgentOutput.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentInput:
    """What an agent receives as input."""

    task_goal: str
    task_inputs: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentOutput:
    """What an agent returns as output."""

    success: bool
    result: Any
    artifacts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
