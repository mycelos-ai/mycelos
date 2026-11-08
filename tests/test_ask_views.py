"""Answering questions with knowledge views (Package 8, slice 4: Ask)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.knowledge import ask_views
from mycelos.knowledge.ask_views import (
    Gathered,
    build_answer,
    classify_intent,
    clean_structure,
    compute_facts,
    requested_view,
)


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-ask-views"
        a = App(Path(tmp))
        a.initialize()
        a._llm = None
        yield a


def _created(app, path: str, when: str) -> None:
    app.storage.execute("UPDATE knowledge_notes SET created_at=? WHERE path=?", (when, path))


@pytest.fixture
def mueller(app):
    """Scenario 2 of the spec: what is open with client Müller."""
    kb = app.knowledge_base
    client = kb.create_topic("Müller GmbH")
    projects = kb.create_topic("Projekte")
    mail = kb.write(title="Mail Müller: Q3 Belege fehlen", content="Müller Belege", topic=client)
    call = kb.write(title="Telefonat Müller Q3 Abschluss", content="Müller Abschluss",
                    topic=client, links=[mail])
    task = kb.write(title="Q3 Abschluss Müller", content="Müller Frist", type="task",
                    due="2099-11-15", topic=projects)
    _created(app, call, "2026-10-28T10:00:00Z")
    _created(app, mail, "2026-11-03T10:00:00Z")
    return {"mail": mail, "call": call, "task": task, "client": client, "projects": projects}


def _ask(app, question: str, query: str = "Müller", **kwargs):
    return build_answer(ask_views.gather(app, query), question=question, **kwargs)


class TestIntent:
    @pytest.mark.parametrize("question,intent", [
        ("Was ist offen bei Müller?", "status"),
        ("What is open with client Müller?", "status"),
        ("Wie hängt Müller mit dem Q3-Abschluss zusammen?", "relation"),
        ("How does Müller relate to the closing?", "relation"),
        ("Gib mir einen Überblick über Onboarding", "overview"),
        ("Vergleich DATEV und Lexware", "compare"),
        ("DATEV vs Lexware", "compare"),
        ("Wie viele Notizen habe ich zu Steuern?", "count"),
        ("How many tasks per project?", "count"),
        ("Wie ist der Ablauf beim Onboarding?", "process"),
        ("Stimmt es, dass die Frist verschoben wurde?", "verify"),
        ("Do my sources contradict each other?", "verify"),
    ])
    def test_rules(self, question, intent):
        assert classify_intent(question) == intent

    def test_model_intent_only_when_no_rule_matches(self):
        assert classify_intent("Müller?", "relation") == "relation"
        assert classify_intent("Was ist offen bei Müller?", "relation") == "status"

    def test_unknown_model_intent_is_ignored(self):
        assert classify_intent("Müller?", "approval_card") == "overview"
        assert classify_intent("Müller?", None) == "overview"

    def test_short_words_do_not_match_inside_others(self):
        # "vs" must not match inside a word, "due" not inside "residue".
        assert classify_intent("Notizen zu Kvsoft") == "overview"
        assert classify_intent("residue notes") == "overview"

    def test_named_views(self):
        assert requested_view("Zeig mir Müller als Zeitleiste") == "timeline"
        assert requested_view("as a mind map please") == "mind_map"
        assert requested_view("Was ist offen bei Müller?") == ""


class TestFacts:
    def test_a_single_day_is_not_a_timeline(self):
        g = Gathered(notes=[
            {"path": "a", "title": "A", "created_at": "2026-11-01T08:00:00Z"},
            {"path": "b", "title": "B", "created_at": "2026-11-01T09:00:00Z"},
        ])
        assert compute_facts(g)["dated_share"] == 0.0

    def test_due_dates_count(self):
        g = Gathered(notes=[
            {"path": "a", "title": "A", "created_at": "2026-11-01T08:00:00Z"},
            {"path": "b", "title": "B", "created_at": "2026-11-01T09:00:00Z", "due": "2026-11-20"},
        ])
        assert compute_facts(g)["dated_share"] == 1.0

    def test_evidence_relations(self):
        g = Gathered(
            notes=[{"path": "a", "title": "A"}, {"path": "b", "title": "B"}],
            relations=[{"from": "a", "to": "b", "kind": "contradicts", "reason": "dates differ"}],
        )
        facts = compute_facts(g)
        assert facts["has_contradicts"] is True
        assert facts["entity_count"] == 2


class TestAnswer:
    def test_scenario_status_is_a_timeline_with_a_graph_switch(self, app, mueller):
        result = _ask(app, "Was ist offen bei Müller?")
        view = result.view
        assert result.intent == "status"
        assert view.type == "timeline"
        assert "graph_slice" in view.alternatives
        assert view.alternatives[-1] == "text"
        events = view.data["events"]
        assert [e["date"] for e in events] == sorted(e["date"] for e in events)
        due = [e for e in events if e["kind"] == "due"]
        assert due and due[0]["text"] == "Q3 Abschluss Müller"
        assert {r.path for r in view.refs} == {mueller["mail"], mueller["call"], mueller["task"]}
        assert view.reason  # the "why" line

    def test_variants_are_the_alternatives_and_point_back(self, app, mueller):
        result = _ask(app, "Was ist offen bei Müller?")
        alternatives = [a for a in result.view.alternatives if a != "text"]
        assert sorted(result.variants) == sorted(alternatives)
        for name, variant in result.variants.items():
            assert variant.type == name
            assert result.view.type in variant.alternatives
            assert variant.alternatives[-1] == "text"
            assert variant.refs == result.view.refs

    def test_graph_slice_has_the_link_and_the_shared_topic(self, app, mueller):
        graph = _ask(app, "Was ist offen bei Müller?").variants["graph_slice"]
        ids = {n["id"] for n in graph.data["nodes"]}
        assert {mueller["mail"], mueller["call"], "topic:" + mueller["client"]} <= ids
        pairs = {(e["source"], e["target"]) for e in graph.data["edges"]}
        assert (mueller["call"], mueller["mail"]) in pairs

    def test_same_question_same_view(self, app, mueller):
        first = _ask(app, "Wie hängt Müller mit Projekte zusammen?")
        second = _ask(app, "Wie hängt Müller mit Projekte zusammen?")
        assert first.view.type == second.view.type == "graph_slice"
        assert first.view.data == second.view.data

    def test_overview_is_a_mind_map_of_topics(self, app, mueller):
        view = _ask(app, "Überblick über Müller").view
        assert view.type == "mind_map"
        labels = {c["label"] for c in view.data["root"]["children"]}
        assert labels == {"Müller GmbH", "Projekte"}

    def test_count_per_topic(self, app, mueller):
        view = _ask(app, "Wie viele Notizen gibt es zu Müller?").view
        assert view.type == "counts"
        assert {i["label"]: i["value"] for i in view.data["items"]} == {"Müller GmbH": 2, "Projekte": 1}

    def test_model_hint_inside_candidates_is_accepted(self, app, mueller):
        assert _ask(app, "Was ist offen bei Müller?", model_hint="graph_slice").view.type == "graph_slice"

    def test_model_hint_outside_candidates_is_rejected(self, app, mueller):
        for hint in ("comparison", "approval_card", "tree_change", "nonsense"):
            assert _ask(app, "Was ist offen bei Müller?", model_hint=hint).view.type == "timeline"

    def test_users_own_words_beat_the_model_hint(self, app, mueller):
        result = _ask(app, "Was ist offen bei Müller? Als Netzwerk bitte", model_hint="counts")
        assert result.view.type == "graph_slice"

    def test_model_comparison_structure(self, app, mueller):
        structure = {"type": "comparison", "data": {
            "options": ["DATEV", "Lexware"],
            "rows": [{"criterion": "Preis", "cells": [
                {"value": "teuer", "verdict": "bad"}, {"value": "günstig", "verdict": "good"}]}],
        }}
        view = _ask(app, "Vergleich DATEV und Lexware", structure=structure).view
        assert view.type == "comparison"
        assert view.data["options"] == ["DATEV", "Lexware"]

    def test_invalid_structure_degrades(self, app, mueller):
        broken = {"type": "comparison", "data": {"options": ["only one"], "rows": []}}
        assert clean_structure(broken) is None
        result = _ask(app, "Vergleich Müller", structure=broken)
        assert result.view is None or result.view.type != "comparison"

    def test_flow_structure(self, app, mueller):
        structure = {"type": "flow", "data": {
            "steps": [{"id": "a", "text": "Vertrag"}, {"id": "b", "text": "Zugänge"}],
            "edges": [{"source": "a", "target": "b"}],
        }}
        assert _ask(app, "Wie ist der Ablauf bei Müller?", structure=structure).view.type == "flow"

    def test_no_notes(self, app):
        result = _ask(app, "Was ist offen bei Niemand?", query="Niemand")
        assert result.view is None and result.refs == []


class TestTool:
    def test_view_payload_and_summary(self, app, mueller):
        from mycelos.tools.knowledge_view import execute_knowledge_view
        result = execute_knowledge_view(
            {"question": "Was ist offen bei Müller?", "query": "Müller"}, {"app": app})
        payload = result["__view__"]
        assert result["status"] == "view_shown"
        assert result["view"] == payload["type"] == "timeline"
        assert result["summary"] == payload["fallback_text"]
        assert set(payload["variants"]) == set(payload["alternatives"]) - {"text"}
        json.dumps(result)

    def test_text_channels_ignore_variants(self, app, mueller):
        from mycelos.tools.knowledge_view import execute_knowledge_view
        from mycelos.widgets.json_serializer import widget_from_dict
        from mycelos.widgets.types import KnowledgeView
        payload = execute_knowledge_view(
            {"question": "Was ist offen bei Müller?", "query": "Müller"}, {"app": app})["__view__"]
        widget = widget_from_dict(payload)
        assert isinstance(widget, KnowledgeView)
        assert widget.fallback_text == payload["fallback_text"]

    def test_no_notes(self, app):
        from mycelos.tools.knowledge_view import execute_knowledge_view
        result = execute_knowledge_view({"question": "Was ist mit Niemand?"}, {"app": app})
        assert result["status"] == "no_notes"
        assert "__view__" not in result

    def test_registered_as_core_read_only_tool(self):
        from mycelos.tools.registry import ToolPermission, ToolRegistry
        ToolRegistry._ensure_initialized()
        entry = ToolRegistry._tools["knowledge_view"]
        assert entry["category"] == "core"
        assert entry["permission"] == ToolPermission.OPEN


def _llm(content=None, tool=None):
    mock = MagicMock()
    mock.content = content
    mock.total_tokens = 20
    mock.model = "test-model"
    mock.cost = 0.0
    mock.prompt_tokens = 15
    mock.completion_tokens = 5
    mock.stop_reason = "tool_use" if tool else "end_turn"
    mock.tool_calls = [{"id": "tc-view-1", "function": {
        "name": tool[0], "arguments": json.dumps(tool[1])}}] if tool else None
    return mock


def test_chat_emits_the_view_and_keeps_it_out_of_the_model_context(app, mueller):
    from mycelos.chat.service import ChatService
    app._llm = MagicMock()
    service = ChatService(app)
    session_id = service.create_session()
    calls: list = []

    def complete(*args, **kwargs):
        calls.append(kwargs.get("messages") or (args[0] if args else None))
        if len(calls) == 1:
            return _llm(tool=("knowledge_view", {"question": "Was ist offen bei Müller?",
                                                 "query": "Müller"}))
        return _llm(content="Bei Müller fehlen die Q3-Belege.")

    with patch.object(app.llm, "complete", side_effect=complete):
        events = service.handle_message("Was ist offen bei Müller?", session_id)

    widgets = [e.data["widget"] for e in events if e.type == "widget"]
    views = [w for w in widgets if w.get("type") == "timeline"]
    assert len(views) == 1
    assert "graph_slice" in views[0]["variants"]
    # The second model round sees the summary, not the view payload.
    tool_messages = [m for m in calls[-1] if m.get("role") == "tool"]
    assert tool_messages
    content = tool_messages[-1]["content"]
    assert "__view__" not in content and "variants" not in content
    assert "view_shown" in content
