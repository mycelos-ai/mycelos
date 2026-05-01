"""Doctor tools — read-only diagnostic queries available to the Doctor agent."""

from __future__ import annotations

from typing import Any

from mycelos.tools.registry import ToolPermission


DOCTOR_CHECK_TELEGRAM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "doctor_check_telegram",
        "description": (
            "Check Telegram channel state: whether the channel is configured, "
            "its status, whether a token credential is stored, whether a chat_id "
            "is mapped, and the allowlist. No secrets are returned."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

DOCTOR_CHECK_REMINDERS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "doctor_check_reminders",
        "description": (
            "List overdue/due reminder tasks and the timestamp of the last "
            "'reminder.sent' audit event. Use to diagnose missed notifications."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

DOCTOR_CHECK_SCHEDULES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "doctor_check_schedules",
        "description": (
            "List scheduled tasks with their last_run/next_run, plus a missed-runs "
            "count. Use to diagnose workflows that should have triggered but didn't."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

DOCTOR_CHECK_CREDENTIALS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "doctor_check_credentials",
        "description": (
            "List which services have credentials stored. Returns service+label "
            "pairs only — never secret values."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

DOCTOR_QUERY_AUDIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "doctor_query_audit",
        "description": (
            "Query recent audit events. Filter by event_type substring (e.g. "
            "'reminder', 'workflow.run.started') and/or since (ISO timestamp). "
            "Returns the most recent matches first. The audit log is your timeline."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "description": "Substring to match against event_type (case-sensitive LIKE %x%).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max events to return. Default 20.",
                },
                "since": {
                    "type": "string",
                    "description": "ISO timestamp lower bound (e.g. '2026-04-30').",
                },
            },
        },
    },
}

DOCTOR_CONFIG_HISTORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "doctor_config_history",
        "description": (
            "List recent config generations with their description, trigger, and "
            "which one is currently active. Use to find the change that broke things."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max generations to return. Default 10.",
                },
            },
        },
    },
}


def execute_doctor_check_telegram(args: dict, context: dict) -> Any:
    from mycelos.doctor.tools import doctor_check_telegram
    return doctor_check_telegram(context["app"])


def execute_doctor_check_reminders(args: dict, context: dict) -> Any:
    from mycelos.doctor.tools import doctor_check_reminders
    return doctor_check_reminders(context["app"])


def execute_doctor_check_schedules(args: dict, context: dict) -> Any:
    from mycelos.doctor.tools import doctor_check_schedules
    return doctor_check_schedules(context["app"])


def execute_doctor_check_credentials(args: dict, context: dict) -> Any:
    from mycelos.doctor.tools import doctor_check_credentials
    return doctor_check_credentials(context["app"])


def execute_doctor_query_audit(args: dict, context: dict) -> Any:
    from mycelos.doctor.tools import doctor_query_audit
    return doctor_query_audit(
        context["app"],
        event_type=args.get("event_type"),
        limit=int(args.get("limit", 20)),
        since=args.get("since"),
    )


def execute_doctor_config_history(args: dict, context: dict) -> Any:
    from mycelos.doctor.tools import doctor_config_history
    return doctor_config_history(context["app"], limit=int(args.get("limit", 10)))


def register(registry: type) -> None:
    """Register all doctor diagnostic tools.

    All read-only → ToolPermission.OPEN so the Doctor agent (and any other
    agent) can call them. They never mutate state and never return secret
    values, so wide access is safe.
    """
    registry.register(
        "doctor_check_telegram", DOCTOR_CHECK_TELEGRAM_SCHEMA,
        execute_doctor_check_telegram, ToolPermission.OPEN,
        concurrent_safe=True, category="doctor",
    )
    registry.register(
        "doctor_check_reminders", DOCTOR_CHECK_REMINDERS_SCHEMA,
        execute_doctor_check_reminders, ToolPermission.OPEN,
        concurrent_safe=True, category="doctor",
    )
    registry.register(
        "doctor_check_schedules", DOCTOR_CHECK_SCHEDULES_SCHEMA,
        execute_doctor_check_schedules, ToolPermission.OPEN,
        concurrent_safe=True, category="doctor",
    )
    registry.register(
        "doctor_check_credentials", DOCTOR_CHECK_CREDENTIALS_SCHEMA,
        execute_doctor_check_credentials, ToolPermission.OPEN,
        concurrent_safe=True, category="doctor",
    )
    registry.register(
        "doctor_query_audit", DOCTOR_QUERY_AUDIT_SCHEMA,
        execute_doctor_query_audit, ToolPermission.OPEN,
        concurrent_safe=True, category="doctor",
    )
    registry.register(
        "doctor_config_history", DOCTOR_CONFIG_HISTORY_SCHEMA,
        execute_doctor_config_history, ToolPermission.OPEN,
        concurrent_safe=True, category="doctor",
    )
