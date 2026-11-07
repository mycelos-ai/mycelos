"""Read selected local sources without changing their files.

Transcripts use internal vendor formats. Unknown record types are ignored;
malformed complete records and oversized files are explicit errors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class SourceUnit:
    external_id: str
    title: str
    content: str
    files: tuple[str, ...]
    project: str = ""
    metadata: dict = field(default_factory=dict)


EXCLUDED = {".git", ".hg", "node_modules", "vendor", ".venv", "venv", "__pycache__",
            "dist", "build", "target", ".next", ".worktrees", "worktrees"}
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_UNIT_CHARS = 4_000_000


def _paths(root: Path, extensions: set[str], extra_excluded: set[str] | None = None) -> Iterator[Path]:
    for directory, dirs, files in os.walk(root, followlinks=False,
                                         onerror=lambda error: (_ for _ in ()).throw(error)):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED | (extra_excluded or set())
                         and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix.lower() in extensions and not path.is_symlink():
                if path.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError(f"Datei zu groß: {path}")
                yield path


def _blocks(value) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    result = []
    for block in value:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"text", "input_text", "output_text"}:
            result.append(str(block.get("text", "")))
        elif block.get("type") == "tool_result":
            result.append(f"Werkzeugergebnis [{block.get('tool_use_id', '')}]: " + _blocks(block.get("content")))
        elif block.get("type") == "tool_use":
            result.append(f"Werkzeugaufruf [{block.get('id', '')}] {block.get('name', '')}: "
                          + json.dumps(block.get("input", {}), ensure_ascii=False))
    return "\n".join(result)


def _transcript(kind: str, path: Path) -> SourceUnit | None:
    messages, fallback = [], []
    session_id, project = "", ""
    seen = set()
    ancestry, line_ids = {}, {}
    branch_leaf = None
    # Only complete JSONL records are a stable read boundary while an app writes.
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.endswith("\n"):
                break
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("record is not an object")
            except (ValueError, TypeError) as error:
                raise ValueError(f"Ungültiges Protokoll: {path.name}:{number}") from error
            role, text = "", ""
            if kind == "codex":
                payload = record.get("payload") or {}
                if not isinstance(payload, dict):
                    continue
                if record.get("type") == "session_meta":
                    session_id = str(payload.get("id", session_id))
                    project = Path(payload.get("cwd") or "").name
                elif record.get("type") == "response_item":
                    if payload.get("type") == "message":
                        role = payload.get("role", "")
                        if role in {"user", "assistant"}:
                            text = _blocks(payload.get("content"))
                    elif payload.get("type") == "function_call_output":
                        role = "tool"
                        text = f"Ergebnis [{payload.get('call_id', '')}]: {payload.get('output', '')}"
                    elif payload.get("type") == "function_call":
                        role = "assistant"
                        text = (f"Werkzeugaufruf [{payload.get('call_id', '')}] {payload.get('name', '')}: "
                                f"{payload.get('arguments', '')}")
                elif record.get("type") == "event_msg":
                    if payload.get("type") in {"user_message", "agent_message"}:
                        fallback.append((number, "user" if payload["type"] == "user_message"
                                         else "assistant", str(payload.get("message", ""))))
            else:
                session_id = str(record.get("sessionId") or session_id)
                if record.get("cwd"):
                    project = Path(record["cwd"]).name
                if record.get("uuid") and "parentUuid" in record:
                    ancestry[record["uuid"]] = record.get("parentUuid")
                    line_ids[number] = record["uuid"]
                if record.get("type") in {"user", "assistant"}:
                    message = record.get("message") or {}
                    if isinstance(message, dict):
                        role = record["type"]
                        text = _blocks(message.get("content"))
                    uid = record.get("uuid")
                    if uid and uid in seen:
                        continue
                    if uid:
                        seen.add(uid)
            if text.strip():
                messages.append((number, role, text))
                if number in line_ids:
                    branch_leaf = line_ids[number]
    if kind == "claude" and branch_leaf:
        active = set()
        node = branch_leaf
        while node and node not in active:
            active.add(node)
            node = ancestry.get(node)
        messages = [m for m in messages if m[0] not in line_ids or line_ids[m[0]] in active]
    if not messages:
        messages = fallback
    if not session_id or not messages:
        return None
    content = "\n\n".join(f"[Zeile {n}; {role}]\n{text}" for n, role, text in messages)
    if len(content) > MAX_UNIT_CHARS:
        raise ValueError(f"Gespräch zu groß für diesen Import: {path.name}")
    title = f"{project or kind} · Gespräch {session_id[:12]}"
    return SourceUnit(session_id, title, content, (str(path),), project)


def _document(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            import fitz
        except ImportError as error:
            raise ValueError("PDF-Import benötigt die Dateierweiterung mycelos[files].") from error
        with fitz.open(path) as doc:
            return "\n\n".join(f"[Seite {i + 1}]\n{page.get_text()}" for i, page in enumerate(doc))
    return path.read_text(encoding="utf-8")


def read_units(kind: str, root: Path, workflow=None) -> Iterator[SourceUnit]:
    from .source_workflows import resolve_workflow, source_root, selected_paths

    workflow = resolve_workflow(kind, root, workflow)
    root = source_root(kind, root)
    if kind == "obsidian":
        from .obsidian import read_vault
        yield from read_vault(root, workflow)
        return
    projects: dict[Path, list[Path]] = {}
    for path, project in selected_paths(kind, root, workflow):
        if kind in {"codex", "claude"}:
            unit = _transcript(kind, path)
            if unit:
                yield unit
        elif project:
            projects.setdefault(project, []).append(path)
        else:
            content = _document(path)
            if content.strip():
                if len(content) > MAX_UNIT_CHARS:
                    raise ValueError(f"Dokument zu groß: {path.name}")
                external_id = path.name if kind == "file" else str(path.relative_to(root))
                yield SourceUnit(external_id, path.stem, content, (str(path),))
    for project, paths in projects.items():
        content = "\n\n".join(f"## {p.relative_to(project)}\n{_document(p)}" for p in paths)
        if len(content) > MAX_UNIT_CHARS:
            raise ValueError(f"Projekt zu groß für diesen Import: {project.name}")
        yield SourceUnit("project:" + str(project.relative_to(root)), project.name,
                         content, tuple(str(p) for p in paths), project.name)
