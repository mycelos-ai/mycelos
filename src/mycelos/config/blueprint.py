"""Simplified Blueprint Manager — risk classification + user confirmation.

Phase 4a: plan + apply (no Guard Period — that needs Gateway).
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from mycelos.config.generations import ConfigGenerationManager


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_RISK_KEYS: dict[str, set[str]] = {
    "critical": {"security", "guardian", "credential_proxy"},
    "high": {"agents", "policies", "capabilities", "workflows", "scheduled_tasks"},
    "medium": {"tools", "llm", "default_model", "provider", "connectors"},
}


class BlueprintManager:
    """Simplified Blueprint Lifecycle. No Guard Period (needs Gateway)."""

    def __init__(self, config: ConfigGenerationManager) -> None:
        self._config = config

    def classify_risk(self, changes: dict[str, Any]) -> RiskLevel:
        """Classify risk based on changed keys."""
        change_keys = set(changes.keys())
        for level in ("critical", "high", "medium"):
            if change_keys & _RISK_KEYS.get(level, set()):
                return RiskLevel(level)
        return RiskLevel.LOW

    def plan(self, new_config: dict[str, Any]) -> dict[str, Any]:
        """Show what will change + risk level."""
        current = self._config.get_active_config() or {}
        active_id = self._config.get_active_generation_id()
        added = {k: v for k, v in new_config.items() if k not in current}
        removed = {k: v for k, v in current.items() if k not in new_config}
        changed = {
            k: (current[k], new_config[k])
            for k in current
            if k in new_config and current[k] != new_config[k]
        }
        all_keys = set(added) | set(removed) | set(changed)
        risk = self.classify_risk({k: None for k in all_keys})
        return {
            "risk": risk,
            "diff": {"added": added, "removed": removed, "changed": changed},
            "requires_confirmation": risk
            in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL),
            "current_generation": active_id,
        }

    def apply(
        self,
        new_config: dict[str, Any],
        description: str = "",
        trigger: str = "manual",
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Apply a config change. LOW auto-applies, MEDIUM+ needs confirmed=True."""
        plan_result = self.plan(new_config)
        if plan_result["requires_confirmation"] and not confirmed:
            return {
                "applied": False,
                "risk": plan_result["risk"],
                "reason": "Requires user confirmation",
                "plan": plan_result,
            }
        gen_id = self._config.apply(
            new_config, description=description, trigger=trigger
        )
        return {"applied": True, "risk": plan_result["risk"], "generation_id": gen_id}
