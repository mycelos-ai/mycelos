"""StateManager — snapshot and restore of all declarative system state.

Reads all declarative tables (connectors, agents, capabilities, policies,
LLM models, credentials) into a JSON-serializable dict. Restores from
a snapshot back into live tables. This bridges live DB state with
NixOS-style immutable config generations.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from typing import Any

logger = logging.getLogger("mycelos.config")

from mycelos.protocols import StorageBackend


class StateManager:
    """Manages snapshots and restores of declarative system state."""

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def snapshot(self) -> dict[str, Any]:
        """Read all declarative tables and build a complete state snapshot."""
        return {
            "schema_version": 2,
            "connectors": self._snapshot_connectors(),
            "agents": self._snapshot_agents(),
            "policies": self._snapshot_policies(),
            "llm": self._snapshot_llm(),
            "credentials": self._snapshot_credentials(),
            "workflows": self._snapshot_workflows(),
            "mounts": self._snapshot_mounts(),
            "channels": self._snapshot_channels(),
            "scheduled_tasks": self._snapshot_scheduled_tasks(),
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore all declarative tables from a snapshot.

        Credentials with security_rotated=1 in the CURRENT DB are
        preserved (not overwritten by snapshot data).

        Runs inside a single DB transaction — if any statement fails the
        entire restore is rolled back, leaving the previous state intact.
        This gives NixOS-style all-or-nothing config switching.
        """
        with self._storage.transaction():
            self._restore_impl(snapshot)

    def _restore_impl(self, snapshot: dict[str, Any]) -> None:
        # Collect rotated credentials BEFORE wiping
        rotated: set[str] = set()
        for row in self._storage.fetchall(
            "SELECT service FROM credentials WHERE security_rotated = 1"
        ):
            rotated.add(row["service"])

        # Wipe declarative tables (order matters for FK constraints)
        # scheduled_tasks references workflows, so wipe first
        self._storage.execute("DELETE FROM scheduled_tasks")
        # workflow_runs references workflows, so wipe runs first if any exist
        self._storage.execute("DELETE FROM workflow_runs")
        self._storage.execute("DELETE FROM workflows")
        self._storage.execute("DELETE FROM channels")
        self._storage.execute("DELETE FROM mounts")
        self._storage.execute("DELETE FROM agent_llm_models")
        self._storage.execute("DELETE FROM agent_capabilities")
        self._storage.execute("DELETE FROM connector_capabilities")
        self._storage.execute("DELETE FROM agents")
        self._storage.execute("DELETE FROM connectors")
        self._storage.execute("DELETE FROM policies")
        self._storage.execute("DELETE FROM llm_models")

        # Restore LLM models first (FK target for agent_llm_models)
        for model_id, info in snapshot.get("llm", {}).get("models", {}).items():
            self._storage.execute(
                """INSERT INTO llm_models (id, provider, tier, input_cost_per_1k,
                   output_cost_per_1k, max_context, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    model_id,
                    info["provider"],
                    info["tier"],
                    info.get("input_cost_per_1k"),
                    info.get("output_cost_per_1k"),
                    info.get("max_context"),
                    info.get("status", "available"),
                ),
            )

        # Restore connectors
        for cid, info in snapshot.get("connectors", {}).items():
            self._storage.execute(
                """INSERT INTO connectors (id, name, connector_type, description, status, setup_type)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    cid,
                    info["name"],
                    info["connector_type"],
                    info.get("description"),
                    info.get("status", "active"),
                    info.get("setup_type"),
                ),
            )
            for cap in info.get("capabilities", []):
                self._storage.execute(
                    "INSERT INTO connector_capabilities (connector_id, capability) VALUES (?, ?)",
                    (cid, cap),
                )

        # Restore agents (FK target for agent_capabilities and agent_llm_models)
        for aid, info in snapshot.get("agents", {}).items():
            self._storage.execute(
                """INSERT INTO agents (id, name, agent_type, status, policy, reputation,
                   code_hash, tests_hash, prompt_hash, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    aid,
                    info["name"],
                    info["agent_type"],
                    info.get("status", "proposed"),
                    info.get("policy"),
                    info.get("reputation", 0.5),
                    info.get("code_hash"),
                    info.get("tests_hash"),
                    info.get("prompt_hash"),
                    info.get("created_by", "system"),
                ),
            )
            for cap in info.get("capabilities", []):
                self._storage.execute(
                    "INSERT INTO agent_capabilities (agent_id, capability) VALUES (?, ?)",
                    (aid, cap),
                )

        # Restore LLM assignments
        for key, model_ids in snapshot.get("llm", {}).get("assignments", {}).items():
            parts = key.split(":", 1)
            agent_id = None if parts[0] == "system" else parts[0]
            purpose = parts[1] if len(parts) > 1 else "execution"
            for priority, model_id in enumerate(model_ids, 1):
                self._storage.execute(
                    """INSERT INTO agent_llm_models (agent_id, model_id, priority, purpose)
                       VALUES (?, ?, ?, ?)""",
                    (agent_id, model_id, priority, purpose),
                )

        # Restore policies
        for key, decision in snapshot.get("policies", {}).items():
            parts = key.split(":")
            user_id = parts[0] if len(parts) > 0 else "default"
            agent_id = parts[1] if len(parts) > 1 and parts[1] != "*" else None
            resource = parts[2] if len(parts) > 2 else parts[-1]
            policy_id = str(uuid.uuid4())
            self._storage.execute(
                """INSERT INTO policies (id, user_id, agent_id, resource, decision)
                   VALUES (?, ?, ?, ?, ?)""",
                (policy_id, user_id, agent_id, resource, decision),
            )

        # Restore mounts
        for mid, info in snapshot.get("mounts", {}).items():
            self._storage.execute(
                """INSERT INTO mounts (id, path, access, purpose, agent_id, workflow_id, user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (mid, info["path"], info["access"], info.get("purpose"),
                 info.get("agent_id"), info.get("workflow_id"),
                 info.get("user_id", "default")),
            )

        # Restore credentials (skip rotated ones)
        # Delete only non-rotated credentials
        for row in self._storage.fetchall(
            "SELECT service FROM credentials WHERE security_rotated = 0"
        ):
            self._storage.execute(
                "DELETE FROM credentials WHERE service = ?", (row["service"],)
            )

        for service, info in snapshot.get("credentials", {}).items():
            if service in rotated:
                continue  # Skip rotated — keep current
            # Remove if exists, then insert from snapshot
            self._storage.execute(
                "DELETE FROM credentials WHERE service = ?", (service,)
            )
            encrypted = info["encrypted"]
            nonce = info["nonce"]
            # Handle base64-encoded blobs from snapshot
            if isinstance(encrypted, str):
                encrypted = base64.b64decode(encrypted)
            if isinstance(nonce, str):
                nonce = base64.b64decode(nonce)
            self._storage.execute(
                """INSERT INTO credentials (service, encrypted, nonce, security_rotated)
                   VALUES (?, ?, ?, ?)""",
                (service, encrypted, nonce, info.get("security_rotated", 0)),
            )

        # Restore channels
        for cid, info in snapshot.get("channels", {}).items():
            config_val = info.get("config", {})
            allowed = info.get("allowed_users", [])
            self._storage.execute(
                """INSERT INTO channels (id, channel_type, mode, status, config, allowed_users)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (cid, info["channel_type"], info.get("mode", "polling"),
                 info.get("status", "active"),
                 json.dumps(config_val) if isinstance(config_val, dict) else config_val,
                 json.dumps(allowed) if isinstance(allowed, list) else allowed),
            )

        # Restore workflows (before scheduled_tasks which reference them)
        for wid, info in snapshot.get("workflows", {}).items():
            steps = info.get("steps", [])
            scope = info.get("scope")
            tags = info.get("tags")
            self._storage.execute(
                """INSERT INTO workflows (id, name, description, goal, version,
                   steps, scope, tags, status, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (wid, info["name"], info.get("description"), info.get("goal"),
                 info.get("version", 1),
                 json.dumps(steps) if isinstance(steps, list) else steps,
                 json.dumps(scope) if isinstance(scope, list) else scope,
                 json.dumps(tags) if isinstance(tags, list) else tags,
                 info.get("status", "active"),
                 info.get("created_by", "system")),
            )

        # Restore scheduled tasks
        for tid, info in snapshot.get("scheduled_tasks", {}).items():
            next_run_str = ""
            try:
                from mycelos.scheduler.schedule_manager import parse_next_run

                next_run = parse_next_run(info["schedule"])
                next_run_str = next_run.isoformat()
            except Exception as exc:
                logger.warning(
                    "restore: could not parse schedule %r for task %s: %s",
                    info.get("schedule"), tid, exc,
                )
            self._storage.execute(
                """INSERT INTO scheduled_tasks
                   (id, workflow_id, user_id, schedule, inputs, status, budget_per_run, next_run)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tid,
                    info["workflow_id"],
                    info["user_id"],
                    info["schedule"],
                    json.dumps(info["inputs"]) if info.get("inputs") else None,
                    info.get("status", "active"),
                    info.get("budget_per_run"),
                    next_run_str,
                ),
            )

    # --- Private snapshot helpers ---

    def _snapshot_connectors(self) -> dict[str, Any]:
        """Snapshot all connectors with their capabilities."""
        result: dict[str, Any] = {}
        for row in self._storage.fetchall("SELECT * FROM connectors ORDER BY id"):
            cid = row["id"]
            caps = self._storage.fetchall(
                "SELECT capability FROM connector_capabilities WHERE connector_id = ? ORDER BY capability",
                (cid,),
            )
            result[cid] = {
                "name": row["name"],
                "connector_type": row["connector_type"],
                "description": row.get("description"),
                "status": row.get("status"),
                "setup_type": row.get("setup_type"),
                "capabilities": [c["capability"] for c in caps],
            }
        return result

    def _snapshot_agents(self) -> dict[str, Any]:
        """Snapshot all agents with capabilities and code hashes."""
        result: dict[str, Any] = {}
        for row in self._storage.fetchall("SELECT * FROM agents ORDER BY id"):
            aid = row["id"]
            caps = self._storage.fetchall(
                "SELECT capability FROM agent_capabilities WHERE agent_id = ? ORDER BY capability",
                (aid,),
            )
            result[aid] = {
                "name": row["name"],
                "agent_type": row["agent_type"],
                "status": row.get("status"),
                "policy": row.get("policy"),
                "reputation": row.get("reputation"),
                "code_hash": row.get("code_hash"),
                "tests_hash": row.get("tests_hash"),
                "prompt_hash": row.get("prompt_hash"),
                "capabilities": [c["capability"] for c in caps],
                "created_by": row.get("created_by"),
            }
        return result

    def _snapshot_policies(self) -> dict[str, str]:
        """Snapshot all policies as key -> decision mapping."""
        result: dict[str, str] = {}
        for row in self._storage.fetchall("SELECT * FROM policies ORDER BY id"):
            agent_part = row["agent_id"] or "*"
            key = f"{row['user_id']}:{agent_part}:{row['resource']}"
            result[key] = row["decision"]
        return result

    def _snapshot_llm(self) -> dict[str, Any]:
        """Snapshot LLM model registry and agent-model assignments."""
        models: dict[str, Any] = {}
        for row in self._storage.fetchall("SELECT * FROM llm_models ORDER BY id"):
            models[row["id"]] = {
                "provider": row["provider"],
                "tier": row["tier"],
                "input_cost_per_1k": row.get("input_cost_per_1k"),
                "output_cost_per_1k": row.get("output_cost_per_1k"),
                "max_context": row.get("max_context"),
                "status": row.get("status"),
            }

        assignments: dict[str, list[str]] = {}
        for row in self._storage.fetchall(
            "SELECT * FROM agent_llm_models ORDER BY agent_id, purpose, priority"
        ):
            agent_part = row["agent_id"] or "system"
            key = f"{agent_part}:{row['purpose']}"
            if key not in assignments:
                assignments[key] = []
            assignments[key].append(row["model_id"])

        return {"models": models, "assignments": assignments}

    def _snapshot_credentials(self) -> dict[str, Any]:
        """Snapshot all credentials (encrypted blobs as base64)."""
        result: dict[str, Any] = {}
        for row in self._storage.fetchall(
            "SELECT * FROM credentials ORDER BY service"
        ):
            encrypted = row["encrypted"]
            nonce = row["nonce"]
            # Encode binary to base64 for JSON serialization
            if isinstance(encrypted, (bytes, memoryview)):
                encrypted = base64.b64encode(bytes(encrypted)).decode()
            if isinstance(nonce, (bytes, memoryview)):
                nonce = base64.b64encode(bytes(nonce)).decode()
            result[row["service"]] = {
                "encrypted": encrypted,
                "nonce": nonce,
                "security_rotated": row["security_rotated"],
            }
        return result

    def _snapshot_scheduled_tasks(self) -> dict[str, Any]:
        """Snapshot all scheduled tasks."""
        result: dict[str, Any] = {}
        for row in self._storage.fetchall(
            "SELECT * FROM scheduled_tasks ORDER BY id"
        ):
            tid = row["id"]
            inputs = row["inputs"]
            if isinstance(inputs, str):
                inputs = json.loads(inputs)
            result[tid] = {
                "workflow_id": row["workflow_id"],
                "user_id": row["user_id"],
                "schedule": row["schedule"],
                "inputs": inputs,
                "status": row["status"],
                "budget_per_run": row["budget_per_run"],
            }
        return result

    def _snapshot_workflows(self) -> dict[str, Any]:
        result = {}
        for row in self._storage.fetchall("SELECT * FROM workflows ORDER BY id"):
            wid = row["id"]
            steps = row["steps"]
            scope = row["scope"]
            tags = row["tags"]
            # Parse JSON fields
            if isinstance(steps, str):
                steps = json.loads(steps)
            if isinstance(scope, str):
                scope = json.loads(scope)
            if isinstance(tags, str):
                tags = json.loads(tags)
            result[wid] = {
                "name": row["name"],
                "description": row["description"],
                "goal": row["goal"],
                "version": row["version"],
                "steps": steps,
                "scope": scope,
                "tags": tags,
                "status": row["status"],
                "created_by": row["created_by"],
            }
        return result

    def _snapshot_channels(self) -> dict[str, Any]:
        """Snapshot all channel configurations."""
        result: dict[str, Any] = {}
        for row in self._storage.fetchall("SELECT * FROM channels ORDER BY id"):
            cid = row["id"]
            config_val = row["config"]
            allowed = row["allowed_users"]
            if isinstance(config_val, str):
                config_val = json.loads(config_val)
            if isinstance(allowed, str):
                allowed = json.loads(allowed)
            result[cid] = {
                "channel_type": row["channel_type"],
                "mode": row["mode"],
                "status": row.get("status", "active"),
                "config": config_val,
                "allowed_users": allowed,
            }
        return result

    def _snapshot_mounts(self) -> dict[str, Any]:
        result = {}
        for row in self._storage.fetchall("SELECT * FROM mounts WHERE status = 'active' ORDER BY id"):
            mid = row["id"]
            result[mid] = {
                "path": row["path"],
                "access": row["access"],
                "purpose": row["purpose"],
                "agent_id": row["agent_id"],
                "workflow_id": row["workflow_id"],
                "user_id": row["user_id"],
            }
        return result
