"""Agent Registry V2 -- normalized capabilities, Object Store code, LLM model assignments."""

from __future__ import annotations

from typing import Any

from mycelos.protocols import StorageBackend


class AgentRegistry:
    """Manages agent records with normalized capabilities and Object Store code."""

    def __init__(self, storage: StorageBackend, notifier=None) -> None:
        self._storage = storage
        self._notifier = notifier

    def register(
        self,
        agent_id: str,
        name: str,
        agent_type: str,
        capabilities: list[str],
        created_by: str,
    ) -> None:
        """Register a new agent with capabilities."""
        self._storage.execute(
            "INSERT INTO agents (id, name, agent_type, created_by) VALUES (?, ?, ?, ?)",
            (agent_id, name, agent_type, created_by),
        )
        for cap in capabilities:
            self._storage.execute(
                "INSERT INTO agent_capabilities (agent_id, capability) VALUES (?, ?)",
                (agent_id, cap),
            )
        if self._notifier:
            self._notifier.notify_change(f"Agent registered: {agent_id}", "agent_register")
            self._notifier.log("agent.registered", {"agent_id": agent_id})

    def get(self, agent_id: str) -> dict | None:
        """Get an agent by ID with capabilities."""
        row = self._storage.fetchone("SELECT * FROM agents WHERE id = ?", (agent_id,))
        if row is None:
            return None
        result = dict(row)
        caps = self._storage.fetchall(
            "SELECT capability FROM agent_capabilities WHERE agent_id = ?",
            (agent_id,),
        )
        result["capabilities"] = [c["capability"] for c in caps]
        return result

    def update_persona_fields(
        self,
        agent_id: str,
        *,
        system_prompt: str | None = None,
        model: str | None = None,
        allowed_tools: list[str] | None = None,
        audit: Any = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        """Update one or more persona fields and record the previous values.

        Returns a dict with the `before` snapshot so callers (API, CLI, tests)
        can show it in a history view or revert if needed.
        """
        import json
        current = self.get(agent_id) or {}
        before = {
            "system_prompt": current.get("system_prompt"),
            "model": current.get("model"),
            "allowed_tools": current.get("allowed_tools"),
        }
        updates: list[str] = []
        params: list = []
        if system_prompt is not None:
            updates.append("system_prompt = ?")
            params.append(system_prompt)
        if model is not None:
            updates.append("model = ?")
            params.append(model)
        if allowed_tools is not None:
            updates.append("allowed_tools = ?")
            params.append(json.dumps(allowed_tools))
        if not updates:
            return {"before": before, "changed": []}
        params.append(agent_id)
        self._storage.execute(
            f"UPDATE agents SET {', '.join(updates)} WHERE id = ?", tuple(params)
        )
        if audit is not None:
            audit.log(
                "agent.persona.updated",
                details={
                    "agent_id": agent_id,
                    "actor": actor,
                    "before": before,
                    "after": {
                        "system_prompt": system_prompt if system_prompt is not None else before["system_prompt"],
                        "model": model if model is not None else before["model"],
                        "allowed_tools": allowed_tools if allowed_tools is not None else before["allowed_tools"],
                    },
                },
            )
        if self._notifier:
            self._notifier.notify_change(
                f"Agent {agent_id} persona updated", "agent_persona_updated",
            )
        return {"before": before, "changed": [u.split(" = ")[0] for u in updates]}

    def persona_history(self, agent_id: str, limit: int = 10) -> list[dict]:
        """Return the last N agent.persona.updated audit events for an agent."""
        import json
        rows = self._storage.fetchall(
            "SELECT created_at, details FROM audit_events "
            "WHERE event_type = 'agent.persona.updated' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit * 5,),  # overfetch, filter in Python by agent_id
        )
        history: list[dict] = []
        for r in rows:
            try:
                d = json.loads(r["details"]) if r["details"] else {}
            except Exception:
                continue
            if d.get("agent_id") != agent_id:
                continue
            history.append({
                "created_at": r["created_at"],
                "actor": d.get("actor", ""),
                "before": d.get("before", {}),
                "after": d.get("after", {}),
            })
            if len(history) >= limit:
                break
        return history

    def rename(self, agent_id: str, display_name: str) -> None:
        """Set a user-chosen display name for an agent.

        The canonical `name` stays stable so code lookups by name keep working;
        the UI reads `display_name` (falling back to `name`).
        """
        self._storage.execute(
            "UPDATE agents SET display_name = ? WHERE id = ?",
            (display_name or None, agent_id),
        )
        if self._notifier:
            self._notifier.notify_change(
                f"Agent {agent_id} renamed to {display_name}", "agent_rename",
            )

    def set_status(self, agent_id: str, status: str) -> None:
        """Update an agent's status (proposed, active, deprecated)."""
        self._storage.execute(
            "UPDATE agents SET status = ? WHERE id = ?", (status, agent_id)
        )
        if self._notifier:
            self._notifier.notify_change(f"Agent {agent_id} -> {status}", "agent_status")

    def update_reputation(self, agent_id: str, score: float) -> None:
        """Update an agent's reputation score."""
        self._storage.execute(
            "UPDATE agents SET reputation = ? WHERE id = ?", (score, agent_id)
        )
        if self._notifier:
            self._notifier.notify_change(f"Agent {agent_id} reputation: {score}", "agent_reputation")

    def set_persona(
        self, agent_id: str, system_prompt: str,
        allowed_tools: list[str] | None = None,
        model: str | None = None,
        user_facing: bool = True,
        display_name: str | None = None,
    ) -> None:
        """Configure an agent as a persona with custom prompt, tools, and model."""
        import json
        updates = ["system_prompt = ?", "user_facing = ?"]
        params: list = [system_prompt, 1 if user_facing else 0]
        if allowed_tools is not None:
            updates.append("allowed_tools = ?")
            params.append(json.dumps(allowed_tools))
        if model:
            updates.append("model = ?")
            params.append(model)
        if display_name:
            updates.append("display_name = ?")
            params.append(display_name)
        params.append(agent_id)
        self._storage.execute(
            f"UPDATE agents SET {', '.join(updates)} WHERE id = ?", tuple(params)
        )
        if self._notifier:
            self._notifier.notify_change(f"Agent {agent_id} persona configured", "agent_persona")

    def set_capabilities(self, agent_id: str, capabilities: list[str]) -> None:
        """Replace all capabilities for an agent."""
        self._storage.execute(
            "DELETE FROM agent_capabilities WHERE agent_id = ?", (agent_id,)
        )
        for cap in capabilities:
            self._storage.execute(
                "INSERT INTO agent_capabilities (agent_id, capability) VALUES (?, ?)",
                (agent_id, cap),
            )
        if self._notifier:
            self._notifier.notify_change(f"Agent {agent_id} capabilities updated", "agent_capabilities")

    def save_code(
        self,
        agent_id: str,
        code: str,
        tests: str | None,
        prompt: str | None,
        object_store: Any,
    ) -> dict[str, str]:
        """Store agent code in Object Store, update hash references."""
        code_hash = object_store.store(code)
        tests_hash = object_store.store(tests) if tests else None
        prompt_hash = object_store.store(prompt) if prompt else None
        self._storage.execute(
            """UPDATE agents SET code_hash = ?, tests_hash = ?, prompt_hash = ?
               WHERE id = ?""",
            (code_hash, tests_hash, prompt_hash, agent_id),
        )
        if self._notifier:
            self._notifier.notify_change(f"Agent {agent_id} code updated", "agent_code")
        return {"code_hash": code_hash, "tests_hash": tests_hash, "prompt_hash": prompt_hash}

    def get_code(self, agent_id: str, object_store: Any) -> dict | None:
        """Load agent code from Object Store."""
        row = self._storage.fetchone(
            "SELECT code_hash, tests_hash, prompt_hash FROM agents WHERE id = ?",
            (agent_id,),
        )
        if row is None or row["code_hash"] is None:
            return None
        return {
            "code": object_store.load(row["code_hash"]),
            "tests": object_store.load(row["tests_hash"]) if row["tests_hash"] else None,
            "prompt": object_store.load(row["prompt_hash"]) if row["prompt_hash"] else None,
        }

    def set_models(self, agent_id: str, model_ids: list[str], purpose: str = "execution") -> None:
        """Set LLM models for an agent with priority order."""
        self._storage.execute(
            "DELETE FROM agent_llm_models WHERE agent_id = ? AND purpose = ?",
            (agent_id, purpose),
        )
        for priority, model_id in enumerate(model_ids, 1):
            self._storage.execute(
                """INSERT INTO agent_llm_models (agent_id, model_id, priority, purpose)
                   VALUES (?, ?, ?, ?)""",
                (agent_id, model_id, priority, purpose),
            )
        if self._notifier:
            self._notifier.notify_change(f"Agent {agent_id} models set", "agent_models")

    def get_models(self, agent_id: str, purpose: str = "execution") -> list[str]:
        """Get LLM models for an agent, ordered by priority."""
        rows = self._storage.fetchall(
            """SELECT model_id FROM agent_llm_models
               WHERE agent_id = ? AND purpose = ? ORDER BY priority""",
            (agent_id, purpose),
        )
        return [r["model_id"] for r in rows]

    def list_agents(self, status: str | None = None) -> list[dict]:
        """List agents with capabilities, optionally filtered by status."""
        if status:
            rows = self._storage.fetchall(
                "SELECT * FROM agents WHERE status = ? ORDER BY created_at",
                (status,),
            )
        else:
            rows = self._storage.fetchall(
                "SELECT * FROM agents ORDER BY created_at"
            )
        result = []
        for row in rows:
            entry = dict(row)
            caps = self._storage.fetchall(
                "SELECT capability FROM agent_capabilities WHERE agent_id = ?",
                (row["id"],),
            )
            entry["capabilities"] = [c["capability"] for c in caps]
            result.append(entry)
        return result
