"""LLM Model Registry -- model CRUD, agent assignment, failover resolution, LiteLLM sync."""

from __future__ import annotations

from typing import Any

from mycelos.protocols import StorageBackend


# Smart defaults per provider
SMART_DEFAULTS: dict[str, dict[str, list[str]]] = {
    "anthropic": {
        "system:execution": [
            "anthropic/claude-sonnet-4-6",
            "anthropic/claude-haiku-4-5",
        ],
        "system:classification": ["anthropic/claude-haiku-4-5"],
    },
    "openai": {
        "system:execution": ["openai/gpt-4o", "openai/gpt-4o-mini"],
        "system:classification": ["openai/gpt-4o-mini"],
    },
    "gemini": {
        "system:execution": ["gemini/gemini-2.5-flash"],
        "system:classification": ["gemini/gemini-2.5-flash"],
    },
}


class ModelRegistry:
    """Manages LLM models and agent-to-model assignments with failover.

    Provides model CRUD, per-agent model assignment with priority ordering,
    system-wide defaults, and resolution logic that falls back from
    agent-specific models to system defaults.
    """

    def __init__(self, storage: StorageBackend, notifier: Any = None) -> None:
        self._storage = storage
        self._notifier = notifier

    def add_model(
        self,
        model_id: str,
        provider: str,
        tier: str,
        input_cost_per_1k: float | None = None,
        output_cost_per_1k: float | None = None,
        max_context: int | None = None,
        status: str = "available",
    ) -> None:
        """Add or update a model in the registry."""
        self._storage.execute(
            """INSERT OR REPLACE INTO llm_models
               (id, provider, tier, input_cost_per_1k, output_cost_per_1k, max_context, status,
                updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))""",
            (
                model_id,
                provider,
                tier,
                input_cost_per_1k,
                output_cost_per_1k,
                max_context,
                status,
            ),
        )
        if self._notifier:
            self._notifier.notify_change(f"Model added: {model_id}", "model_add")

    def remove_model(self, model_id: str) -> None:
        """Remove a model and its assignments from the registry."""
        self._storage.execute(
            "DELETE FROM agent_llm_models WHERE model_id = ?", (model_id,)
        )
        self._storage.execute(
            "DELETE FROM llm_models WHERE id = ?", (model_id,)
        )
        if self._notifier:
            self._notifier.notify_change(f"Model removed: {model_id}", "model_remove")

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        """Get a model by ID. Returns None if not found."""
        row = self._storage.fetchone(
            "SELECT * FROM llm_models WHERE id = ?", (model_id,)
        )
        return dict(row) if row else None

    def list_models(
        self,
        provider: str | None = None,
        tier: str | None = None,
    ) -> list[dict[str, Any]]:
        """List models, optionally filtered by provider and/or tier."""
        conditions: list[str] = []
        params: list[Any] = []
        if provider:
            conditions.append("provider = ?")
            params.append(provider)
        if tier:
            conditions.append("tier = ?")
            params.append(tier)
        where = " AND ".join(conditions)
        sql = "SELECT * FROM llm_models"
        if where:
            sql += f" WHERE {where}"
        sql += " ORDER BY id"
        return [dict(r) for r in self._storage.fetchall(sql, tuple(params))]

    def set_system_defaults(self, models_by_purpose: dict[str, list[str]]) -> None:
        """Set system-wide default models (agent_id=NULL).

        Args:
            models_by_purpose: Mapping of purpose to ordered list of model IDs.
                               Order determines failover priority.
        """
        self._storage.execute(
            "DELETE FROM agent_llm_models WHERE agent_id IS NULL"
        )
        for purpose, model_ids in models_by_purpose.items():
            for priority, model_id in enumerate(model_ids, 1):
                self._storage.execute(
                    """INSERT INTO agent_llm_models (agent_id, model_id, priority, purpose)
                       VALUES (NULL, ?, ?, ?)""",
                    (model_id, priority, purpose),
                )
        if self._notifier:
            self._notifier.notify_change("System defaults updated", "model_defaults")

    def set_agent_models(
        self,
        agent_id: str,
        model_ids: list[str],
        purpose: str = "execution",
    ) -> None:
        """Set agent-specific models with priority order.

        Args:
            agent_id: The agent to configure models for.
            model_ids: Ordered list of model IDs (first = highest priority).
            purpose: The purpose category (execution, classification, etc.).
        """
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
            self._notifier.notify_change(f"Agent models set: {agent_id}", "model_agent")

    def resolve_models(
        self,
        agent_id: str | None,
        purpose: str = "execution",
    ) -> list[str]:
        """Resolve model chain for an agent: agent-specific > system defaults.

        Returns an ordered list of model IDs to try (first = preferred).
        If agent has specific models configured, those are returned.
        Otherwise falls back to system defaults.
        """
        if agent_id is not None:
            rows = self._storage.fetchall(
                """SELECT model_id FROM agent_llm_models
                   WHERE agent_id = ? AND purpose = ? ORDER BY priority""",
                (agent_id, purpose),
            )
            if rows:
                return [r["model_id"] for r in rows]
        # Fall back to system defaults
        rows = self._storage.fetchall(
            """SELECT model_id FROM agent_llm_models
               WHERE agent_id IS NULL AND purpose = ? ORDER BY priority""",
            (purpose,),
        )
        return [r["model_id"] for r in rows]

    def sync_from_litellm(self) -> int:
        """Import model metadata and costs from LiteLLM's built-in database.

        Returns:
            Number of models synced.
        """
        try:
            import litellm
        except ImportError:
            return 0

        count = 0
        for model_id, info in litellm.model_cost.items():
            # Filter out region-specific and third-party gateway variants
            if any(
                prefix in model_id
                for prefix in [
                    "bedrock/",
                    "vertex_ai/",
                    "azure_ai/",
                    "eu.",
                    "us.",
                    "au.",
                    "jp.",
                    "global.",
                    "apac.",
                    "gmi/",
                    "deepinfra/",
                    "replicate/",
                    "openrouter/",
                    "github_copilot/",
                    "heroku/",
                    "perplexity/",
                    "vercel_ai_gateway/",
                    "databricks/",
                ]
            ):
                continue
            # Must be a recognizable provider
            provider = self._guess_provider(model_id)
            if not provider:
                continue
            # Ensure provider prefix (litellm uses bare IDs for some providers)
            if "/" not in model_id:
                model_id = f"{provider}/{model_id}"
            tier = self._classify_tier(model_id)
            self.add_model(
                model_id=model_id,
                provider=provider,
                tier=tier,
                input_cost_per_1k=info.get("input_cost_per_token", 0) * 1000,
                output_cost_per_1k=info.get("output_cost_per_token", 0) * 1000,
                max_context=info.get("max_input_tokens"),
            )
            count += 1
        return count

    def setup_smart_defaults(self, provider: str) -> None:
        """Set up smart default model assignments for a provider.

        Only assigns models that actually exist in the registry.
        """
        defaults = SMART_DEFAULTS.get(provider, {})
        system_models: dict[str, list[str]] = {}
        for key, model_ids in defaults.items():
            if key.startswith("system:"):
                purpose = key.split(":", 1)[1]
                existing = [m for m in model_ids if self.get_model(m)]
                if existing:
                    system_models[purpose] = existing
        if system_models:
            self.set_system_defaults(system_models)

    @staticmethod
    def _guess_provider(model_id: str) -> str | None:
        """Infer provider from model ID string."""
        if "/" in model_id:
            prefix = model_id.split("/")[0]
            if prefix in (
                "anthropic",
                "openai",
                "ollama",
                "gemini",
                "google",
                "mistral",
                "cohere",
            ):
                return prefix
            return None
        if "claude" in model_id:
            return "anthropic"
        if "gpt" in model_id or "o1" in model_id or "o4" in model_id:
            return "openai"
        if "gemini" in model_id:
            return "gemini"
        if "llama" in model_id or "mistral" in model_id:
            return "ollama"
        return None

    @staticmethod
    def _classify_tier(model_id: str) -> str:
        """Classify a model into a cost tier."""
        lower = model_id.lower()
        if "opus" in lower:
            return "opus"
        if "haiku" in lower or "mini" in lower or "flash" in lower:
            return "haiku"
        return "sonnet"
