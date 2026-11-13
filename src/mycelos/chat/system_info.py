"""System environment block for the chat system prompt.

Split out of ``chat/service.py``.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger("mycelos.chat.service")


def _build_system_info(now: Any, user_tz: str | None = None) -> str:
    """Build system environment info for the prompt.

    If ``user_tz`` (IANA name like ``Europe/Berlin``) is provided, date
    and time are rendered in the user's timezone — not the container's.
    This is critical inside Docker, where the OS TZ is usually UTC but
    the user lives elsewhere, so "tomorrow 9am" resolved against UTC
    would land in the past and fire the reminder immediately.
    """
    import os
    import platform
    from datetime import datetime as _dt
    from datetime import timezone as _tz
    from pathlib import Path

    # Make `now` aware: naive values are assumed OS-local.
    if getattr(now, "tzinfo", None) is None:
        now_aware = now.astimezone()
    else:
        now_aware = now

    tz_name = ""
    local_now = now_aware
    if user_tz:
        try:
            from zoneinfo import ZoneInfo
            local_now = now_aware.astimezone(ZoneInfo(user_tz))
            tz_name = user_tz
        except Exception:
            _log.debug("Unknown user timezone %r, falling back to OS tz", user_tz, exc_info=True)

    if not tz_name:
        # Fall back to OS offset (e.g. "UTC", "UTC+2").
        try:
            off = now_aware.utcoffset()
            hours = int((off.total_seconds() if off else 0) // 3600)
            tz_name = "UTC" if hours == 0 else f"UTC{hours:+d}"
        except Exception:
            tz_name = "UTC"

    parts = ["## System Environment"]
    parts.append(f"Date: {local_now.strftime('%A, %d. %B %Y')}")
    parts.append(f"Time: {local_now.strftime('%H:%M')} ({tz_name})")
    parts.append(f"Timezone: {tz_name}")
    utc_now = _dt.now(_tz.utc)
    parts.append(f"UTC now: {utc_now.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    parts.append(f"OS: {platform.system()} {platform.release()} ({platform.machine()})")
    parts.append(f"Python: {platform.python_version()}")

    # Detect Docker
    if Path("/.dockerenv").exists() or os.environ.get("DOCKER_CONTAINER"):
        parts.append("Environment: Docker Container")
    elif os.environ.get("KUBERNETES_SERVICE_HOST"):
        parts.append("Environment: Kubernetes Pod")
    else:
        parts.append("Environment: Local (bare metal)")

    # Home directory (not sensitive)
    parts.append(f"Home: {Path.home()}")

    # Shell
    shell = os.environ.get("SHELL", "unknown")
    parts.append(f"Shell: {Path(shell).name if shell != 'unknown' else 'unknown'}")

    # Node.js availability (for MCP connectors)
    import shutil
    if shutil.which("npx"):
        parts.append("Node.js: available (MCP connectors supported)")
    else:
        parts.append("Node.js: not found (MCP connectors need it)")

    return "\n".join(parts)
