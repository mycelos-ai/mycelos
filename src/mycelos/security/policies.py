"""Policy Engine -- evaluates permission decisions per agent and resource.

Decisions: always, confirm, prepare, never
- always:  action is auto-allowed
- confirm: user must approve synchronously
- prepare: action runs, result parked for user review
- never:   action is blocked

Protected resources (never auto-learnable):
- agent.register: always requires human confirmation
"""

from __future__ import annotations

import uuid

from mycelos.protocols import StorageBackend

VALID_DECISIONS = {"always", "confirm", "prepare", "never"}
PROTECTED_RESOURCES = {"agent.register"}


class PolicyEngine:
    """SQLite-backed policy engine.

    Satisfies the PolicyEngine protocol from protocols.py.
    """

    def __init__(self, storage: StorageBackend, notifier=None) -> None:
        self._storage = storage
        self._notifier = notifier

    def evaluate(self, user_id: str, agent_id: str, resource: str) -> str:
        """Evaluate the policy decision for an agent accessing a resource.

        Lookup order: agent-specific > global > default (confirm).
        Protected resources always return 'confirm'.
        """
        if resource in PROTECTED_RESOURCES:
            return "confirm"

        row = self._storage.fetchone(
            "SELECT decision FROM policies WHERE user_id = ? AND agent_id = ? AND resource = ?",
            (user_id, agent_id, resource),
        )
        if row:
            return row["decision"]

        row = self._storage.fetchone(
            "SELECT decision FROM policies WHERE user_id = ? AND agent_id IS NULL AND resource = ?",
            (user_id, resource),
        )
        if row:
            return row["decision"]

        return "confirm"

    def set_policy(
        self,
        user_id: str,
        agent_id: str | None,
        resource: str,
        decision: str,
        requested_by: str | None = None,
    ) -> None:
        """Set a policy.

        Raises PermissionError for self-modification,
        ValueError for invalid decision.
        """
        if decision not in VALID_DECISIONS:
            raise ValueError(
                f"Invalid decision '{decision}'. Must be one of: {VALID_DECISIONS}"
            )

        if requested_by and requested_by == agent_id:
            raise PermissionError(
                f"Agent '{agent_id}' cannot modify its own policy. "
                "Only the user or Blueprint Lifecycle can change policies."
            )

        policy_id = str(uuid.uuid4())

        if agent_id is not None:
            existing = self._storage.fetchone(
                "SELECT id FROM policies WHERE user_id = ? AND agent_id = ? AND resource = ?",
                (user_id, agent_id, resource),
            )
        else:
            existing = self._storage.fetchone(
                "SELECT id FROM policies WHERE user_id = ? AND agent_id IS NULL AND resource = ?",
                (user_id, resource),
            )

        if existing:
            self._storage.execute(
                "UPDATE policies SET decision = ? WHERE id = ?",
                (decision, existing["id"]),
            )
        else:
            self._storage.execute(
                "INSERT INTO policies (id, user_id, agent_id, resource, decision) VALUES (?, ?, ?, ?, ?)",
                (policy_id, user_id, agent_id, resource, decision),
            )
        if self._notifier:
            self._notifier.notify_change(
                f"Policy set: {agent_id or 'global'} / {resource} = {decision}",
                "policy_set",
            )

    def list_policies(self, user_id: str, agent_id: str | None = None) -> list[dict]:
        """List all policies for a user, optionally filtered by agent."""
        if agent_id:
            return self._storage.fetchall(
                "SELECT * FROM policies WHERE user_id = ? AND agent_id = ? ORDER BY resource",
                (user_id, agent_id),
            )
        return self._storage.fetchall(
            "SELECT * FROM policies WHERE user_id = ? ORDER BY agent_id, resource",
            (user_id,),
        )
