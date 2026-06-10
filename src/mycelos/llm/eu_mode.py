"""Server-side EU-mode state.

EU mode is configuration that changes which providers may handle user data,
so it must be persisted server-side and audited — not held in browser
localStorage. Stored in the system memory scope; every change logs an audit
event so a GDPR-bound user can demonstrate when EU mode was enabled.
"""
from __future__ import annotations

from typing import Any

_SCOPE = "system"
_KEY = "eu_mode"


def get_eu_mode(app: Any, user_id: str) -> bool:
    """Return whether EU mode is enabled for the user (default False)."""
    value = app.memory.get(user_id, _SCOPE, _KEY)
    return bool(value)


def set_eu_mode(app: Any, user_id: str, enabled: bool) -> None:
    """Enable/disable EU mode and audit the change."""
    app.memory.set(user_id, _SCOPE, _KEY, bool(enabled))
    app.audit.log(
        "eu_mode.enabled" if enabled else "eu_mode.disabled",
        details={"enabled": bool(enabled)},
        user_id=user_id,
    )
