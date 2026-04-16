"""Capability Token Manager -- issues and validates short-lived, scope-limited tokens.

Tokens are UUIDs stored in SQLite. Each token has:
- scope: JSON array of allowed operations
- TTL: expiry time
- max_requests: optional request count limit
- revoked flag: for immediate invalidation

Validation checks: exists -> not revoked -> not expired -> scope matches -> request limit.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta

from mycelos.protocols import StorageBackend, ValidationResult


class CapabilityTokenManager:
    """SQLite-backed capability token manager.

    Satisfies the CapabilityTokenManager protocol from protocols.py.
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def issue(
        self,
        agent_id: str,
        scope: list[str],
        ttl_seconds: int,
        task_id: str | None = None,
        user_id: str = "default",
        max_requests: int | None = None,
    ) -> str:
        """Issue a new capability token. Returns the token ID."""
        token_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)

        self._storage.execute(
            """INSERT INTO capability_tokens
               (id, user_id, agent_id, task_id, scope, max_requests, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                token_id,
                user_id,
                agent_id,
                task_id,
                json.dumps(scope),
                max_requests,
                expires_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            ),
        )
        return token_id

    def validate(self, token_id: str, operation: str) -> ValidationResult:
        """Validate a token for a specific operation.

        Checks: exists -> not revoked -> not expired -> scope -> request limit.
        """
        row = self._storage.fetchone(
            "SELECT * FROM capability_tokens WHERE id = ?", (token_id,)
        )
        if row is None:
            return ValidationResult(valid=False, reason="Token not found")

        if row["revoked"]:
            return ValidationResult(
                valid=False, agent_id=row["agent_id"], reason="Token revoked"
            )

        expires_at = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires_at:
            return ValidationResult(
                valid=False, agent_id=row["agent_id"], reason="Token expired"
            )

        scope = json.loads(row["scope"])
        if operation not in scope:
            return ValidationResult(
                valid=False,
                agent_id=row["agent_id"],
                reason=f"Scope denied: '{operation}' not in {scope}",
            )

        if row["max_requests"] is not None:
            # Atomic check-and-increment to prevent TOCTOU race condition.
            # A single UPDATE that only succeeds when used_requests < max_requests
            # guarantees no two concurrent requests can both pass the limit.
            cursor = self._storage.execute(
                """UPDATE capability_tokens
                   SET used_requests = used_requests + 1
                   WHERE id = ? AND (max_requests IS NULL OR used_requests < max_requests)""",
                (token_id,),
            )
            if cursor.rowcount == 0:
                return ValidationResult(
                    valid=False,
                    agent_id=row["agent_id"],
                    reason=f"Request limit exceeded ({row['max_requests']})",
                )

        return ValidationResult(valid=True, agent_id=row["agent_id"])

    def revoke(self, token_id: str) -> None:
        """Revoke a specific token."""
        self._storage.execute(
            "UPDATE capability_tokens SET revoked = 1 WHERE id = ?", (token_id,)
        )

    def revoke_all_for_agent(self, agent_id: str) -> None:
        """Revoke all tokens for an agent."""
        self._storage.execute(
            "UPDATE capability_tokens SET revoked = 1 WHERE agent_id = ? AND revoked = 0",
            (agent_id,),
        )
