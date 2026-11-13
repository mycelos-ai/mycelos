"""Backward-compatible shims, system commands and setup hints kept on ChatService.

Mixin for :class:`mycelos.chat.service.ChatService`; split out of
``chat/service.py`` without behavior changes.
"""

from __future__ import annotations

import logging

from mycelos.chat.events import (
    ChatEvent,
    agent_event,
    done_event,
    system_response_event,
)

_log = logging.getLogger("mycelos.chat.service")


class CompatShimsMixin:
    """Backward-compatible shims, system commands and setup hints kept on ChatService."""

    def _handle_system_command(
        self, session_id: str, message: str, conversation: list[dict]
    ) -> list[ChatEvent]:
        """Handle a system command directly without LLM."""
        from mycelos.chat.context import handle_system_command as _handle_system_command

        response = _handle_system_command(self._app, message)
        conversation.append({"role": "assistant", "content": response})
        self._app.session_store.append_message(session_id, role="assistant", content=response)

        return [
            agent_event("System"),
            system_response_event(response),
            done_event(),
        ]

    def _get_system_status(self) -> dict:
        """Shim: delegates to mycelos.tools.system.execute_system_status."""
        from mycelos.tools.system import execute_system_status
        return execute_system_status({}, {"app": self._app})

    def _filesystem_read(self, path: str) -> dict:
        """Shim: delegates to mycelos.tools.filesystem.execute_filesystem_read."""
        from mycelos.tools.filesystem import execute_filesystem_read
        return execute_filesystem_read({"path": path}, {"app": self._app})

    def _filesystem_write(self, path: str, content: str) -> dict:
        """Shim: delegates to mycelos.tools.filesystem.execute_filesystem_write."""
        from mycelos.tools.filesystem import execute_filesystem_write
        return execute_filesystem_write({"path": path, "content": content}, {"app": self._app})

    def _filesystem_list(self, path: str) -> dict:
        """Shim: delegates to mycelos.tools.filesystem.execute_filesystem_list."""
        from mycelos.tools.filesystem import execute_filesystem_list
        return execute_filesystem_list({"path": path}, {"app": self._app})

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Shim: delegates to mycelos.tools.filesystem._normalize_path."""
        from mycelos.tools.filesystem import _normalize_path
        return _normalize_path(path)

    def _get_follow_up_suggestions(self) -> str:
        """Check what setups the user hasn't done yet and suggest them."""
        suggestions = []

        # Check Telegram — look in channels table (not just credentials)
        try:
            from mycelos.channels.telegram import load_channel_config
            telegram_cfg = load_channel_config(self._app.storage)
            if telegram_cfg and telegram_cfg.get("status") == "active":
                suggestions.append(
                    "Telegram is ACTIVE and working. The user can message the bot from their phone. "
                    "When the user asks for scheduled messages or notifications, you CAN deliver them via Telegram."
                )
            else:
                # Metadata-only check — get_credential isn't available in
                # two-container mode. A row in credential_list is enough
                # to know the token has been stored.
                has_token = any(
                    (c.get("service") or "").lower() == "telegram"
                    for c in self._app.credentials.list_credentials(user_id="default")
                )
                if not has_token:
                    suggestions.append(
                        "The user has NOT set up Telegram yet. "
                        "If it comes up naturally, mention: "
                        "'Du kannst mich auch ueber Telegram nutzen — /connector add telegram'"
                    )
        except Exception:
            _log.debug("Failed to check Telegram setup status", exc_info=True)

        # Check mounts
        try:
            from mycelos.security.mounts import MountRegistry
            mounts = MountRegistry(self._app.storage)
            if not mounts.list_mounts():
                suggestions.append(
                    "The user has NO directories mounted yet. "
                    "If they ask about files, just try to access them — "
                    "the system will automatically prompt for permission."
                )
        except Exception:
            _log.debug("Failed to check mount status", exc_info=True)

        # Check scheduled tasks
        try:
            tasks = self._app.schedule_manager.list_tasks(status="active")
            if not tasks:
                suggestions.append(
                    "The user has NO scheduled tasks. "
                    "If they mention 'daily', 'every morning', or 'recurring', "
                    "suggest scheduling with /schedule add"
                )
        except Exception:
            _log.debug("Failed to check scheduled tasks", exc_info=True)

        if not suggestions:
            return ""

        return "## Setup Suggestions (mention naturally, don't force)\n" + "\n".join(
            f"- {s}" for s in suggestions
        )

    def _try_save_name(self, user_input: str) -> None:
        """Try to extract and save user name from input."""
        words = user_input.strip().split()
        if 1 <= len(words) <= 3 and all(w[0].isupper() for w in words if w):
            name = user_input.strip()
            self._app.memory.set("default", "system", "user.name", name, created_by="chat")
            self._app.audit.log("user.name_set", details={"name": name})
            self._app.audit.log("onboarding.step_1", details={"name": name})
