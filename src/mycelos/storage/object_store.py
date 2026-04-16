"""Content-addressed immutable file storage.

Like Git's object store: files stored by SHA-256 hash.
Same content = same hash = stored once. Files are never deleted.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


class ObjectStore:
    """Content-addressed immutable file storage.

    Files are stored at: base_dir/objects/sha256/<hash>
    """

    def __init__(self, base_dir: Path) -> None:
        self._dir = base_dir / "objects" / "sha256"
        self._dir.mkdir(parents=True, exist_ok=True)

    def store(self, content: str) -> str:
        """Store content and return its SHA-256 hash. Idempotent."""
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        path = self._dir / content_hash
        if not path.exists():
            path.write_text(content, encoding="utf-8")
        return content_hash

    def load(self, content_hash: str) -> str | None:
        """Load content by hash. Returns None if not found."""
        path = self._dir / content_hash
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def exists(self, content_hash: str) -> bool:
        """Check if content with the given hash exists."""
        return (self._dir / content_hash).exists()

    def list_objects(self) -> list[str]:
        """List all stored object hashes."""
        if not self._dir.exists():
            return []
        return sorted(f.name for f in self._dir.iterdir() if f.is_file())
