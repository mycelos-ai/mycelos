"""Memory Management Service with scoped access."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from mycelos.protocols import StorageBackend


class SQLiteMemoryService:
    """SQLite-based memory service with four scoped layers.

    Scopes: system, agent, shared, session.
    All entries are user-scoped via user_id.
    """

    def __init__(self, storage: StorageBackend):
        self._storage = storage

    def get(
        self,
        user_id: str,
        scope: str,
        key: str,
        agent_id: str | None = None,
    ) -> Any:
        now = datetime.now(timezone.utc).isoformat()
        if agent_id:
            row = self._storage.fetchone(
                """SELECT value FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND key = ? AND agent_id = ?
                   AND (expires_at IS NULL OR expires_at > ?)""",
                (user_id, scope, key, agent_id, now),
            )
        else:
            row = self._storage.fetchone(
                """SELECT value FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND key = ? AND agent_id IS NULL
                   AND (expires_at IS NULL OR expires_at > ?)""",
                (user_id, scope, key, now),
            )
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]

    def set(
        self,
        user_id: str,
        scope: str,
        key: str,
        value: Any,
        agent_id: str | None = None,
        created_by: str = "system",
    ) -> None:
        serialized = json.dumps(value) if not isinstance(value, str) else value
        now = datetime.now(timezone.utc).isoformat()

        # SQLite treats NULL as unique in UNIQUE constraints, so we use
        # an explicit check-then-update/insert pattern for NULL agent_id
        existing = None
        if agent_id:
            existing = self._storage.fetchone(
                """SELECT id FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND agent_id = ? AND key = ?""",
                (user_id, scope, agent_id, key),
            )
        else:
            existing = self._storage.fetchone(
                """SELECT id FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND agent_id IS NULL AND key = ?""",
                (user_id, scope, key),
            )

        if existing:
            self._storage.execute(
                """UPDATE memory_entries SET value = ?, updated_at = ?
                   WHERE id = ?""",
                (serialized, now, existing["id"]),
            )
        else:
            self._storage.execute(
                """INSERT INTO memory_entries (user_id, scope, agent_id, key, value, created_by)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, scope, agent_id, key, serialized, created_by),
            )

    def search(
        self,
        user_id: str,
        scope: str,
        query: str,
        agent_id: str | None = None,
    ) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        if agent_id:
            rows = self._storage.fetchall(
                """SELECT key, value, created_by, created_at FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND agent_id = ? AND key LIKE ?
                   AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY created_at DESC""",
                (user_id, scope, agent_id, f"%{query}%", now),
            )
        else:
            rows = self._storage.fetchall(
                """SELECT key, value, created_by, created_at FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND key LIKE ?
                   AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY created_at DESC""",
                (user_id, scope, f"%{query}%", now),
            )
        return rows

    def cleanup_expired(self) -> int:
        """Delete all expired memory entries. Returns count deleted."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._storage.execute(
            "DELETE FROM memory_entries WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        return cursor.rowcount

    def delete(
        self,
        user_id: str,
        scope: str,
        key: str,
        agent_id: str | None = None,
    ) -> bool:
        if agent_id:
            cursor = self._storage.execute(
                """DELETE FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND key = ? AND agent_id = ?""",
                (user_id, scope, key, agent_id),
            )
        else:
            cursor = self._storage.execute(
                """DELETE FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND key = ? AND agent_id IS NULL""",
                (user_id, scope, key),
            )
        return cursor.rowcount > 0
