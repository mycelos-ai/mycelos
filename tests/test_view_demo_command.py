"""/demo view sends one sample of every knowledge view to the chat."""
from __future__ import annotations

from mycelos.chat.slash_commands import _handle_demo
from mycelos.cli.completer import SLASH_COMMANDS
from mycelos.widgets.views import VIEW_TYPES, view_from_payload
from mycelos.widgets.types import KnowledgeView


def test_demo_view_emits_every_catalog_type_once():
    events = _handle_demo(None, ["view"])
    assert [e.type for e in events] == ["widget"] * len(VIEW_TYPES)
    payloads = [e.data["widget"] for e in events]
    assert sorted(p["type"] for p in payloads) == sorted(VIEW_TYPES)
    for payload in payloads:
        # Every sample survives the untrusted round trip unchanged.
        view = view_from_payload(payload)
        assert isinstance(view, KnowledgeView)
        assert view.data == payload["data"]
        assert view.fallback_text == payload["fallback_text"]
        assert payload["reason"]


def test_demo_views_carry_their_alternatives_as_variants():
    payloads = {e.data["widget"]["type"]: e.data["widget"] for e in _handle_demo(None, ["view"])}
    timeline = payloads["timeline"]
    assert timeline["variants"]["graph_slice"]["type"] == "graph_slice"
    for payload in payloads.values():
        for name, variant in payload.get("variants", {}).items():
            assert name in payload["alternatives"]
            assert variant["type"] == name and "variants" not in variant


def test_demo_usage_and_completion_list_view():
    assert "/demo view" in _handle_demo(None, [])
    assert "view" in _handle_demo(None, ["nope"])
    assert "view" in SLASH_COMMANDS["/demo"]["subs"]
