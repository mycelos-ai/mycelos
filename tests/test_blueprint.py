"""Tests for simplified Blueprint Manager."""
from pathlib import Path

import pytest

from mycelos.config.blueprint import BlueprintManager, RiskLevel
from mycelos.config.generations import ConfigGenerationManager
from mycelos.storage.database import SQLiteStorage


def make_blueprint(db_path: Path) -> BlueprintManager:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    config_mgr = ConfigGenerationManager(storage)
    config_mgr.apply({"version": "0.1.0", "agents": {}}, description="initial")
    return BlueprintManager(config_mgr)


def test_classify_risk_low(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    assert bp.classify_risk({"description": "Updated"}) == RiskLevel.LOW


def test_classify_risk_medium(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    assert bp.classify_risk({"tools": {}}) == RiskLevel.MEDIUM


def test_classify_risk_high(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    assert bp.classify_risk({"agents": {}}) == RiskLevel.HIGH


def test_classify_risk_critical(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    assert bp.classify_risk({"security": {}}) == RiskLevel.CRITICAL


def test_apply_low_risk_auto(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    current = bp._config.get_active_config()
    result = bp.apply({**current, "description": "updated"}, description="low risk")
    assert result["applied"] is True
    assert result["risk"] == RiskLevel.LOW


def test_apply_high_risk_needs_confirm(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    current = bp._config.get_active_config()
    result = bp.apply({**current, "agents": {"new": {}}})
    assert result["applied"] is False
    assert result["risk"] == RiskLevel.HIGH


def test_apply_high_risk_with_confirm(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    current = bp._config.get_active_config()
    result = bp.apply({**current, "agents": {"new": {}}}, confirmed=True)
    assert result["applied"] is True


def test_plan_shows_diff(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    current = bp._config.get_active_config()
    result = bp.plan({**current, "new_key": "val"})
    assert "new_key" in result["diff"]["added"]
