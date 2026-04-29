"""Per-session attachment storage. Files live as long as the session does.

Each session gets its own folder under sessions/<id>/attachments/.
Folders are created on first save and removed wholesale when the
session is deleted. Path-traversal-safe via sanitize_filename.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from mycelos.files.inbox import sanitize_filename


# Per-type size caps — match Anthropic's Multi-Part content limits.
SIZE_CAPS_BYTES: dict[str, int] = {
    "pdf": 32 * 1024 * 1024,
    "image": 5 * 1024 * 1024,
    "text": 10 * 1024 * 1024,
}


class SessionAttachmentStore:
    """File store scoped to a single session.

    base_dir is the parent directory (e.g. ~/.mycelos/sessions/);
    each session gets a subfolder created on first save.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def _session_dir(self, session_id: str) -> Path:
        return self._base_dir / session_id / "attachments"

    def save(self, session_id: str, data: bytes, filename: str) -> Path:
        if not data:
            raise ValueError("empty file")
        safe_name = sanitize_filename(filename)
        target_dir = self._session_dir(session_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_name
        # Path containment check
        resolved = target.resolve()
        if not resolved.is_relative_to(target_dir.resolve()):
            raise ValueError("path traversal blocked")
        # Collision handling — foo.pdf, foo-2.pdf, foo-3.pdf, ...
        if target.exists():
            stem, suffix = target.stem, target.suffix
            counter = 2
            while target.exists():
                target = target_dir / f"{stem}-{counter}{suffix}"
                counter += 1
        target.write_bytes(data)
        return target

    def list(self, session_id: str) -> list[Path]:
        """All attachments for the session, oldest first (by mtime)."""
        d = self._session_dir(session_id)
        if not d.exists():
            return []
        return sorted(
            (p for p in d.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )

    def read(self, session_id: str, filename: str) -> bytes:
        target = self._session_dir(session_id) / sanitize_filename(filename)
        return target.read_bytes()

    def delete_session(self, session_id: str) -> None:
        # Remove the entire sessions/<id>/ folder (parent of attachments/).
        # Idempotent — no error if it doesn't exist.
        d = self._session_dir(session_id).parent
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


def media_type(path: Path) -> str:
    """Map file suffix to a MIME type for Anthropic Multi-Part content."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    if suffix in (".txt", ".md"):
        return "text/plain"
    return "application/octet-stream"


def content_kind(path: Path) -> str:
    """Return 'document', 'image', 'text', or 'unsupported'.

    Only types Anthropic accepts as Multi-Part content (or that we
    inline as plain text) are supported. Anything else is rejected
    at the upload boundary.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "document"
    if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        return "image"
    if suffix in (".txt", ".md", ".csv", ".json", ".yaml", ".yml"):
        return "text"
    return "unsupported"
