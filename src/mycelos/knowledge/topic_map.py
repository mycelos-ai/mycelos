"""Build a Mermaid graph of a topic and its sub-notes.

Embedded into topic-index markdown files by
``KnowledgeBase.regenerate_topic_indexes``. Manually-authored mermaid
blocks in any other note are untouched.
"""
from __future__ import annotations

from typing import Any


def _node_id(path: str) -> str:
    safe = path.replace("/", "_").replace("-", "_").replace(".", "_")
    return "n_" + safe


def _label(title: str | None, fallback: str) -> str:
    text = title or fallback
    return text.replace('"', "'")[:40]


def build_topic_mermaid(topic_path: str, knowledge: Any) -> str:
    """Return a fenced mermaid code block, or '' if the topic has no sub-notes."""
    children = knowledge.list_children(topic_path)
    if not children:
        return ""

    lines: list[str] = ["graph TD"]
    topic_node = _node_id(topic_path)
    topic_label = topic_path.rsplit("/", 1)[-1] or topic_path
    lines.append(f'  {topic_node}(("{topic_label}"))')

    note_ids: dict[str, str] = {}
    for child in children:
        path = child.get("path") or ""
        if not path:
            continue
        nid = _node_id(path)
        note_ids[path] = nid
        lines.append(f'  {nid}["{_label(child.get("title"), path.rsplit("/", 1)[-1])}"]')
        lines.append(f"  {topic_node} --> {nid}")

    for child in children:
        path = child.get("path") or ""
        if not path or path not in note_ids:
            continue
        md_path = knowledge._knowledge_dir / (path + ".md")
        if not md_path.exists():
            continue
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception:
            continue
        for link in knowledge._extract_wikilinks(text):
            if link in note_ids and link != path:
                lines.append(f"  {note_ids[path]} --> {note_ids[link]}")

    body = "\n".join(lines)
    fenced = f"```mermaid\n{body}\n```\n"

    if len(children) >= 16:
        return f"<details><summary>Topic Map</summary>\n\n{fenced}\n</details>\n"
    return fenced
