"""Filing changes as approval cards (Package 8, slice 5).

``filing_correct`` proposes moving a misfiled note, ``source_attach``
proposes attaching a local source to a folder with a rule. Both only build a
card: nothing moves, attaches or imports until the user presses a button on
it. No tool can press the button; proposals resolve only through
``POST /api/proposals/{id}/resolve`` from the user's client.
"""

from __future__ import annotations

from typing import Any

from mycelos.knowledge.proposals import FilingError, attach_cards, correct_card
from mycelos.tools.registry import ToolPermission

_SHOWN = (
    "The user now sees an approval card. Nothing has changed yet; it changes "
    "only when the user presses a button on the card. Say so in one short "
    "sentence and do not repeat the card."
)

FILING_CORRECT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "filing_correct",
        "description": (
            "Propose moving a note that sits in the wrong folder, when the user "
            "says it belongs elsewhere ('that belongs to Müller, not Projects', "
            "'file it under X'). Shows an approval card with before and after and "
            "the option to teach the rule, which lists the similar notes that "
            "would move too. Does not move anything by itself. Find the note "
            "path with note_search first if you do not have it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the misfiled note."},
                "topic": {
                    "type": "string",
                    "description": "Target folder: an existing topic path or its exact title.",
                },
            },
            "required": ["path", "topic"],
        },
    },
}

SOURCE_ATTACH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "source_attach",
        "description": (
            "Propose attaching a local source (Obsidian vault, folder, repository) "
            "to a folder of the knowledge base, with an optional filing rule in the "
            "user's words. Shows an approval card with what, where and the rule, "
            "plus an import preview. Approving attaches the source, saves the rule "
            "and starts the import; nothing happens before. Get the source_id from "
            "local_sources list, or add the source first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "The local source id."},
                "topic": {
                    "type": "string",
                    "description": "Target folder: an existing topic path or its exact title.",
                },
                "rule": {
                    "type": "string",
                    "description": "Optional filing rule, e.g. 'Meeting notes go to Clients'.",
                },
            },
            "required": ["source_id", "topic"],
        },
    },
}


def _error(exc: FilingError) -> dict[str, Any]:
    hints = {
        "note_not_found": "No note with this path. Search for it with note_search.",
        "topic_not_found": "No unique folder with this path or title. Use topic_list.",
        "already_there": "The note is already in this folder.",
        "source_not_found": "No local source with this id. Use local_sources list.",
    }
    return {"error": exc.code, "message": hints.get(exc.code, exc.code)}


def execute_filing_correct(args: dict, context: dict) -> dict[str, Any]:
    path = str(args.get("path") or "").strip()
    topic = str(args.get("topic") or "").strip()
    if not path or not topic:
        return {"error": "Missing required parameter: path and topic"}
    try:
        card = correct_card(context["app"], path, topic,
                            user_id=context.get("user_id") or "default",
                            session_id=context.get("session_id"))
    except FilingError as exc:
        return _error(exc)
    return {"__view__": card, "status": "card_shown", "summary": card["fallback_text"],
            "message": _SHOWN}


def execute_source_attach(args: dict, context: dict) -> dict[str, Any]:
    source_id = str(args.get("source_id") or "").strip()
    topic = str(args.get("topic") or "").strip()
    if not source_id or not topic:
        return {"error": "Missing required parameter: source_id and topic"}
    try:
        views = attach_cards(context["app"], source_id, topic, str(args.get("rule") or ""),
                             user_id=context.get("user_id") or "default",
                             session_id=context.get("session_id"))
    except FilingError as exc:
        return _error(exc)
    return {"__view__": views, "status": "card_shown", "summary": views[0]["fallback_text"],
            "message": _SHOWN}


def register(registry: type) -> None:
    # STANDARD: building a card writes no knowledge; the user's button press
    # is the approval for the change itself.
    registry.register("filing_correct", FILING_CORRECT_SCHEMA, execute_filing_correct,
                      ToolPermission.STANDARD, category="knowledge_write")
    registry.register("source_attach", SOURCE_ATTACH_SCHEMA, execute_source_attach,
                      ToolPermission.STANDARD, category="knowledge_manage")
