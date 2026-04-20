"""Model Updater — deterministic system handler.

Runs once per day. Refreshes the LLM model registry from LiteLLM's remote
cost map so newly-released provider models (e.g. a fresh Opus / GPT)
appear in Settings without a ``pip install --upgrade litellm``. Also
checks GitHub for a new Mycelos release so users know to run
``docker compose pull``.

This handler is pure Python — zero LLM calls, zero tool loop. It exists
as a deliberate proof point for "deterministic programs are first-class
agents" — a workflow can be valuable without a language model.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mycelos.model_updater")

# GitHub repo that publishes Mycelos releases. Unauthenticated endpoint,
# 60 req/h rate limit — we call once per day. No telemetry: GitHub only
# sees the IP and User-Agent of the request.
_RELEASE_API = "https://api.github.com/repos/mycelos-ai/mycelos/releases/latest"

# Memory keys for update-check state and opt-out toggle.
_UPDATE_OPTOUT_KEY = "system.check_for_updates"
_UPDATE_STATE_KEY = "system.update.latest"


class ModelUpdaterHandler:
    """System handler. Not user-facing, not registered in the sidebar."""

    def __init__(self, app: Any) -> None:
        self._app = app

    @property
    def agent_id(self) -> str:
        return "model-updater"

    def run(self, user_id: str = "default") -> dict[str, Any]:
        """Refresh the model registry from the LiteLLM remote cost map AND
        check for a new Mycelos release.

        Restricts the sync to providers the user has credentials for — no
        point showing 200 Gemini variants if the user only has an Anthropic
        key. Ollama is always included when an Ollama endpoint is configured
        (credential-less provider).

        Returns ``{"added": [...], "updated_count": N, "total": N,
        "update_available": bool, "latest_version": "..."}``.
        When new models are discovered, emits a ``models.discovered`` audit
        event so the Doctor Activity panel surfaces the event.
        """
        # App-update check runs alongside the model refresh. Failure here
        # must never block the model refresh — it's best-effort.
        update_info = self._check_app_update()

        providers = self._configured_providers(user_id)
        if not providers:
            # Nothing configured — skip the model refresh but still return the
            # update-check result so the UI can render it.
            return {
                "added": [], "updated_count": 0, "total": 0,
                "update_available": update_info.get("update_available", False),
                "latest_version": update_info.get("latest_version"),
                "current_version": update_info.get("current_version"),
            }

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
            "skipped_legacy": result.get("skipped_legacy", []),
            "total": result.get("total", 0),
            "providers_checked": providers,
            "update_available": update_info.get("update_available", False),
            "latest_version": update_info.get("latest_version"),
            "current_version": update_info.get("current_version"),
        }

    # ── App update check ───────────────────────────────────────────

    def _check_app_update(self) -> dict[str, Any]:
        """Query GitHub for the latest Mycelos release tag.

        Opt-out: users can disable via the ``system.check_for_updates``
        memory entry (Settings UI writes it). Default is enabled so
        first-time users get the signal without extra configuration.

        Returns dict with ``update_available``, ``latest_version``,
        ``current_version``, ``release_url``, ``published_at``. Best-effort:
        any error logs a warning and returns the last-known state from memory
        (or an empty dict). Never raises.
        """
        if self._is_update_check_disabled():
            return {}

        current = self._current_version()
        if not current:
            logger.debug("Update check: could not determine local version; skipping")
            return {}

        try:
            import httpx
            import json as _json
            from mycelos.connectors import http_tools as _http_tools

            _headers = {"Accept": "application/vnd.github+json", "User-Agent": "mycelos-updater"}
            if _http_tools._proxy_client is not None:
                raw = _http_tools._proxy_client.http_get(_RELEASE_API, headers=_headers, timeout=5)
                status = raw.get("status", 0)
                if status == 404:
                    # No release published yet on this repo — not an error.
                    return {}
                if status == 0 or status >= 400:
                    raise RuntimeError(raw.get("error", f"HTTP {status}"))
                data = _json.loads(raw.get("body", "{}")) if raw.get("body") else {}
            else:
                resp = httpx.get(_RELEASE_API, timeout=5, headers=_headers)
                if resp.status_code == 404:
                    # No release published yet on this repo — not an error.
                    return {}
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.debug("Update check failed: %s", e)
            return self._load_cached_update_state()

        latest = (data.get("tag_name") or "").lstrip("v")
        release_url = data.get("html_url") or ""
        published_at = data.get("published_at") or ""

        if not latest:
            return {}

        update_available = self._is_newer(latest, current)
        state = {
            "update_available": update_available,
            "latest_version": latest,
            "current_version": current,
            "release_url": release_url,
            "published_at": published_at,
        }

        # Decide whether to audit BEFORE persisting — _should_audit_update
        # compares against the previous cached tag, not the one we're about
        # to write.
        should_audit = update_available and self._should_audit_update(latest)

        # Persist so the UI can read without re-querying GitHub.
        self._save_update_state(state)

        if update_available:
            if should_audit:
                self._app.audit.log(
                    "mycelos.update_available",
                    details={
                        "current": current,
                        "latest": latest,
                        "url": release_url,
                    },
                )
            logger.info("Mycelos update available: %s → %s", current, latest)

        return state

    def _is_update_check_disabled(self) -> bool:
        try:
            value = self._app.memory.get(
                user_id="default", scope="system", key=_UPDATE_OPTOUT_KEY
            )
        except Exception:
            return False
        if value is None:
            return False  # default: check enabled
        return str(value).lower() in {"0", "false", "off", "no"}

    @staticmethod
    def _current_version() -> str | None:
        try:
            from importlib.metadata import version
            return version("mycelos")
        except Exception:
            return None

    @staticmethod
    def _is_newer(latest: str, current: str) -> bool:
        """Compare two semver-ish version strings.

        Uses ``packaging.version`` when available (handles pre-releases
        correctly); falls back to tuple-of-ints on the numeric segments.
        """
        try:
            from packaging.version import Version
            return Version(latest) > Version(current)
        except Exception:
            def parts(v: str) -> tuple[int, ...]:
                out: list[int] = []
                for segment in v.split("."):
                    digits = "".join(ch for ch in segment if ch.isdigit())
                    out.append(int(digits) if digits else 0)
                return tuple(out)
            return parts(latest) > parts(current)

    def _load_cached_update_state(self) -> dict[str, Any]:
        try:
            cached = self._app.memory.get(
                user_id="default", scope="system", key=_UPDATE_STATE_KEY
            )
            if cached:
                import json as _json
                # memory.get already deserializes JSON when possible, so
                # cached could already be a dict.
                if isinstance(cached, dict):
                    return cached
                return _json.loads(cached)
        except Exception:
            pass
        return {}

    def _save_update_state(self, state: dict[str, Any]) -> None:
        try:
            self._app.memory.set(
                user_id="default",
                scope="system",
                key=_UPDATE_STATE_KEY,
                value=state,
            )
        except Exception as e:
            logger.debug("Could not persist update state: %s", e)

    def _should_audit_update(self, latest_tag: str) -> bool:
        """Audit once per new latest tag, not once per daily poll."""
        cached = self._load_cached_update_state()
        return cached.get("latest_version") != latest_tag

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
            if self._app.memory.get(
                user_id="default", scope="system", key="provider.ollama.url"
            ):
                providers.add("ollama")
        except Exception:
            pass

        return sorted(providers)
