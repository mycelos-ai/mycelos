"""knowledge_view — answer a knowledge question with a view (Package 8, slice 4).

The tool retrieves the notes, picks the view deterministically
(``knowledge.ask_views``) and hands the chat service a ``__view__`` payload,
which it emits as a widget event. The model gets back the view's text
fallback and the sources, so it can write a short lead without repeating the
view. Read-only: nothing is written, no state changes.
"""

from __future__ import annotations

from typing import Any

from mycelos.knowledge import ask_views
from mycelos.knowledge.view_choice import INTENTS
from mycelos.tools.registry import ToolPermission

_SUMMARY_LIMIT = 4000

KNOWLEDGE_VIEW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "knowledge_view",
        "description": (
            "Answer a question about the user's own knowledge (notes, tasks, "
            "sources, clients, projects, topics) with a visual view: timeline, "
            "connections graph, mind map, counts, evidence, comparison or flow. "
            "Mycelos retrieves the notes and picks the view that fits; the user "
            "can switch to another view or to text. Use it for questions like "
            "'what is open with client X', 'how does A relate to B', 'give me an "
            "overview of topic Y', 'how many ... per ...', 'does source X "
            "contradict Y'. After the call, write a short lead (one to three "
            "sentences) that answers the question; do not repeat the view's "
            "content as a list. If it returns status 'no_notes', answer in text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The user's question, in the user's own words.",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Search terms for the notes (names, topics, key words). "
                        "Defaults to the question."
                    ),
                },
                "topic": {
                    "type": "string",
                    "description": "Optional topic path whose notes belong to the answer.",
                },
                "intent": {
                    "type": "string",
                    "enum": sorted(INTENTS),
                    "description": (
                        "Your guess of the question type. Only used when Mycelos' "
                        "own rules do not recognise the question."
                    ),
                },
                "view": {
                    "type": "string",
                    "enum": list(ask_views.ANSWER_VIEWS),
                    "description": (
                        "Optional preferred view. Ignored when it does not fit the data."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Short title for the view, e.g. 'Müller GmbH: what is open'.",
                },
                "structure": {
                    "type": "object",
                    "description": (
                        "Only for comparison, flow or mind map, which need a structure "
                        "you write from the retrieved notes. {type, data}: "
                        "comparison data = {options: [2-4 names], rows: [{criterion, "
                        "cells: [{value, verdict: good|bad|neutral}] one per option]}; "
                        "flow data = {steps: [{id, text}], edges: [{source, target, label}]}; "
                        "mind_map data = {root: {label, children: [{label, children}]}} "
                        "up to three levels. Invalid structures are dropped."
                    ),
                    "properties": {
                        "type": {"type": "string", "enum": sorted(ask_views.MODEL_STRUCTURES)},
                        "data": {"type": "object"},
                    },
                },
            },
            "required": ["question"],
        },
    },
}


def execute_knowledge_view(args: dict, context: dict) -> dict[str, Any]:
    app = context["app"]
    question = str(args.get("question") or "").strip()
    if not question:
        return {"error": "Missing required parameter: question"}
    query = str(args.get("query") or "").strip() or question
    topic = args.get("topic") if isinstance(args.get("topic"), str) else None

    gathered = ask_views.gather(app, query, topic=topic)
    result = ask_views.build_answer(
        gathered,
        question=question,
        title=str(args.get("title") or ""),
        model_intent=args.get("intent"),
        model_hint=args.get("view"),
        structure=args.get("structure"),
    )
    sources = [{"path": r.path, "title": r.title} for r in result.refs]
    if not gathered.notes:
        return {"status": "no_notes", "intent": result.intent,
                "message": "No matching notes were found. Answer in text and say so."}

    payload = ask_views.answer_payload(result)
    if payload is None:
        # Only plain text fits this data: hand the notes to the model.
        return {
            "status": "text_only",
            "intent": result.intent,
            "message": "No view fits these notes. Answer in text and name the sources.",
            "notes": [
                {"path": n["path"], "title": n["title"], "type": n.get("type"),
                 "status": n.get("status"), "due": n.get("due"),
                 "created_at": n.get("created_at")}
                for n in gathered.notes
            ],
        }

    return {
        "__view__": payload,
        "status": "view_shown",
        "intent": result.intent,
        "view": payload["type"],
        "alternatives": payload["alternatives"],
        "summary": payload["fallback_text"][:_SUMMARY_LIMIT],
        "sources": sources,
        "message": (
            "The user now sees this view with its sources. Write a short lead "
            "(one to three sentences) that answers the question. Do not repeat "
            "the view as a list."
        ),
    }


def register(registry: type) -> None:
    registry.register(
        "knowledge_view",
        KNOWLEDGE_VIEW_SCHEMA,
        execute_knowledge_view,
        ToolPermission.OPEN,
        concurrent_safe=True,
        category="core",  # the main way to answer knowledge questions; always loaded
    )
