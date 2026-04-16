"""Filesystem tools — read, write, list, manage, and analyze files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mycelos.tools.registry import ToolPermission

# --- Tool Schemas ---

FILESYSTEM_READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "filesystem_read",
        "description": (
            "Read a file from a mounted directory. "
            "The system will prompt for permission if the directory is not yet accessible."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file (must be within a mounted directory)",
                },
            },
            "required": ["path"],
        },
    },
}

FILESYSTEM_WRITE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "filesystem_write",
        "description": (
            "Write content to a file in a mounted directory. "
            "The system will prompt for permission if the directory is not yet accessible."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path for the file (must be within a write-mounted directory)",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
    },
}

FILESYSTEM_LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "filesystem_list",
        "description": "List files in a mounted directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the directory (must be within a mounted directory)",
                },
            },
            "required": ["path"],
        },
    },
}

FILE_MANAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "file_manage",
        "description": "Move, copy, or delete a file. Checks mount permissions and updates KB notes.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["move", "copy", "delete"],
                    "description": "Action to perform",
                },
                "source": {
                    "type": "string",
                    "description": "Source file path",
                },
                "destination": {
                    "type": "string",
                    "description": "Destination path (for move/copy)",
                },
            },
            "required": ["action", "source"],
        },
    },
}

FILE_ANALYZE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "file_analyze",
        "description": "Analyze a file. Checks knowledge base for existing analysis first. If not found, extracts text and analyzes.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to analyze",
                },
                "question": {
                    "type": "string",
                    "description": "Specific question about the file (optional)",
                },
            },
            "required": ["path"],
        },
    },
}


# --- Path Helpers ---

def _normalize_path(path: str) -> str:
    """Normalize a file path -- fix common LLM mistakes.

    LLMs often generate /home/username instead of /Users/username on macOS,
    or forget to expand ~. This normalizes to the actual filesystem path.
    """
    expanded = str(Path(path).expanduser())
    home = str(Path.home())
    username = Path.home().name
    wrong_homes = [f"/home/{username}", "/root"]
    for wrong in wrong_homes:
        if expanded.startswith(wrong):
            expanded = home + expanded[len(wrong):]
            break
    return expanded


# --- Sensitive File Protection ---

# Files/patterns that must NEVER be readable by agents (even if directory is mounted)
_BLOCKED_FILENAMES = frozenset({
    ".env", ".env.local", ".env.production", ".env.development",
    ".master_key", ".netrc", ".npmrc", ".pypirc",
    "credentials.json", "service-account.json",
})

_BLOCKED_PREFIXES = (
    ".env.",       # .env.* variants
)

_BLOCKED_EXTENSIONS = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".keystore",
})

_BLOCKED_DIRS = frozenset({
    ".ssh", ".gnupg", ".gpg", ".aws", ".azure", ".gcloud",
    ".config/gcloud", ".kube",
    ".mycelos",  # Data dir contains master key + encrypted credentials
})


def _is_sensitive_path(path: str) -> str | None:
    """Check if a path points to a sensitive file. Returns reason if blocked, None if OK."""
    p = Path(path)
    name = p.name.lower()

    # Exact filename match
    if name in _BLOCKED_FILENAMES:
        return f"Sensitive file blocked: {name}"

    # Prefix match (.env.*)
    for prefix in _BLOCKED_PREFIXES:
        if name.startswith(prefix):
            return f"Sensitive file blocked: {name}"

    # Extension match
    suffix = p.suffix.lower()
    if suffix in _BLOCKED_EXTENSIONS:
        return f"Sensitive file type blocked: {suffix}"

    # Directory match — any path component is a blocked dir
    parts = [part.lower() for part in p.parts]
    for blocked_dir in _BLOCKED_DIRS:
        if blocked_dir in parts:
            return f"Sensitive directory blocked: {blocked_dir}"

    return None


# --- Tool Execution ---

def execute_filesystem_read(args: dict, context: dict) -> Any:
    """Read a file -- validates path, then checks mount access."""
    from mycelos.security.mounts import MountRegistry
    from mycelos.security.permissions import PermissionRequired

    app = context["app"]
    try:
        path = _normalize_path(args.get("path", ""))
        p = Path(path).expanduser().resolve()

        # Block sensitive files (dotfiles, keys, credentials)
        blocked = _is_sensitive_path(str(p))
        if blocked:
            app.audit.log("filesystem.sensitive_blocked", details={"path": str(p), "reason": blocked})
            return {"error": blocked}

        if not p.exists():
            return {"error": f"File not found: {path}"}
        if not p.is_file():
            return {"error": f"Not a file: {path}"}
        mounts = MountRegistry(app.storage)
        if not mounts.check_access(str(p), "read"):
            parent = str(p.parent)
            raise PermissionRequired(
                tool="filesystem_read",
                action=f"mount add {parent} --rw",
                reason=f"Need read access to {p}",
                target=parent,
                action_type="mount",
                original_args={"path": path},
            )
        content = p.read_text(errors="replace")
        if len(content) > 10000:
            content = content[:10000] + "\n...(truncated)"
        return {"content": content, "path": str(p), "size": p.stat().st_size}
    except PermissionRequired:
        raise
    except Exception as e:
        return {"error": str(e)}


def execute_filesystem_write(args: dict, context: dict) -> Any:
    """Write a file -- checks mount access first."""
    from mycelos.security.mounts import MountRegistry
    from mycelos.security.permissions import PermissionRequired

    app = context["app"]
    path = args.get("path", "")
    content = args.get("content", "")

    # Block sensitive files
    blocked = _is_sensitive_path(path)
    if blocked:
        app.audit.log("filesystem.sensitive_blocked", details={"path": path, "reason": blocked, "op": "write"})
        return {"error": blocked}

    # Guard: block writing executable files from chat context
    blocked_extensions = (".py", ".sh", ".js", ".ts", ".rb", ".pl", ".bash")
    if any(path.lower().endswith(ext) for ext in blocked_extensions):
        app.audit.log(
            "filesystem.script_blocked",
            details={"path": path, "reason": "executable file from chat context"},
        )
        return {
            "error": (
                f"Cannot write executable file '{path}' from chat. "
                "Use the Creator-Agent to build proper agents instead. "
                "The Creator-Agent will test, audit, and register the agent."
            )
        }

    try:
        path = _normalize_path(path)
        mounts = MountRegistry(app.storage)
        if not mounts.check_access(path, "write"):
            parent = str(Path(path).expanduser().parent)
            raise PermissionRequired(
                tool="filesystem_write",
                action=f"mount add {parent} --rw",
                reason=f"Need write access to {path}",
                target=parent,
                action_type="mount",
                original_args={"path": path, "content": content},
            )
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        app.audit.log("filesystem.write", details={"path": str(p), "size": len(content)})
        return {"status": "written", "path": str(p), "size": len(content)}
    except PermissionRequired:
        raise
    except Exception as e:
        return {"error": str(e)}


def execute_filesystem_list(args: dict, context: dict) -> Any:
    """List files in a directory -- validates path, then checks mount access."""
    from mycelos.security.mounts import MountRegistry
    from mycelos.security.permissions import PermissionRequired

    app = context["app"]
    try:
        path = _normalize_path(args.get("path", ""))
        p = Path(path).expanduser().resolve()

        # Block sensitive directories (data dir, ssh, etc.)
        blocked = _is_sensitive_path(str(p))
        if blocked:
            app.audit.log("filesystem.sensitive_blocked", details={"path": str(p), "reason": blocked})
            return {"error": blocked}

        if not p.exists():
            return {"error": f"Directory not found: {path}"}
        if not p.is_dir():
            return {"error": f"Not a directory: {path}"}
        mounts = MountRegistry(app.storage)
        if not mounts.check_access(str(p), "read"):
            raise PermissionRequired(
                tool="filesystem_list",
                action=f"mount add {str(p)} --rw",
                reason=f"Need access to list {p}",
                target=str(p),
                action_type="mount",
                original_args={"path": path},
            )
        files = []
        for item in sorted(p.iterdir())[:100]:
            # Skip sensitive files/directories in listings
            if _is_sensitive_path(str(item)):
                continue
            files.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else 0,
            })
        return {"path": str(p), "files": files, "count": len(files)}
    except PermissionRequired:
        raise
    except Exception as e:
        return {"error": str(e)}


def execute_file_manage(args: dict, context: dict) -> Any:
    """Move, copy, or delete a file."""
    import shutil

    from mycelos.security.mounts import MountRegistry
    from mycelos.security.permissions import PermissionRequired

    app = context["app"]
    action = args.get("action")
    source_raw = args.get("source")
    if not action or not source_raw:
        return {"error": "Missing required parameter: action and source"}
    source = _normalize_path(source_raw)
    src = Path(source).expanduser().resolve()
    mounts = MountRegistry(app.storage)

    # Check source access — delete/move require write, copy/rename require read
    required_access = "write" if action in ("delete", "move") else "read"
    if not mounts.check_access(str(src), required_access):
        raise PermissionRequired(
            tool="file_manage",
            action=f"mount add {src.parent} --rw",
            reason=f"Need access to {src.name}",
            target=str(src.parent),
            original_args=args,
        )

    if action == "delete":
        if not src.exists():
            return {"error": f"File not found: {source}"}
        src.unlink()
        app.audit.log("file.deleted", details={"path": str(src)})
        return {"status": "deleted", "path": str(src)}

    # Move/Copy need destination
    dest_path = args.get("destination", "")
    if not dest_path:
        return {"error": "Destination required for move/copy"}
    dest = Path(_normalize_path(dest_path)).expanduser().resolve()
    if not mounts.check_access(str(dest.parent), "write"):
        raise PermissionRequired(
            tool="file_manage",
            action=f"mount add {dest.parent} --rw",
            reason=f"Need write access to {dest.parent}",
            target=str(dest.parent),
            original_args=args,
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    if action == "move":
        shutil.move(str(src), str(dest))
    elif action == "copy":
        shutil.copy2(str(src), str(dest))

    app.audit.log(f"file.{action}", details={"source": str(src), "destination": str(dest)})

    # Update KB notes
    try:
        kb = app.knowledge_base
        notes = kb.search(str(src), limit=5)
        for note in notes:
            if str(src) in note.get("content", ""):
                kb.update(note["path"], content=note["content"].replace(str(src), str(dest)))
    except Exception:
        pass

    return {"status": action + "d", "source": str(src), "destination": str(dest)}


def execute_file_analyze(args: dict, context: dict) -> Any:
    """Analyze a file -- check KB first, then extract text."""
    from mycelos.security.mounts import MountRegistry
    from mycelos.security.permissions import PermissionRequired

    app = context["app"]
    raw_path = args.get("path")
    if not raw_path:
        return {"error": "Missing required parameter: path"}
    path = _normalize_path(raw_path)
    p = Path(path).expanduser().resolve()

    if not p.exists():
        return {"error": f"File not found: {path}"}

    mounts = MountRegistry(app.storage)
    if not mounts.check_access(str(p), "read"):
        raise PermissionRequired(
            tool="file_analyze",
            action=f"mount add {p.parent} --rw",
            reason=f"Need read access to analyze {p.name}",
            target=str(p.parent),
            original_args=args,
        )

    # Check KB for existing analysis
    kb = app.knowledge_base
    existing = kb.search(str(p), limit=1)
    if existing and not args.get("question"):
        return {"analysis": existing[0], "source": "knowledge_base"}

    # Extract and analyze
    from mycelos.files.extractor import extract_text

    text, method = extract_text(p)
    if method == "vision_needed":
        return {
            "status": "vision_needed",
            "path": str(p),
            "message": "This file requires vision analysis. Ask the user for confirmation.",
        }
    if not text:
        return {"error": f"Could not extract text from {p.name}"}

    return {"text": text[:3000], "method": method, "path": str(p)}


# --- Registration ---

def register(registry: type) -> None:
    """Register all filesystem tools."""
    registry.register("filesystem_read", FILESYSTEM_READ_SCHEMA, execute_filesystem_read, ToolPermission.PRIVILEGED, concurrent_safe=True, category="system")
    registry.register("filesystem_write", FILESYSTEM_WRITE_SCHEMA, execute_filesystem_write, ToolPermission.PRIVILEGED, category="system")
    registry.register("filesystem_list", FILESYSTEM_LIST_SCHEMA, execute_filesystem_list, ToolPermission.PRIVILEGED, concurrent_safe=True, category="system")
    registry.register("file_manage", FILE_MANAGE_SCHEMA, execute_file_manage, ToolPermission.PRIVILEGED, category="system")
    registry.register("file_analyze", FILE_ANALYZE_SCHEMA, execute_file_analyze, ToolPermission.PRIVILEGED, concurrent_safe=True, category="system")
