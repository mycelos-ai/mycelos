"""/connector chat command is read-only after the registry unification."""

from __future__ import annotations

import os
from pathlib import Path


def _fake_app(tmp_data_dir: Path):
    """Minimal App stand-in for the /connector dispatcher."""
    from mycelos.app import App
    os.environ.setdefault("MYCELOS_MASTER_KEY", "slash-test-key")
    app = App(tmp_data_dir)
    app.initialize()
    return app


def _extract_text(result) -> str:
    """Normalize slash-command return (string or list of events)."""
    if isinstance(result, str):
        return result
    text = ""
    for ev in result:
        if hasattr(ev, "data") and "content" in ev.data:
            text += ev.data.get("content", "")
    return text


def test_connector_add_is_deprecated(tmp_data_dir: Path) -> None:
    """`/connector add github` returns a pointer to CLI / Web UI, not a setup flow."""
    from mycelos.chat import slash_commands

    app = _fake_app(tmp_data_dir)

    # Prefer the public API if it exists, else fall back to the private one.
    handler = getattr(slash_commands, "handle_slash_command", None)
    if handler is not None:
        result = handler(app, "/connector add github")
    else:
        # Private API — connector dispatcher is `_handle_connector`.
        result = slash_commands._handle_connector(app, ["add", "github"])

    text = _extract_text(result)
    lowered = text.lower()
    assert (
        "not supported in chat" in lowered
        or "use the web ui" in lowered
        or "mycelos connector setup" in lowered
    ), f"Expected deprecation notice, got: {text!r}"


def test_connector_list_still_works(tmp_data_dir: Path) -> None:
    """`/connector list` must still return the available-connectors listing."""
    from mycelos.chat import slash_commands

    app = _fake_app(tmp_data_dir)
    handler = getattr(slash_commands, "handle_slash_command", None)
    if handler is not None:
        result = handler(app, "/connector list")
    else:
        result = slash_commands._handle_connector(app, ["list"])

    text = _extract_text(result)
    assert "connector" in text.lower(), (
        "/connector list should still produce a listing"
    )
