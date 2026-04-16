"""Audit event logging."""

from __future__ import annotations

import json

from mycelos.protocols import StorageBackend


class SQLiteAuditLogger:
    """SQLite-based audit logger."""

    def __init__(self, storage: StorageBackend):
        self._storage = storage

    def log(
        self,
        event_type: str,
        agent_id: str | None = None,
        task_id: str | None = None,
        details: dict | None = None,
        user_id: str = "default",
        generation_id: int | None = None,
    ) -> None:
        self._storage.execute(
            """INSERT INTO audit_events
               (event_type, agent_id, task_id, user_id, details, generation_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event_type,
                agent_id,
                task_id,
                user_id,
                json.dumps(details) if details else None,
                generation_id,
            ),
        )

    def query(
        self,
        event_type: str | None = None,
        agent_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        conditions = []
        params: list = []

        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        return self._storage.fetchall(
            f"SELECT * FROM audit_events {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
