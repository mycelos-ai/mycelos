"""Smart Import pipeline — preserve and suggest modes.

- ``detect_import_mode`` is a pure function driven by folder count.
- ``run_preserve_import`` mirrors source structure 1:1 (added in a later task).
- ``run_suggest_import`` drops files into ``imports/<YYYY-MM-DD>/`` and
  lets the organizer classify them (added in a later task).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class FileEntry:
    relpath: str
    content: bytes


def detect_import_mode(files: list[FileEntry]) -> str:
    """Return 'preserve' if the source has >=3 distinct folders, else 'suggest'."""
    folders: set[str] = set()
    for f in files:
        if "/" in f.relpath:
            folders.add(f.relpath.rsplit("/", 1)[0])
    return "preserve" if len(folders) >= 3 else "suggest"


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _safe_filename(name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    return _SAFE_NAME_RE.sub("-", stem).strip("-").lower() or "note"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (front_dict, body). Simple key:value parser — no YAML dep."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    front_raw, body = m.group(1), m.group(2)
    front: dict = {}
    for line in front_raw.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            front[key.strip()] = val.strip().strip('"').strip("'")
    return front, body


def run_preserve_import(
    files: list[FileEntry],
    knowledge: Any,
) -> dict:
    """Mirror source folders into topics. Pure file mapping, no LLM.

    Path scheme: ``topics/<lowercased-folder-chain>/<note>``.
    Frontmatter (title, tags) is parsed out of .md files; .txt files
    are stored as-is.
    """
    created: list[str] = []
    topics_touched: set[str] = set()

    for f in files:
        if not f.relpath.lower().endswith((".md", ".txt")):
            continue
        parts = f.relpath.split("/")
        folder_chain = (
            "/".join(p.lower() for p in parts[:-1]) if len(parts) > 1 else ""
        )
        topic_path = f"topics/{folder_chain}" if folder_chain else "topics"
        topics_touched.add(topic_path)

        text = f.content.decode("utf-8", errors="replace")
        if f.relpath.lower().endswith(".md"):
            front, body = _parse_frontmatter(text)
            title = front.get("title") or parts[-1].rsplit(".", 1)[0]
            tags_raw = front.get("tags", "")
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        else:
            body = text
            title = parts[-1].rsplit(".", 1)[0]
            tags = []

        path = knowledge.write(
            title=title,
            content=body,
            tags=tags,
            topic=topic_path,
        )

        # Preserve mode trusts the source layout — mark as organized.
        knowledge._app.storage.execute(
            "UPDATE knowledge_notes SET organizer_state='ok' WHERE path=?",
            (path,),
        )
        created.append(path)

    return {
        "mode": "preserve",
        "created": created,
        "topics": list(topics_touched),
    }


def run_suggest_import(
    files: list[FileEntry],
    knowledge: Any,
) -> dict:
    """Drop files into ``imports/<YYYY-MM-DD>/`` with organizer_state='pending'.

    The caller (the API endpoint) is responsible for optionally triggering
    the organizer afterwards.
    """
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    bucket = f"imports/{today}"

    created: list[str] = []
    for f in files:
        if not f.relpath.lower().endswith((".md", ".txt")):
            continue
        text = f.content.decode("utf-8", errors="replace")
        if f.relpath.lower().endswith(".md"):
            front, body = _parse_frontmatter(text)
            title = front.get("title") or f.relpath.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        else:
            body = text
            title = f.relpath.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        path = knowledge.write(title=title, content=body, topic=bucket)
        created.append(path)

    return {
        "mode": "suggest",
        "created": created,
        "bucket": bucket,
    }
