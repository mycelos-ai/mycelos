"""Choose the knowledge view for an answer (Package 8).

Pure functions, no LLM. The rule result over the question intent and the
shape of the retrieved knowledge comes first. A model may propose a view, but
a proposal outside the candidate set is ignored. Same question and same facts
always give the same view.

``facts`` are deterministic properties of the retrieved notes, never note
content. Recognised keys (all optional, anything else is ignored):

- ``note_count``      number of retrieved notes
- ``dated_share``     share of notes that carry a date (0.0 to 1.0)
- ``tree_depth``      depth of the topic subtree the notes sit in
- ``entity_count``    number of distinct linked entities
- ``option_count``    number of options being compared
- ``step_count``      number of ordered steps found
- ``has_quantities``  numbers across sources, folders or time are available
- ``has_contradicts`` a ``contradicts`` relation exists among the notes
- ``has_supports``    a ``supports`` relation exists among the notes
"""

from __future__ import annotations

from typing import Any

TEXT = "text"

INTENTS: frozenset[str] = frozenset(
    {"status", "overview", "relation", "compare", "process", "count", "verify"}
)


def _num(facts: dict[str, Any], key: str) -> float:
    value = facts.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _flag(facts: dict[str, Any], key: str) -> bool:
    return facts.get(key) is True


def candidate_views(intent: str, facts: dict[str, Any]) -> list[str]:
    """Views that fit the intent and the shape of the knowledge, best first.

    The list always ends with ``"text"``, which fits everything.
    """
    facts = facts if isinstance(facts, dict) else {}
    dated = _num(facts, "dated_share") >= 0.5 and _num(facts, "note_count") >= 2
    hierarchy = _num(facts, "tree_depth") >= 2
    linked = _num(facts, "entity_count")
    options = _num(facts, "option_count")
    steps = _num(facts, "step_count") >= 2
    quantities = _flag(facts, "has_quantities")
    evidence = _flag(facts, "has_contradicts") or _flag(facts, "has_supports")

    rules: dict[str, list[tuple[bool, str]]] = {
        "status": [(dated, "timeline"), (linked >= 2, "graph_slice"), (quantities, "counts")],
        "overview": [
            (hierarchy, "mind_map"),
            (linked >= 3, "graph_slice"),
            (quantities, "counts"),
            (dated, "timeline"),
        ],
        "relation": [(linked >= 2, "graph_slice"), (evidence, "evidence"), (dated, "timeline")],
        "compare": [(2 <= options <= 4, "comparison"), (evidence, "evidence")],
        "process": [(steps, "flow"), (dated, "timeline")],
        "count": [(quantities, "counts"), (dated, "timeline")],
        "verify": [(evidence, "evidence"), (dated, "timeline"), (linked >= 2, "graph_slice")],
    }
    views = [view for ok, view in rules.get(intent, []) if ok]
    return list(dict.fromkeys(views)) + [TEXT]


def choose_view(intent: str, facts: dict[str, Any], model_hint: str | None = None) -> str:
    """Rule result first; the model hint is accepted only if it is a candidate."""
    candidates = candidate_views(intent, facts)
    if isinstance(model_hint, str) and model_hint in candidates:
        return model_hint
    return candidates[0]


def alternatives_for(view: str, intent: str, facts: dict[str, Any]) -> list[str]:
    """Other views valid for the same data, for the view switcher."""
    return [v for v in candidate_views(intent, facts) if v != view]
