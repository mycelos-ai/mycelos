"""Open Knowledge Format (OKF v0.1) export — the boundary serializer.

OKF is a *boundary format* (Decision D1): the internal ``Note`` + SQLite index
stay authoritative. All OKF knowledge lives in this one file, so a future spec
bump touches a single serializer rather than the core.

What this module does NOT do (PoC scope):
- No import (a later extension of ``run_preserve_import``).
- No binary ``documents/`` blobs — text notes only.
- No ``log.md`` synthesis — journal notes export as ordinary files.

Mapping to OKF v0.1 (Decision D3 — additive, never removes Mycelos keys):
- ``type``       — required by OKF; always emitted.
- ``title``      — passthrough.
- ``description``— derived from the first non-heading paragraph of the body.
- ``tags``       — passthrough.
- ``timestamp``  — ``updated_at`` or, failing that, ``created_at``.
- ``resource``   — ``source.url`` or ``source.filename`` when present.
Mycelos-specific keys (``status``, ``priority``, ``parent_path``, ``links``,
``created_by``, ``source``) pass through unchanged so the bundle can round-trip
back into Mycelos.
"""
from __future__ import annotations

from typing import Callable

import yaml

# Keys copied verbatim from the note dict into OKF frontmatter when present.
# These keep the bundle round-trippable back into a Mycelos Note.
_PASSTHROUGH_KEYS = (
    "status",
    "priority",
    "parent_path",
    "links",
    "created_by",
    "source",
)


def _derive_description(content: str) -> str:
    """First non-heading, non-empty paragraph of the body (single line).

    Returns "" when the body is empty or contains only headings.
    """
    for block in (content or "").split("\n\n"):
        line = block.strip()
        if not line or line.startswith("#"):
            continue
        # Collapse a multi-line paragraph into one line.
        return " ".join(part.strip() for part in line.splitlines() if part.strip())
    return ""


def note_to_okf_frontmatter(note: dict) -> dict:
    """Map a Mycelos note dict to an OKF frontmatter dict.

    ``type`` is always present (OKF's only required field). The mapping is
    additive: existing Mycelos keys are preserved alongside the OKF aliases.
    """
    fm: dict = {
        # OKF requires `type`. Default to "note" if a caller omits it.
        "type": note.get("type") or "note",
        "title": note.get("title", ""),
    }

    description = _derive_description(note.get("content", ""))
    if description:
        fm["description"] = description

    tags = note.get("tags")
    if tags:
        fm["tags"] = tags

    timestamp = note.get("updated_at") or note.get("created_at")
    if timestamp:
        fm["timestamp"] = timestamp

    source = note.get("source")
    if isinstance(source, dict):
        resource = source.get("url") or source.get("filename")
        if resource:
            fm["resource"] = resource

    for key in _PASSTHROUGH_KEYS:
        value = note.get(key)
        # Keep falsy-but-meaningful values like priority=0 out only when the
        # key is genuinely absent; pass through anything explicitly provided.
        if value not in (None, "", [], {}):
            fm[key] = value

    return fm


def _render_okf_file(note: dict) -> str:
    """Render one note as OKF markdown: YAML frontmatter + body."""
    frontmatter = note_to_okf_frontmatter(note)
    yaml_str = yaml.dump(
        frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False
    )
    body = note.get("content", "")
    return f"---\n{yaml_str}---\n\n{body}"


def _okf_dir_of(row: dict) -> str:
    """The OKF directory a note belongs in.

    Membership is carried by ``parent_path`` on the list row — KnowledgeBase
    lays every note out at ``notes/<slug>`` regardless of topic, so the file
    path is NOT the membership signal. A topic note itself anchors its own
    directory (derived from its stored path); a plain note lives under its
    topic's directory, or at the root when it has no topic.
    """
    if row.get("type") == "topic":
        # A topic anchors a directory named after its own slug, nested under
        # its parent topic if any. Its stored path already encodes that.
        return row["path"]
    parent = row.get("parent_path")
    return parent or ""


def _slug_of(path: str) -> str:
    """Final path segment — the note's own filename slug."""
    return path.rsplit("/", 1)[-1]


def _render_index(
    title: str,
    children: list[dict],
    subdirs: list[str] | None = None,
) -> str:
    """Render a navigation ``index.md`` listing children as markdown links.

    ``subdirs`` are relative directory paths (topics) linked to their own
    ``index.md`` so the root entry point surfaces the topic tree, not just
    loose notes.
    """
    lines = [f"# {title}", ""]
    has_entries = False
    for sub in sorted(subdirs or []):
        lines.append(f"- [{sub}/]({sub}/index.md)")
        has_entries = True
    for child in sorted(children, key=lambda c: c.get("slug", "")):
        link_title = child.get("title") or child["slug"]
        lines.append(f"- [{link_title}]({child['slug']}.md)")
        has_entries = True
    if not has_entries:
        lines.append("_No entries._")
    lines.append("")
    return "\n".join(lines)


def build_okf_bundle(
    notes: list[dict],
    read_fn: Callable[[str], dict | None],
) -> dict[str, str]:
    """Build an in-memory OKF bundle: ``{relative_path: file_contents}``.

    Args:
        notes: list rows from ``list_notes`` — each must carry ``path`` and
            (for membership) ``parent_path``. Archived notes should already be
            filtered out by the caller.
        read_fn: callback returning the full note dict (frontmatter + content)
            for a path, or ``None`` if it cannot be read. A note that reads as
            ``None`` is skipped, not fatal. ``read_fn`` need not return
            ``parent_path`` — membership is taken from the list row.

    The OKF directory layout reflects topic membership (``parent_path``), not
    the internal file path. The same dict is consumed by both surfaces (CLI
    writes it to disk, the API zips it) so the bytes are identical.
    """
    bundle: dict[str, str] = {}

    # Group children by their OKF directory so we can synthesize an index.md
    # per topic directory and at the root.
    children_by_dir: dict[str, list[dict]] = {}

    for row in notes:
        path = row.get("path")
        if not path:
            continue
        full = read_fn(path)
        if full is None:
            continue
        full.setdefault("path", path)

        directory = _okf_dir_of(row)
        slug = _slug_of(path)
        relpath = f"{directory}/{slug}.md" if directory else f"{slug}.md"
        bundle[relpath] = _render_okf_file(full)

        children_by_dir.setdefault(directory, []).append(
            {"slug": slug, "title": full.get("title")}
        )

    # Per-topic-directory index.md (navigation). The root index is emitted
    # separately below so it always exists, even for an empty bundle.
    for directory, children in children_by_dir.items():
        if directory == "":
            continue
        title = directory.rsplit("/", 1)[-1].replace("-", " ").title()
        bundle[f"{directory}/index.md"] = _render_index(title, children)

    # Root index.md always present. List the topic directories that have their
    # own index.md so the entry point surfaces the topic tree, plus any loose
    # root notes.
    indexed_dirs = sorted(d for d in children_by_dir if d)
    bundle["index.md"] = _render_index(
        "Knowledge Base", children_by_dir.get("", []), subdirs=indexed_dirs
    )

    return bundle
