"""Note dataclass with frontmatter parsing and rendering."""

from __future__ import annotations

import re
import yaml
from dataclasses import dataclass, field


@dataclass
class Note:
    """A knowledge base note with YAML frontmatter support."""

    title: str
    content: str = ""
    type: str = "note"
    tags: list[str] = field(default_factory=list)
    status: str = "active"
    priority: int = 0
    due: str | None = None
    links: list[str] = field(default_factory=list)
    reminder: bool = False
    parent_path: str = ""
    created_at: str = ""
    updated_at: str = ""
    path: str = ""

    _TYPE_FOLDERS: dict[str, str] = field(default_factory=lambda: {
        "note": "notes",
        "task": "tasks",
        "decision": "decisions",
        "reference": "references",
        "fact": "facts",
        "journal": "journal",
    }, init=False, repr=False, compare=False)

    def generate_path(self) -> str:
        """Creates slug from title + type-based folder."""
        folders = {
            "note": "notes",
            "task": "tasks",
            "decision": "decisions",
            "reference": "references",
            "fact": "facts",
            "journal": "journal",
            "topic": "topics",
        }
        folder = folders.get(self.type, "notes")
        slug = re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")
        return f"{folder}/{slug}"


def render_note(note: Note) -> str:
    """Renders a Note to Markdown with YAML frontmatter."""
    frontmatter: dict = {
        "title": note.title,
        "type": note.type,
        "status": note.status,
        "priority": note.priority,
        "tags": note.tags,
        "due": note.due,
    }
    if note.reminder:
        frontmatter["reminder"] = True
    if note.parent_path:
        frontmatter["parent_path"] = note.parent_path
    if note.links:
        frontmatter["links"] = note.links
    if note.created_at:
        frontmatter["created_at"] = note.created_at
    if note.updated_at:
        frontmatter["updated_at"] = note.updated_at
    if note.path:
        frontmatter["path"] = note.path

    yaml_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f"---\n{yaml_str}---\n\n{note.content}"


def parse_frontmatter(markdown: str) -> Note:
    """Parses Markdown with YAML frontmatter into a Note."""
    if not markdown.startswith("---"):
        return Note(title="", content=markdown)

    # Find the closing ---
    end = markdown.find("\n---", 3)
    if end == -1:
        return Note(title="", content=markdown)

    yaml_str = markdown[3:end].strip()
    content = markdown[end + 4:].lstrip("\n")

    try:
        data = yaml.safe_load(yaml_str) or {}
    except yaml.YAMLError:
        return Note(title="", content=markdown)

    return Note(
        title=data.get("title", ""),
        content=content,
        type=data.get("type", "note"),
        tags=data.get("tags") or [],
        status=data.get("status", "active"),
        priority=data.get("priority", 0),
        due=data.get("due"),
        links=data.get("links") or [],
        reminder=bool(data.get("reminder", False)),
        parent_path=data.get("parent_path", ""),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        path=data.get("path", ""),
    )
