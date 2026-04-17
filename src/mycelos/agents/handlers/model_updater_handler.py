"""Model Updater — deterministic system handler.

Runs once per day. Refreshes the LLM model registry from LiteLLM's remote
cost map so newly-released provider models (e.g. a fresh Opus / GPT)
appear in Settings without a ``pip install --upgrade litellm``.

This handler is pure Python — zero LLM calls, zero tool loop. It exists
as a deliberate proof point for "deterministic programs are first-class
agents" — a workflow can be valuable without a language model.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mycelos.model_updater")


class ModelUpdaterHandler:
    """System handler. Not user-facing, not registered in the sidebar."""

    def __init__(self, app: Any) -> None:
        self._app = app

    @property
    def agent_id(self) -> str:
        return "model-updater"

    def run(self, user_id: str = "default") -> dict[str, Any]:
        """Refresh the model registry from the LiteLLM remote cost map.

        Restricts the sync to providers the user has credentials for — no
        point showing 200 Gemini variants if the user only has an Anthropic
        key. Ollama is always included when an Ollama endpoint is configured
        (credential-less provider).

        Returns ``{"added": [...], "updated_count": N, "total": N}``.
        When new models are discovered, emits a ``models.discovered`` audit
        event so the Doctor Activity panel surfaces the event and users
        can navigate to Settings to review them.
        """
        providers = self._configured_providers(user_id)
        if not providers:
            # Nothing configured — nothing to do.
            return {"added": [], "updated_count": 0, "total": 0}

        try:
            result = self._app.model_registry.sync_from_litellm(
                prefer_remote=True,
                providers=providers,
            )
        except Exception as e:
            logger.error("Model refresh FAILED: %s", e, exc_info=True)
            self._app.audit.log(
                "models.refresh_failed",
                details={"error": str(e)[:200]},
            )
            return {"added": [], "updated_count": 0, "total": 0, "error": str(e)}

        added = result.get("added", [])
        updated = result.get("updated", [])

        if added:
            self._app.audit.log(
                "models.discovered",
                details={"added": added[:50], "count": len(added)},
            )
            logger.info("Model refresh: %d new models (%s)", len(added), ", ".join(added[:5]))
        else:
            logger.debug("Model refresh: no new models")

        return {
            "added": added,
            "updated_count": len(updated),
            "total": result.get("total", 0),
            "providers_checked": providers,
        }

    def _configured_providers(self, user_id: str) -> list[str]:
        """Return provider IDs the user has credentials for, plus Ollama
        when its endpoint is set (credential-less)."""
        providers: set[str] = set()
        try:
            # Credential proxy carries one row per (service, label)
            creds = self._app.credentials.list_credentials(user_id=user_id)
            for c in creds:
                service = (c.get("service") or "").lower()
                if service:
                    providers.add(service)
        except Exception as e:
            logger.warning("Could not enumerate credentials for model refresh: %s", e)

        # Ollama is credential-less — include it when an endpoint is recorded
        # in memory (set during provider setup).
        try:
            if self._app.memory.get("provider.ollama.url", scope="system"):
                providers.add("ollama")
        except Exception:
            pass

        return sorted(providers)
