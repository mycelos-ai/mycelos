"""ConnectorRegistry — CRUD for connectors and their capabilities in SQLite."""

from __future__ import annotations

from mycelos.protocols import StorageBackend


class ConnectorRegistry:
    """Manages connector records with normalized capabilities."""

    def __init__(self, storage: StorageBackend, notifier=None) -> None:
        self._storage = storage
        self._notifier = notifier

    def register(
        self,
        connector_id: str,
        name: str,
        connector_type: str,
        capabilities: list[str],
        description: str | None = None,
        setup_type: str | None = None,
    ) -> None:
        """Register a new connector with its capabilities."""
        self._storage.execute(
            """INSERT INTO connectors (id, name, connector_type, description, setup_type)
               VALUES (?, ?, ?, ?, ?)""",
            (connector_id, name, connector_type, description, setup_type),
        )
        for cap in capabilities:
            self._storage.execute(
                "INSERT INTO connector_capabilities (connector_id, capability) VALUES (?, ?)",
                (connector_id, cap),
            )
        if self._notifier:
            self._notifier.notify_change(f"Connector registered: {connector_id}", "connector_register")

    def get(self, connector_id: str) -> dict | None:
        """Get a connector by ID with its capabilities."""
        row = self._storage.fetchone(
            "SELECT * FROM connectors WHERE id = ?", (connector_id,)
        )
        if row is None:
            return None
        result = dict(row)
        caps = self._storage.fetchall(
            "SELECT capability FROM connector_capabilities WHERE connector_id = ?",
            (connector_id,),
        )
        result["capabilities"] = [c["capability"] for c in caps]
        return result

    def list_connectors(self, status: str | None = None) -> list[dict]:
        """List connectors, optionally filtered by status, with capabilities."""
        if status:
            rows = self._storage.fetchall(
                "SELECT * FROM connectors WHERE status = ? ORDER BY created_at",
                (status,),
            )
        else:
            rows = self._storage.fetchall(
                "SELECT * FROM connectors ORDER BY created_at"
            )
        result = []
        for row in rows:
            entry = dict(row)
            caps = self._storage.fetchall(
                "SELECT capability FROM connector_capabilities WHERE connector_id = ?",
                (row["id"],),
            )
            entry["capabilities"] = [c["capability"] for c in caps]
            result.append(entry)
        return result

    def set_status(self, connector_id: str, status: str) -> None:
        """Update a connector's status."""
        self._storage.execute(
            """UPDATE connectors SET status = ?,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE id = ?""",
            (status, connector_id),
        )
        if self._notifier:
            self._notifier.notify_change(f"Connector {connector_id} -> {status}", "connector_status")

    def update_description(self, connector_id: str, description: str) -> None:
        """Update a connector's description."""
        self._storage.execute(
            """UPDATE connectors SET description = ?,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE id = ?""",
            (description, connector_id),
        )
        if self._notifier:
            self._notifier.notify_change(f"Connector {connector_id} description updated", "connector_update")

    def remove(self, connector_id: str) -> None:
        """Remove a connector. CASCADE deletes its capabilities."""
        self._storage.execute(
            "DELETE FROM connectors WHERE id = ?", (connector_id,)
        )
        if self._notifier:
            self._notifier.notify_change(f"Connector removed: {connector_id}", "connector_remove")

    # --- Operational telemetry ------------------------------------------------
    #
    # Every outbound call site hands its result back here. The registry
    # stores a single "last success" and "last error" per connector so the
    # Doctor and the Connectors UI can answer "when did this last work?"
    # without scanning audit_events. We deliberately do NOT keep a full
    # history here — audit_events is the authoritative log for that.

    def record_success(self, connector_id: str) -> None:
        """Stamp last_success_at for a connector after a successful call."""
        self._storage.execute(
            """UPDATE connectors
                 SET last_success_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE id = ?""",
            (connector_id,),
        )

    def record_failure(self, connector_id: str, error: str) -> None:
        """Stamp last_error / last_error_at for a connector after a failed call.

        The error message is truncated to 500 chars so a huge traceback
        cannot blow up the row. Stack traces belong in the audit log, not
        in this hot-path column.
        """
        trimmed = (error or "")[:500]
        self._storage.execute(
            """UPDATE connectors
                 SET last_error    = ?,
                     last_error_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE id = ?""",
            (trimmed, connector_id),
        )
