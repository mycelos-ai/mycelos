"""NixOS-style config generation manager with atomic rollback.

Each call to apply() creates an immutable generation record.  Identical
configs (same SHA-256 digest) reuse the existing generation rather than
creating a duplicate.  The active pointer is a single row in
``active_generation`` that can be swapped atomically.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from mycelos.protocols import StorageBackend


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationInfo:
    """Summary record returned by list_generations()."""

    id: int
    parent_id: int | None
    config_hash: str
    description: str | None
    trigger: str
    created_at: str
    is_active: bool


@dataclass
class DiffResult:
    """Semantic diff between two config generations."""

    generation_a: int
    generation_b: int
    added: dict[str, Any] = field(default_factory=dict)
    removed: dict[str, Any] = field(default_factory=dict)
    changed: dict[str, tuple[Any, Any]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """Return True when the two generations are identical."""
        return not (self.added or self.removed or self.changed)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Base error for config generation operations."""


class GenerationNotFoundError(ConfigError):
    """Raised when a referenced generation does not exist."""

    def __init__(self, generation_id: int) -> None:
        super().__init__(f"Generation {generation_id} does not exist")
        self.generation_id = generation_id


class NoActiveGenerationError(ConfigError):
    """Raised when no generation has been activated yet."""


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ConfigGenerationManager:
    """Manages immutable, content-addressed config generations.

    Follows the NixOS model: every configuration change creates a new
    generation that references its parent.  Rolling back is an O(1)
    pointer swap — no data is ever mutated or deleted.

    Args:
        storage: Any object that satisfies the ``StorageBackend`` protocol.
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(
        self,
        config: dict[str, Any],
        description: str | None = None,
        trigger: str = "manual",
    ) -> int:
        """Persist a new config generation and make it active.

        If *config* is byte-for-byte identical to an existing generation
        (same SHA-256 hash), that generation is reused rather than
        duplicated.  In both cases the reused/new generation becomes the
        active one.

        Args:
            config: Arbitrary JSON-serialisable configuration mapping.
            description: Human-readable label for this generation.
            trigger: Origin of the change, e.g. ``"manual"``, ``"api"``,
                ``"agent"``.

        Returns:
            The integer ID of the active (possibly reused) generation.
        """
        config_json = self._canonical_json(config)
        config_hash = self._sha256(config_json)

        existing_id = self._find_by_hash(config_hash)
        if existing_id is not None:
            self._set_active(existing_id)
            return existing_id

        parent_id = self.get_active_generation_id()

        cursor = self._storage.execute(
            """
            INSERT INTO config_generations
                (parent_id, config_hash, config_snapshot, description, trigger)
            VALUES (?, ?, ?, ?, ?)
            """,
            (parent_id, config_hash, config_json, description, trigger),
        )
        new_id: int = cursor.lastrowid  # type: ignore[assignment]
        self._set_active(new_id)
        return new_id

    def apply_from_state(
        self,
        state_manager: Any,
        description: str | None = None,
        trigger: str = "manual",
    ) -> int:
        """Create a new generation from the current live DB state.

        Calls ``state_manager.snapshot()`` to capture all declarative tables,
        then stores the snapshot as a new immutable generation.

        Args:
            state_manager: Object with a ``snapshot()`` method that returns
                a JSON-serialisable dict of declarative system state.
            description: Human-readable label for this generation.
            trigger: Origin of the change, e.g. ``"manual"``, ``"api"``,
                ``"connector_setup"``.

        Returns:
            The integer ID of the active (possibly reused) generation.
        """
        snapshot = state_manager.snapshot()
        return self.apply(snapshot, description=description, trigger=trigger)

    def rollback(self, to_generation: int | None = None, state_manager: Any = None) -> int:
        """Roll back the active pointer to a previous generation.

        When *state_manager* is provided the live declarative tables are
        also restored from the target generation's snapshot, making the
        rollback a full state restoration rather than just a pointer swap.

        Args:
            to_generation: Target generation ID.  When *None* the manager
                rolls back to the parent of the current active generation.
            state_manager: Optional object with a ``restore(snapshot)``
                method.  When given, the target generation's config snapshot
                is written back into the live database tables.

        Returns:
            The generation ID that is now active.

        Raises:
            NoActiveGenerationError: If there is no active generation.
            GenerationNotFoundError: If *to_generation* does not exist.
        """
        if to_generation is None:
            current_id = self._require_active_id()
            row = self._storage.fetchone(
                "SELECT parent_id FROM config_generations WHERE id = ?",
                (current_id,),
            )
            if row is None:
                raise GenerationNotFoundError(current_id)
            parent_id = row["parent_id"]
            if parent_id is None:
                # Already at root; stay on current generation.
                return current_id
            to_generation = parent_id

        self._require_generation(to_generation)
        self._set_active(to_generation)

        # Restore live tables from the target generation's snapshot
        if state_manager is not None:
            snapshot = self.get_active_config()
            state_manager.restore(snapshot)

        return to_generation

    def get_active_generation_id(self) -> int | None:
        """Return the current active generation ID, or None if none exists."""
        row = self._storage.fetchone(
            "SELECT generation_id FROM active_generation WHERE id = 1"
        )
        return row["generation_id"] if row else None

    def get_active_config(self) -> dict[str, Any]:
        """Return the config dict for the active generation.

        Raises:
            NoActiveGenerationError: If no generation is active.
        """
        active_id = self._require_active_id()
        row = self._storage.fetchone(
            "SELECT config_snapshot FROM config_generations WHERE id = ?",
            (active_id,),
        )
        if row is None:
            raise GenerationNotFoundError(active_id)
        return json.loads(row["config_snapshot"])

    def list_generations(self, limit: int = 50) -> list[GenerationInfo]:
        """Return recent generations, newest first, with active marker.

        Args:
            limit: Maximum number of records to return.

        Returns:
            List of :class:`GenerationInfo` objects ordered by descending ID.
        """
        active_id = self.get_active_generation_id()
        rows = self._storage.fetchall(
            """
            SELECT id, parent_id, config_hash, description, trigger, created_at
            FROM config_generations
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            GenerationInfo(
                id=row["id"],
                parent_id=row["parent_id"],
                config_hash=row["config_hash"],
                description=row["description"],
                trigger=row["trigger"],
                created_at=row["created_at"],
                is_active=(row["id"] == active_id),
            )
            for row in rows
        ]

    def diff(self, gen_a: int, gen_b: int) -> DiffResult:
        """Compute a semantic diff between two config generations.

        Keys present only in *gen_a* appear in ``removed``; keys present
        only in *gen_b* appear in ``added``; keys whose values differ
        appear in ``changed`` as ``(value_in_a, value_in_b)`` tuples.

        Args:
            gen_a: ID of the base generation.
            gen_b: ID of the target generation.

        Returns:
            A :class:`DiffResult` describing the delta.

        Raises:
            GenerationNotFoundError: If either generation does not exist.
        """
        config_a = self._load_config(gen_a)
        config_b = self._load_config(gen_b)

        result = DiffResult(generation_a=gen_a, generation_b=gen_b)
        keys_a = set(config_a)
        keys_b = set(config_b)

        result.removed = {k: config_a[k] for k in keys_a - keys_b}
        result.added = {k: config_b[k] for k in keys_b - keys_a}
        result.changed = {
            k: (config_a[k], config_b[k])
            for k in keys_a & keys_b
            if config_a[k] != config_b[k]
        }
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _canonical_json(config: dict[str, Any]) -> str:
        """Serialise *config* deterministically (sorted keys, no whitespace)."""
        return json.dumps(config, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def _find_by_hash(self, config_hash: str) -> int | None:
        row = self._storage.fetchone(
            "SELECT id FROM config_generations WHERE config_hash = ? LIMIT 1",
            (config_hash,),
        )
        return row["id"] if row else None

    def _set_active(self, generation_id: int) -> None:
        """Atomically update the active-generation pointer."""
        self._storage.execute(
            """
            INSERT INTO active_generation (id, generation_id)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET
                generation_id = excluded.generation_id,
                activated_at  = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (generation_id,),
        )

    def _require_active_id(self) -> int:
        active_id = self.get_active_generation_id()
        if active_id is None:
            raise NoActiveGenerationError("No active generation exists")
        return active_id

    def _require_generation(self, generation_id: int) -> None:
        row = self._storage.fetchone(
            "SELECT id FROM config_generations WHERE id = ?",
            (generation_id,),
        )
        if row is None:
            raise GenerationNotFoundError(generation_id)

    def _load_config(self, generation_id: int) -> dict[str, Any]:
        row = self._storage.fetchone(
            "SELECT config_snapshot FROM config_generations WHERE id = ?",
            (generation_id,),
        )
        if row is None:
            raise GenerationNotFoundError(generation_id)
        return json.loads(row["config_snapshot"])
