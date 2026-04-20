"""In Docker / two-container mode, _install_packages must refuse."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_install_packages_blocked_in_docker_mode(monkeypatch):
    monkeypatch.setenv("MYCELOS_PROXY_URL", "http://proxy.internal:9110")

    from mycelos.security.permissions import PermissionRequired, _install_packages

    app = MagicMock()
    permission = PermissionRequired(
        action="pip install", target="pillow", reason="agent needs image ops",
        tool="package_install",
    )

    result = _install_packages(app, permission)

    lowered = result.lower()
    assert "disabled" in lowered or "not available" in lowered
    assert "custom image" in lowered or "docker" in lowered

    audit_calls = [
        c for c in app.audit.log.call_args_list
        if c.args and c.args[0] == "package.install_blocked"
    ]
    assert len(audit_calls) == 1
    # Audit details carry the requested target + reason
    details = audit_calls[0].kwargs.get("details") or (audit_calls[0].args[1] if len(audit_calls[0].args) > 1 else {})
    assert details.get("reason") == "docker_mode"

    assert "installed" not in lowered


def test_install_packages_runs_pip_without_proxy_url(monkeypatch):
    """Single-container mode still runs the real pip code path."""
    monkeypatch.delenv("MYCELOS_PROXY_URL", raising=False)

    from mycelos.security import permissions
    from mycelos.security.permissions import PermissionRequired

    import subprocess as _sp
    calls = {}
    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        return R()
    monkeypatch.setattr(_sp, "run", fake_run)

    app = MagicMock()
    permission = PermissionRequired(action="pip install", target="pillow", reason="x", tool="package_install")
    result = permissions._install_packages(app, permission)

    # Pip was invoked
    assert calls.get("cmd") is not None
    assert "pip" in calls["cmd"]
    assert "pillow" in " ".join(str(c) for c in calls["cmd"])
    # Success message (whatever the existing function returns)
    assert result  # non-empty
