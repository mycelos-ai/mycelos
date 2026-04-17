"""Shared audit event classification.

Used by the CLI (`mycelos db audit --suspicious --quiet`) and the Doctor
Activity panel (`/api/audit/activity?level=...`) so the definition of
"suspicious" and "noisy" stays in one place.
"""

from __future__ import annotations


# Security-relevant events. When any of these appears, the user should probably
# look at it. Keep this list narrow on purpose — broader events (workflow.registered,
# model.added) are not suspicious on their own and would drown the signal.
SUSPICIOUS_EVENT_TYPES: tuple[str, ...] = (
    "config.tamper_detected",
    "tool.blocked",
    "policy.denied",
    "credential.rotate",
    "credential.bootstrap_failed",
    "agent.denied",
    "sandbox.escape_attempt",
    "ssrf.blocked",
    "telegram.user_bootstrapped",
    "capability.expired",
    "chat.security_gate.blocked",
)

# Trailing wildcards — an event ending with one of these suffixes is suspicious
# even if the full name is not in SUSPICIOUS_EVENT_TYPES. Covers *.flood_blocked,
# *.denied, *.tamper_detected across subsystems.
SUSPICIOUS_EVENT_SUFFIXES: tuple[str, ...] = (
    ".flood_blocked",
    ".denied",
    ".tamper_detected",
)


# High-volume, low-information events. These are emitted on a schedule or per
# request and would drown the interesting events if not filtered out.
NOISY_EVENT_TYPES: tuple[str, ...] = (
    "reminder.tick",
    "scheduler.tick",
    "session.heartbeat",
    "llm.usage",
)


def is_suspicious(event_type: str) -> bool:
    """Return True if an audit event is considered security-relevant."""
    if event_type in SUSPICIOUS_EVENT_TYPES:
        return True
    for suffix in SUSPICIOUS_EVENT_SUFFIXES:
        if event_type.endswith(suffix):
            return True
    return False


def is_noisy(event_type: str) -> bool:
    """Return True if an audit event is high-volume, low-information noise."""
    return event_type in NOISY_EVENT_TYPES
