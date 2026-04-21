"""SQLite index manager for knowledge notes using FTS5."""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mycelos.storage.database import SQLiteStorage


class KnowledgeIndexer:
    """Manages the SQLite FTS5 index and metadata for knowledge notes."""

    def __init__(self, storage: "SQLiteStorage") -> None:
        self._storage = storage

    def ensure_fts(self) -> None:
        """Create FTS5 table if it doesn't exist.

        Uses a standalone FTS5 table (no content= backing) so that INSERT/DELETE
        operations work without SQLite trigger complications.
        """
        try:
            self._storage.execute("SELECT 1 FROM knowledge_fts LIMIT 0")
        except Exception:
            self._storage.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                    title, content, tags
                );
            """)

    def index_note(
        self,
        path: str,
        title: str,
        type: str,
        status: str,
        tags: str,
        priority: int,
        due: str | None,
        content_hash: str,
        content: str = "",
        parent_path: str | None = None,
        reminder: bool = False,
        remind_via: str | None = None,
        source_file: str | None = None,
    ) -> int:
        """INSERT or UPDATE knowledge_notes row and sync FTS5 index.

        Returns the note's rowid.
        """
        existing = self._storage.fetchone(
            "SELECT id FROM knowledge_notes WHERE path = ?", (path,)
        )
        if existing:
            note_id = existing["id"]
            self._storage.execute(
                """UPDATE knowledge_notes
                   SET title=?, type=?, status=?, tags=?, priority=?, due=?,
                       content_hash=?, parent_path=?, reminder=?, remind_via=?,
                       source_file=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                   WHERE path=?""",
                (title, type, status, tags, priority, due, content_hash,
                 parent_path, reminder, remind_via, source_file, path),
            )
            # Sync FTS5
            self._storage.execute(
                "DELETE FROM knowledge_fts WHERE rowid = ?", (note_id,)
            )
            self._storage.execute(
                "INSERT INTO knowledge_fts(rowid, title, content, tags) VALUES (?, ?, ?, ?)",
                (note_id, title, content, _tags_string(tags)),
            )
        else:
            cursor = self._storage.execute(
                """INSERT INTO knowledge_notes
                   (path, title, type, status, tags, priority, due, content_hash,
                    parent_path, reminder, remind_via, source_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (path, title, type, status, tags, priority, due, content_hash,
                 parent_path, reminder, remind_via, source_file),
            )
            note_id = cursor.lastrowid
            self._storage.execute(
                "INSERT OR REPLACE INTO knowledge_fts(rowid, title, content, tags) VALUES (?, ?, ?, ?)",
                (note_id, title, content, _tags_string(tags)),
            )
        return note_id

    def remove_note(self, path: str) -> None:
        """Delete a note from index and FTS5."""
        existing = self._storage.fetchone(
            "SELECT id FROM knowledge_notes WHERE path = ?", (path,)
        )
        if existing:
            note_id = existing["id"]
            self._storage.execute(
                "DELETE FROM knowledge_fts WHERE rowid = ?", (note_id,)
            )
        self._storage.execute(
            "DELETE FROM knowledge_notes WHERE path = ?", (path,)
        )

    def get_note_meta(self, path: str) -> dict | None:
        """Fetch row from knowledge_notes by path."""
        return self._storage.fetchone(
            "SELECT * FROM knowledge_notes WHERE path = ?", (path,)
        )

    def get_note_id(self, path: str) -> int | None:
        """Return the integer primary key for a note path, or None if not found."""
        row = self._storage.fetchone(
            "SELECT id FROM knowledge_notes WHERE path = ?", (path,)
        )
        return row["id"] if row else None

    def search_fts(
        self,
        query: str,
        type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """FTS5 full-text search with optional type filter.

        Supports prefix matching: "eli" matches "elias".
        Multiple words are AND-connected: "elias reise" matches both words.
        """
        # Add prefix matching (*) to each word for partial search
        words = query.strip().split()
        fts_query = " ".join(f'"{w}"*' for w in words if w)
        if not fts_query:
            return []

        sql = """
            SELECT kn.path, kn.title, kn.type, kn.status, kn.priority, kn.due,
                   kn.tags, kn.created_at, kn.updated_at
            FROM knowledge_fts fts
            JOIN knowledge_notes kn ON kn.id = fts.rowid
            WHERE knowledge_fts MATCH ?
        """
        params: list = [fts_query]
        if type:
            sql += " AND kn.type = ?"
            params.append(type)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            return self._storage.fetchall(sql, tuple(params))
        except Exception:
            return []

    def search_like(
        self,
        query: str,
        type: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Fallback LIKE search on title, path, and tags — catches typos and partial words."""
        pattern = f"%{query}%"
        sql = """
            SELECT path, title, type, status, priority, due,
                   tags, created_at, updated_at
            FROM knowledge_notes
            WHERE (title LIKE ? OR path LIKE ? OR tags LIKE ?)
        """
        params: list = [pattern, pattern, pattern]
        if type:
            sql += " AND type = ?"
            params.append(type)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return self._storage.fetchall(sql, tuple(params))

    def list_notes(
        self,
        type: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        due: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Filtered query against knowledge_notes."""
        conditions: list[str] = []
        params: list = []

        if type:
            conditions.append("type = ?")
            params.append(type)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if due == "overdue":
            today = date.today().isoformat()
            conditions.append("due < ?")
            conditions.append("due IS NOT NULL")
            params.append(today)
        elif due == "today":
            today = date.today().isoformat()
            conditions.append("due = ?")
            params.append(today)
        elif due == "this_week":
            from datetime import timedelta
            today = date.today()
            week_end = (today + timedelta(days=7 - today.weekday())).isoformat()
            conditions.append("due >= ?")
            conditions.append("due <= ?")
            params.append(today.isoformat())
            params.append(week_end)
        elif due:
            conditions.append("due = ?")
            params.append(due)

        sql = "SELECT path, title, type, status, priority, due, tags, parent_path, reminder, organizer_state, created_at, updated_at FROM knowledge_notes"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return self._storage.fetchall(sql, tuple(params))

    def set_parent(self, path: str, parent_path: str | None) -> bool:
        """Set the parent_path for a note. Returns False if note not found."""
        existing = self._storage.fetchone(
            "SELECT id FROM knowledge_notes WHERE path = ?", (path,)
        )
        if not existing:
            return False
        self._storage.execute(
            """UPDATE knowledge_notes SET parent_path = ?,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
               WHERE path = ?""",
            (parent_path, path),
        )
        return True

    def set_reminder(
        self,
        path: str,
        due: str,
        reminder: bool = True,
        remind_at: str | None = None,
    ) -> bool:
        """Set due date, reminder flag, and optional full-datetime remind_at.

        ``due`` is the day-granularity task deadline; ``remind_at`` is the
        exact ISO datetime at which the scheduler should fire. Passing
        ``remind_at=None`` explicitly clears any previously-configured
        datetime so callers can drop back to "fire any time on the due
        date" by omitting the argument.
        """
        existing = self._storage.fetchone(
            "SELECT id FROM knowledge_notes WHERE path = ?", (path,)
        )
        if not existing:
            return False
        self._storage.execute(
            """UPDATE knowledge_notes SET due = ?, reminder = ?, remind_at = ?,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
               WHERE path = ?""",
            (due, reminder, remind_at, path),
        )
        return True

    def list_children(self, parent_path: str, limit: int = 100) -> list[dict]:
        """List notes that belong to a topic (by parent_path)."""
        return self._storage.fetchall(
            """SELECT path, title, type, status, priority, due, tags,
                      created_at, updated_at, sort_order
               FROM knowledge_notes
               WHERE parent_path = ?
               ORDER BY sort_order, updated_at DESC
               LIMIT ?""",
            (parent_path, limit),
        )

    def list_topics(self, limit: int = 100, top_level_only: bool = False) -> list[dict]:
        """List topic notes. If top_level_only, exclude sub-topics."""
        if top_level_only:
            return self._storage.fetchall(
                """SELECT path, title, type, status, priority, due, tags,
                          created_at, updated_at, parent_path
                   FROM knowledge_notes
                   WHERE type = 'topic'
                     AND (parent_path IS NULL OR parent_path = ''
                          OR parent_path NOT IN (SELECT path FROM knowledge_notes WHERE type = 'topic'))
                   ORDER BY title
                   LIMIT ?""",
                (limit,),
            )
        return self._storage.fetchall(
            """SELECT path, title, type, status, priority, due, tags,
                      created_at, updated_at, parent_path
               FROM knowledge_notes
               WHERE type = 'topic'
               ORDER BY title
               LIMIT ?""",
            (limit,),
        )

    def add_link(self, from_path: str, to_path: str) -> None:
        """Insert a link between two notes."""
        self._storage.execute(
            "INSERT OR IGNORE INTO knowledge_links(from_path, to_path) VALUES (?, ?)",
            (from_path, to_path),
        )

    def replace_links(self, from_path: str, to_paths: list[str]) -> None:
        """Replace all outbound links for a note with a fresh set."""
        self._storage.execute(
            "DELETE FROM knowledge_links WHERE from_path = ?", (from_path,)
        )
        for to_path in to_paths:
            self.add_link(from_path, to_path)

    def get_backlinks(self, path: str) -> list[str]:
        """Return all paths that link to the given path."""
        rows = self._storage.fetchall(
            "SELECT from_path FROM knowledge_links WHERE to_path = ?", (path,)
        )
        return [row["from_path"] for row in rows]

    def get_outgoing_links(self, path: str) -> list[str]:
        """Return all links a note points to."""
        rows = self._storage.fetchall(
            "SELECT to_path FROM knowledge_links WHERE from_path = ?", (path,)
        )
        return [row["to_path"] for row in rows]

    def list_links(self) -> list[dict]:
        """Return all links for graph rendering."""
        return self._storage.fetchall(
            "SELECT from_path, to_path FROM knowledge_links ORDER BY from_path, to_path"
        )


def _tags_string(tags_json: str) -> str:
    """Convert JSON tags array to space-separated string for FTS5."""
    try:
        tags = json.loads(tags_json)
        if isinstance(tags, list):
            return " ".join(str(t) for t in tags)
    except (json.JSONDecodeError, TypeError):
        pass
    return tags_json or ""
