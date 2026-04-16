"""Inbox management — save, list, clean, filename sanitization."""

from __future__ import annotations

import re
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("mycelos.files")

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


def sanitize_filename(name: str) -> str:
    """Sanitize filename to prevent path traversal.

    Strips path components, removes dangerous chars, truncates.
    """
    name = Path(name).name  # Strip directory components
    name = re.sub(r'[^\w\-.]', '_', name)
    name = name.replace('..', '_')
    name = name.replace('\x00', '')
    name = name[:200]
    if not name or name in ('.', '..', '_'):
        name = 'unnamed_file'
    return name


class InboxManager:
    """Manages the file inbox — temporary staging for incoming files."""

    def __init__(self, inbox_dir: Path, max_size_bytes: int = MAX_FILE_SIZE):
        self._inbox_dir = inbox_dir
        self._inbox_dir.mkdir(parents=True, exist_ok=True)
        self._max_size = max_size_bytes

    def save(self, data: bytes, filename: str) -> Path:
        """Save file to inbox. Returns the saved path.

        Raises ValueError if file too large or path traversal detected.
        """
        if len(data) > self._max_size:
            raise ValueError(f"File too large ({len(data)} bytes > {self._max_size})")

        safe_name = sanitize_filename(filename)
        date_prefix = datetime.now().strftime("%Y-%m-%d")
        target = self._inbox_dir / f"{date_prefix}_{safe_name}"

        # Containment check
        resolved = target.resolve()
        if not resolved.is_relative_to(self._inbox_dir.resolve()):
            raise ValueError("Path traversal blocked")

        # Duplicate handling
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            counter = 2
            while target.exists():
                target = self._inbox_dir / f"{stem}-{counter}{suffix}"
                counter += 1

        target.write_bytes(data)
        logger.debug("Saved to inbox: %s (%d bytes)", target.name, len(data))
        return target

    def list_files(self) -> list[Path]:
        """List all files in inbox (not directories)."""
        if not self._inbox_dir.exists():
            return []
        return sorted(f for f in self._inbox_dir.iterdir() if f.is_file())

    def remove(self, path: Path) -> bool:
        """Remove a file from inbox. Returns True if removed."""
        resolved = path.resolve()
        if not resolved.is_relative_to(self._inbox_dir.resolve()):
            return False
        if resolved.exists() and resolved.is_file():
            resolved.unlink()
            return True
        return False

    def get_path(self, filename: str) -> Path | None:
        """Find a file in inbox by name (partial match)."""
        for f in self.list_files():
            if filename in f.name:
                return f
        return None
