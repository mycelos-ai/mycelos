"""ConfigNotifier — minimal interface for registries to trigger config generations.

Follows Least-Privilege: registries only get config + state_manager + audit access.
No credentials, LLM, or proxy access.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mycelos.config")


class ConfigNotifier:
    """Minimal interface for services to trigger config generations + audit."""

    def __init__(self, config: Any, state_manager: Any, audit: Any):
        self._config = config
        self._state_manager = state_manager
        self._audit = audit

    def notify_change(self, description: str, trigger: str = "service") -> None:
        """Create a new config generation after a state change.

        Also emits an audit event named ``"{trigger}.applied"`` so every
        state mutation flowing through this notifier leaves a trace
        (Constitution Rule 1), even if the generation insert itself fails.
        """
        try:
            self._config.apply_from_state(
                self._state_manager,
                description=description,
                trigger=trigger,
            )
        except Exception as e:
            logger.warning("Config generation failed: %s", e)

        try:
            self._audit.log(
                f"{trigger}.applied",
                details={"description": description},
            )
        except Exception as e:
            logger.warning("Audit log failed for %s: %s", trigger, e)

    def log(self, event_type: str, details: dict | None = None) -> None:
        """Log an audit event."""
        self._audit.log(event_type, details=details)
