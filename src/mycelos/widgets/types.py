"""Widget IR — channel-agnostic typed UI primitives.

Widgets are frozen dataclasses that represent structured output.
Agents produce widgets; channel renderers translate them to
platform-specific formats (Rich, Telegram Markdown, Slack Block Kit, HTML).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# --- Atomic Widgets ---

@dataclass(frozen=True)
class TextBlock:
    """Formatted text with optional weight."""
    text: str
    weight: Literal["normal", "bold", "italic"] = "normal"


@dataclass(frozen=True)
class Table:
    """Tabular data with headers and rows."""
    headers: list[str]
    rows: list[list[str]]


@dataclass(frozen=True)
class Choice:
    """A single option in a ChoiceBox."""
    id: str
    label: str


@dataclass(frozen=True)
class ChoiceBox:
    """Interactive selection — buttons, numbered list, or dropdown."""
    prompt: str
    options: list[Choice]


@dataclass(frozen=True)
class StatusCard:
    """Key-value summary card with semantic style."""
    title: str
    facts: dict[str, str]
    style: Literal["info", "success", "warning", "error"] = "info"


@dataclass(frozen=True)
class ProgressBar:
    """Progress indicator."""
    label: str
    current: int
    total: int

    @property
    def percentage(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.current / self.total) * 100


@dataclass(frozen=True)
class CodeBlock:
    """Code with optional syntax highlighting."""
    code: str
    language: str = "text"


@dataclass(frozen=True)
class Confirm:
    """Yes/No confirmation prompt."""
    prompt: str
    danger: bool = False


@dataclass(frozen=True)
class ImageBlock:
    """Image reference with alt text."""
    url: str
    alt: str
    caption: str | None = None


# --- Container ---

Widget = TextBlock | Table | ChoiceBox | StatusCard | ProgressBar | CodeBlock | Confirm | ImageBlock


@dataclass(frozen=True)
class Compose:
    """Container for multiple widgets in a single response."""
    children: list[Widget | Compose]
