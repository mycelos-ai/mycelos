"""Permission system — system-level permission prompts for tool execution.

When a tool needs a permission the user hasn't granted (e.g., filesystem
access, connector setup, package installation), the system raises
PermissionRequired. The ChatService catches this, shows a prompt to
the user, and re-executes the tool after approval.

The LLM never sees this flow — it just gets the tool result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("mycelos.security.permissions")


@dataclass
class PermissionRequired(Exception):
    """Raised when a tool needs user permission to proceed.

    The ChatService catches this and shows a system-level prompt.
    The LLM is paused and never sees the permission interaction.
    """

    tool: str                       # tool that triggered the permission
    action: str                     # what needs to happen (e.g., "mount add ~/Downloads --rw")
    reason: str                     # human-readable reason shown to user
    target: str = ""                # the resource (path, connector, package)
    action_type: str = "mount"      # mount, policy, connector, package
    original_args: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"PermissionRequired: {self.action} — {self.reason}"


def grant_permission(
    app: Any,
    permission: PermissionRequired,
    decision: str,
    agent_id: str | None = None,
    session_grants: set | None = None,
) -> str:
    """Execute the granted permission action.

    Decisions:
        "allow_session" — execute + remember for this session (default, Y/Enter)
        "always_allow"  — execute + save permanent policy in DB (A)
        "deny"          — block this one time (N)
        "never_allow"   — block + save permanent deny in DB (!)

    Args:
        app: Mycelos App instance.
        permission: The PermissionRequired that was raised.
        decision: One of the above decision strings.
        agent_id: The agent requesting permission (for scoped policies).
        session_grants: Set to add session-scoped grants to (for "allow_session").

    Returns:
        Result message from the executed action.
    """
    from mycelos.chat.slash_commands import handle_slash_command

    if decision in ("deny", "never_allow"):
        if decision == "never_allow":
            app.policy_engine.set_policy("default", agent_id, permission.tool, "never")
            app.config.apply_from_state(
                app.state_manager,
                description=f"Permission never: {permission.tool} for {agent_id}",
                trigger="permission",
            )
            app.audit.log("permission.never", details={
                "tool": permission.tool, "action": permission.action,
                "agent_id": agent_id,
            })
        return "Permission denied."

    # Execute the action based on action_type
    if permission.action_type == "package":
        result = _install_packages(app, permission)
    else:
        result = handle_slash_command(app, f"/{permission.action}")

    if decision == "allow_all_always":
        # Global — agent_id=None means all agents
        app.policy_engine.set_policy("default", None, permission.tool, "always")
        app.config.apply_from_state(
            app.state_manager,
            description=f"Permission allow all: {permission.tool}",
            trigger="permission",
        )
        app.audit.log("permission.allow_all", details={
            "tool": permission.tool, "action": permission.action,
            "agent_id": None,
        })
    elif decision == "always_allow":
        # Agent-scoped permanent
        app.policy_engine.set_policy("default", agent_id, permission.tool, "always")
        app.config.apply_from_state(
            app.state_manager,
            description=f"Permission always: {permission.tool} for {agent_id}",
            trigger="permission",
        )
        app.audit.log("permission.always_allow", details={
            "tool": permission.tool, "action": permission.action,
            "agent_id": agent_id,
        })
    else:
        # Session-scoped (allow_session)
        if session_grants is not None:
            grant_key = f"{agent_id or '*'}:{permission.tool}:{permission.target}"
            session_grants.add(grant_key)
        app.audit.log("permission.allow_session", details={
            "tool": permission.tool, "action": permission.action,
            "agent_id": agent_id,
        })

    return result


def _install_packages(app: Any, permission: PermissionRequired) -> str:
    """Install Python packages via pip and log the action.

    Parses package names from the permission target (comma-separated list)
    and runs pip install using the current interpreter.

    Args:
        app: Mycelos App instance (for audit logging).
        permission: The PermissionRequired with target containing package names.

    Returns:
        Human-readable result message.
    """
    import os as _os
    if _os.environ.get("MYCELOS_PROXY_URL", "").strip():
        # Two-container Docker mode: runtime pip install is disabled.
        # The gateway container has no internet route in Phase 1b, and a
        # runtime install without allow-list validation is itself a
        # supply-chain risk (P0 item from the April security review).
        # Phase 1c will add validated proxy-mediated installs.
        try:
            app.audit.log(
                "package.install_blocked",
                details={
                    "target": (permission.target or "")[:200],
                    "reason": "docker_mode",
                },
            )
        except Exception:
            pass
        return (
            "Package installation is disabled in Docker deployments. "
            "To add dependencies, build a custom image — "
            "see docs/deployment/custom-image.md."
        )

    # existing single-container code continues unchanged below
    import subprocess
    import sys

    packages = [p.strip() for p in permission.target.split(",") if p.strip()]
    if not packages:
        return "No packages to install."

    logger.info("Installing packages: %s", packages)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", *packages],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            logger.error("pip install failed: %s", proc.stderr[:500])
            return f"Package installation failed: {proc.stderr[:300]}"

        app.audit.log("package.installed", details={"packages": packages})
        logger.info("Successfully installed packages: %s", packages)
        return f"Installed packages: {', '.join(packages)}"

    except subprocess.TimeoutExpired:
        return "Package installation timed out (120s)."
    except Exception as e:
        return f"Package installation error: {e}"
