"""Filesystem Mount Registry — scoped directory access for agents.

Mounts define which directories agents/workflows can access.
Each mount has: path, access level (read/write/read_write),
and optional scope (specific agent or workflow).

Part of the NixOS State — rollbackable.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from mycelos.protocols import StorageBackend

VALID_ACCESS = {"read", "write", "read_write"}


class MountRegistry:
    """Manages filesystem mount permissions."""

    def __init__(self, storage: StorageBackend, notifier=None) -> None:
        self._storage = storage
        self._notifier = notifier

    def add(
        self,
        path: str,
        access: str = "read",
        purpose: str | None = None,
        agent_id: str | None = None,
        workflow_id: str | None = None,
        user_id: str = "default",
    ) -> str:
        """Add a new mount. Returns mount ID."""
        if access not in VALID_ACCESS:
            raise ValueError(f"Invalid access: '{access}'. Must be one of: {VALID_ACCESS}")

        # Expand ~ and resolve
        expanded = str(Path(path).expanduser().resolve())

        mount_id = str(uuid.uuid4())
        self._storage.execute(
            """INSERT INTO mounts (id, path, access, purpose, agent_id, workflow_id, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (mount_id, expanded, access, purpose, agent_id, workflow_id, user_id),
        )
        if self._notifier:
            self._notifier.notify_change(f"Mount added: {expanded} ({access})", "mount_add")
        return mount_id

    def get(self, mount_id: str) -> dict | None:
        row = self._storage.fetchone("SELECT * FROM mounts WHERE id = ?", (mount_id,))
        return dict(row) if row else None

    def list_mounts(
        self,
        status: str | None = "active",
        agent_id: str | None = None,
        user_id: str = "default",
    ) -> list[dict]:
        """List mounts, optionally filtered."""
        conditions = ["user_id = ?"]
        params: list[Any] = [user_id]
        if status:
            conditions.append("status = ?")
            params.append(status)
        if agent_id:
            conditions.append("(agent_id = ? OR agent_id IS NULL)")
            params.append(agent_id)

        where = " AND ".join(conditions)
        rows = self._storage.fetchall(
            f"SELECT * FROM mounts WHERE {where} ORDER BY path",
            tuple(params),
        )
        return [dict(r) for r in rows]

    def revoke(self, mount_id: str) -> bool:
        """Revoke a mount (set status to 'revoked')."""
        cursor = self._storage.execute(
            "UPDATE mounts SET status = 'revoked' WHERE id = ?", (mount_id,)
        )
        if self._notifier and cursor.rowcount > 0:
            self._notifier.notify_change(f"Mount revoked: {mount_id}", "mount_revoke")
        return cursor.rowcount > 0

    def delete(self, mount_id: str) -> bool:
        cursor = self._storage.execute("DELETE FROM mounts WHERE id = ?", (mount_id,))
        if self._notifier and cursor.rowcount > 0:
            self._notifier.notify_change(f"Mount deleted: {mount_id}", "mount_delete")
        return cursor.rowcount > 0

    def resolve_mounts(
        self,
        agent_id: str | None = None,
        workflow_id: str | None = None,
        user_id: str = "default",
    ) -> list[dict]:
        """Resolve which mounts apply for a given agent/workflow.

        Returns mounts in priority order:
        1. Agent-specific mounts
        2. Workflow-specific mounts
        3. Global mounts (no agent/workflow scope)
        """
        rows = self._storage.fetchall(
            """SELECT * FROM mounts WHERE user_id = ? AND status = 'active'
               AND (agent_id IS NULL OR agent_id = ?)
               AND (workflow_id IS NULL OR workflow_id = ?)
               ORDER BY
                 CASE WHEN agent_id IS NOT NULL THEN 0
                      WHEN workflow_id IS NOT NULL THEN 1
                      ELSE 2 END,
                 path""",
            (user_id, agent_id or "", workflow_id or ""),
        )
        return [dict(r) for r in rows]

    def check_access(
        self,
        path: str,
        access: str,
        agent_id: str | None = None,
        workflow_id: str | None = None,
        user_id: str = "default",
    ) -> bool:
        """Check if access to a path is allowed.

        The requested path must be within a mounted directory
        with sufficient access level.
        """
        resolved_path = Path(path).expanduser().resolve()
        mounts = self.resolve_mounts(agent_id, workflow_id, user_id)

        for mount in mounts:
            mount_path = Path(mount["path"])
            # Secure path containment check — NOT string startswith
            if resolved_path == mount_path or resolved_path.is_relative_to(mount_path):
                # Check access level
                if access == "read" and mount["access"] in ("read", "read_write"):
                    return True
                if access == "write" and mount["access"] in ("write", "read_write"):
                    return True

        return False
