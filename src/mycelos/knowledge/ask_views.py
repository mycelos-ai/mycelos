"""Answer a question with a knowledge view (Package 8, slice 4: Ask).

Flow, all deterministic except the optional model inputs:

1. ``classify_intent`` maps the user's question to one of the sub-intents in
   ``view_choice.INTENTS`` with keyword rules (English and German). Only when
   no rule matches is the intent the model proposed used.
2. ``gather`` retrieves the notes (hybrid search plus an optional topic
   subtree) with their metadata, links and typed relations.
3. ``compute_facts`` derives the shape of that knowledge (dates, topic
   depth, linked entities, quantities, evidence relations).
4. ``view_choice`` picks the candidate views; every candidate that can be
   built is built, so the switcher can change views without another round
   trip. The model hint (or a view the user named) is accepted only if it is
   a built candidate.

Timeline, counts, the topic mind map, the graph slice and evidence come from
deterministic builders. Comparison, flow and (optionally) mind map come from
a structure the model supplies, validated against the view schema; a
structure that does not validate is dropped, never shown half-built.

Note content is data: only titles, dates, topic paths and relation reasons
are read, and they never select a view or add an action. System views
(``tree_change``, ``approval_card``) are never produced here.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from mycelos.i18n import t
from mycelos.knowledge import view_choice
from mycelos.widgets.types import KnowledgeView, Ref
from mycelos.widgets.views import (
    SYSTEM_VIEWS,
    InvalidView,
    make_view,
    validate_view_data,
    view_to_dict,
)

logger = logging.getLogger("mycelos.ask")

ANSWER_VIEWS: tuple[str, ...] = (
    "timeline", "mind_map", "graph_slice", "comparison", "flow", "counts", "evidence",
)
# Views whose structure the model writes (validated against the schema).
MODEL_STRUCTURES: frozenset[str] = frozenset({"comparison", "flow", "mind_map"})

_SHORT = 300
_MAX_NOTES = 40
_MAX_EVENTS = 40
_MAX_GRAPH_NODES = 40
_MAX_REFS = 20
_MAX_CHILDREN = 30


# --- Intent ------------------------------------------------------------------

# Ordered: the first intent with a matching rule wins. Patterns are matched
# case-insensitively against the question with word boundaries where a short
# word would otherwise match inside other words.
_INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("compare", (
        r"vergleich", r"\bcompar", r"\bversus\b", r"\bvs\.?\b", r"unterschied",
        r"\bdifference", r"\bbesser\b", r"\bbetter\b", r"vor- und nachteile",
        r"pros and cons", r"gegenüberstell",
    )),
    ("count", (
        r"wie ?viele", r"\banzahl\b", r"\bhow many\b", r"\bnumber of\b", r"\bcount\b",
        r"statistik", r"\bstatistic", r"verteilung", r"\bdistribution\b",
    )),
    ("process", (
        r"wie funktioniert", r"\bablauf", r"\bprozess", r"\bprocess\b", r"\bschritte\b",
        r"\bsteps?\b", r"\bvorgehen", r"\banleitung", r"\bhow do i\b", r"\bhow to\b",
        r"\bworkflow\b", r"\bchecklist",
    )),
    ("verify", (
        r"stimmt (es|das)", r"\bist (es|das) wahr", r"widerspr", r"\bbeleg", r"\bbeweis",
        r"\bis it true\b", r"\bverify\b", r"\bevidence\b", r"\bcontradict", r"\bprove\b",
    )),
    ("relation", (
        r"zusammenh[äa]ng", r"\bh[äa]ngt\b.*\bzusammen", r"\bverbindung", r"\bverbunden",
        r"\bbeziehung", r"\brelat(ed|e|ion)", r"\bconnect", r"\bzwischen\b", r"\bbetween\b",
    )),
    ("status", (
        r"\boffen\b", r"\boffene", r"\bstand\b", r"\bstatus\b", r"\bverlauf", r"\bwann\b",
        r"\bwhen\b", r"\bopen\b", r"\bpending\b", r"\bhistory\b", r"\bprogress\b",
        r"fortschritt", r"\bf[äa]llig", r"\bdue\b", r"was gibt es neu", r"\bneues\b",
        r"\bwhat'?s new\b", r"\bzuletzt\b", r"\blately\b", r"\brecent",
    )),
    ("overview", (
        r"[üu]berblick", r"[üu]bersicht", r"zusammenfass", r"was wei(ß|ss) ich",
        r"alles [üu]ber", r"\boverview\b", r"\bsummar", r"what do i know",
        r"everything about",
    )),
)

# A view the user names explicitly ("show it as a timeline"). Treated like a
# model hint: accepted only when it is a valid candidate for the data.
_VIEW_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("timeline", (r"zeitleiste", r"zeitstrahl", r"\btimeline\b", r"chronolog")),
    ("mind_map", (r"mind ?map",)),
    ("graph_slice", (r"\bgraph\b", r"\bnetz(werk)?\b", r"\bnetwork\b")),
    ("comparison", (r"\btabelle\b", r"\btable\b")),
    ("flow", (r"flussdiagramm", r"\bflowchart\b", r"ablaufdiagramm")),
    ("counts", (r"balken", r"\bbar chart\b", r"\bchart\b")),
)


def _match(rules: tuple[tuple[str, tuple[str, ...]], ...], text: str) -> str:
    lowered = (text or "").lower()
    for name, patterns in rules:
        if any(re.search(p, lowered) for p in patterns):
            return name
    return ""


def classify_intent(question: str, model_intent: str | None = None) -> str:
    """Sub-intent of a question; keyword rules first, then the model's guess.

    The same question always gets the same intent when a rule matches. The
    model intent is only a fallback and must be one of ``view_choice.INTENTS``.
    Without either, ``overview`` is used.
    """
    by_rule = _match(_INTENT_RULES, question)
    if by_rule:
        return by_rule
    if isinstance(model_intent, str) and model_intent in view_choice.INTENTS:
        return model_intent
    return "overview"


def requested_view(question: str) -> str:
    """A view the user named in the question, or ``""``."""
    return _match(_VIEW_WORDS, question)


# --- Retrieval ---------------------------------------------------------------


@dataclass
class Gathered:
    """Retrieved notes plus their structure. Content bodies are never loaded."""

    notes: list[dict[str, Any]] = field(default_factory=list)
    topics: dict[str, str] = field(default_factory=dict)  # topic path -> title
    links: list[dict[str, str]] = field(default_factory=list)
    relations: list[dict[str, str]] = field(default_factory=list)


_META_COLUMNS = "path, title, type, status, due, parent_path, created_at, updated_at"


def _rows_by_path(storage: Any, paths: list[str]) -> dict[str, dict[str, Any]]:
    if not paths:
        return {}
    marks = ",".join("?" for _ in paths)
    rows = storage.fetchall(
        f"SELECT {_META_COLUMNS} FROM knowledge_notes WHERE path IN ({marks})",
        tuple(paths),
    )
    return {r["path"]: dict(r) for r in rows}


def gather(app: Any, query: str, *, topic: str | None = None, limit: int = 15) -> Gathered:
    """Notes that answer ``query``, optionally widened by a topic subtree."""
    kb = app.knowledge_base
    storage = app.storage
    limit = max(1, min(int(limit or 15), _MAX_NOTES))
    paths: list[str] = []
    if query:
        try:
            for hit in kb.search(query, limit=limit):
                if isinstance(hit, dict) and isinstance(hit.get("path"), str):
                    paths.append(hit["path"])
        except Exception:
            logger.warning("ask search failed", exc_info=True)
    if topic:
        topic = topic.strip().strip("/")
        rows = storage.fetchall(
            "SELECT path FROM knowledge_notes WHERE (parent_path = ? OR parent_path LIKE ?) "
            "AND type != 'topic' ORDER BY created_at LIMIT ?",
            (topic, topic + "/%", _MAX_NOTES),
        )
        paths.extend(r["path"] for r in rows)
    paths = list(dict.fromkeys(paths))[:_MAX_NOTES]

    rows = _rows_by_path(storage, paths)
    notes = [
        rows[p] for p in paths
        if p in rows and rows[p].get("status") != "archived" and rows[p].get("type") != "topic"
    ]
    visible = {n["path"] for n in notes}

    parent_paths = sorted({n["parent_path"] for n in notes if n.get("parent_path")})
    topic_rows = _rows_by_path(storage, parent_paths)
    topics = {p: (topic_rows.get(p) or {}).get("title") or p.rsplit("/", 1)[-1] for p in parent_paths}

    links: list[dict[str, str]] = []
    relations: list[dict[str, str]] = []
    if visible:
        marks = ",".join("?" for _ in visible)
        ordered = sorted(visible)
        for row in storage.fetchall(
            f"SELECT from_path, to_path, kind FROM knowledge_links "
            f"WHERE from_path IN ({marks}) AND to_path IN ({marks}) ORDER BY from_path, to_path",
            tuple(ordered) + tuple(ordered),
        ):
            if row["from_path"] != row["to_path"]:
                links.append({"from": row["from_path"], "to": row["to_path"],
                              "kind": row.get("kind") or "wikilink"})
        try:
            for rel in app.knowledge_base._indexer.article_relations():
                if rel["from_path"] in visible and rel["to_path"] in visible:
                    relations.append({"from": rel["from_path"], "to": rel["to_path"],
                                      "kind": rel.get("kind") or "related",
                                      "reason": rel.get("reason") or ""})
        except Exception:
            logger.warning("ask relations failed", exc_info=True)
    return Gathered(notes=notes, topics=topics, links=links, relations=relations)


# --- Facts -------------------------------------------------------------------


def _event_date(note: dict[str, Any]) -> str:
    """The date a note stands for: its due date, else when it was created."""
    due = (note.get("due") or "").strip()
    if due:
        return due[:10]
    return (note.get("created_at") or "")[:10]


def _groups(g: Gathered) -> tuple[str, list[tuple[str, int]]]:
    """Counts per topic, else per note type, else per month. Best first."""
    no_topic = t("ask.view.no_topic")
    by_topic = Counter(g.topics.get(n.get("parent_path") or "", no_topic) for n in g.notes)
    if len(by_topic) >= 2:
        return "topic", sorted(by_topic.items(), key=lambda kv: (-kv[1], kv[0]))
    by_type = Counter(str(n.get("type") or "note") for n in g.notes)
    if len(by_type) >= 2:
        return "type", sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))
    by_month = Counter(_event_date(n)[:7] for n in g.notes if _event_date(n))
    if len(by_month) >= 2:
        return "month", sorted(by_month.items())
    return "", []


def _graph_edges(g: Gathered) -> list[tuple[str, str, str]]:
    edges: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for rel in g.relations:
        key = (rel["from"], rel["to"])
        if key not in seen:
            seen.add(key)
            edges.append((rel["from"], rel["to"], rel["kind"]))
    for link in g.links:
        key = (link["from"], link["to"])
        if key not in seen and (link["to"], link["from"]) not in seen:
            seen.add(key)
            edges.append((link["from"], link["to"], link["kind"]))
    in_topics = [n for n in g.notes if n.get("parent_path") in g.topics]
    shared = Counter(n["parent_path"] for n in in_topics)
    for n in in_topics:
        # A topic node only helps when it connects more than one note.
        if shared[n["parent_path"]] >= 2:
            edges.append((n["path"], "topic:" + n["parent_path"], "parent"))
    return edges


def compute_facts(g: Gathered, structure: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deterministic shape of the retrieved knowledge, for ``view_choice``."""
    total = len(g.notes)
    dates = [_event_date(n) for n in g.notes]
    dated = [d for d in dates if d]
    # A timeline of a single day shows nothing; it needs at least two dates.
    dated_share = (len(dated) / total) if total and len(set(dated)) >= 2 else 0.0
    _, groups = _groups(g)
    edges = _graph_edges(g)
    entities = {e[0] for e in edges} | {e[1] for e in edges}
    kinds = {r["kind"] for r in g.relations}
    tree_depth = 2 if len(g.topics) >= 2 else 1
    facts: dict[str, Any] = {
        "note_count": total,
        "dated_share": dated_share,
        "tree_depth": tree_depth,
        "entity_count": len(entities),
        "option_count": 0,
        "step_count": 0,
        "has_quantities": len(groups) >= 2,
        "has_contradicts": "contradicts" in kinds,
        "has_supports": "supports" in kinds,
    }
    if structure:
        kind, data = structure["type"], structure["data"]
        if kind == "comparison":
            facts["option_count"] = len(data.get("options", []))
        elif kind == "flow":
            facts["step_count"] = len(data.get("steps", []))
        elif kind == "mind_map":
            facts["tree_depth"] = max(tree_depth, _depth(data.get("root") or {}) - 1)
    return facts


def _depth(node: dict[str, Any]) -> int:
    children = node.get("children") or []
    return 1 + max((_depth(c) for c in children if isinstance(c, dict)), default=0)


def clean_structure(raw: Any) -> dict[str, Any] | None:
    """A model-written structure, validated; ``None`` if it does not fit.

    Only comparison, flow and mind map are accepted. The data is validated
    against the view schema, so the model cannot add fields or actions.
    """
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind not in MODEL_STRUCTURES or kind in SYSTEM_VIEWS:
        return None
    try:
        model = validate_view_data(kind, raw.get("data"))
    except InvalidView:
        logger.info("ask: model structure for %s dropped (invalid)", kind)
        return None
    return {"type": kind, "data": model.model_dump(mode="json")}


# --- Builders ----------------------------------------------------------------


def _cut(text: Any, limit: int = _SHORT) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _timeline(g: Gathered, structure: dict | None) -> dict[str, Any]:
    today = date.today().isoformat()
    events = []
    for n in g.notes:
        when = _event_date(n)
        if not when:
            continue
        kind = "normal"
        open_task = (n.get("status") or "active") not in ("done", "archived")
        if n.get("due") and open_task:
            kind = "warning" if when < today else "due"
        events.append({"date": when, "text": _cut(n["title"], 2000), "kind": kind,
                       "refs": [n["path"][:_SHORT]]})
    events.sort(key=lambda e: (e["date"], e["text"]))
    return {"events": events[-_MAX_EVENTS:]}


def _counts(g: Gathered, structure: dict | None) -> dict[str, Any]:
    by, groups = _groups(g)
    items = [{"label": _cut(_label("ask.view.type.", label) if by == "type" else label),
              "value": value} for label, value in groups[:60]]
    return {"items": items, "unit": t("briefing.view.notes_unit")}


def _graph_slice(g: Gathered, structure: dict | None) -> dict[str, Any]:
    titles = {n["path"]: n["title"] for n in g.notes}
    kinds = {n["path"]: ("task" if n.get("type") == "task" else "note") for n in g.notes}
    for path, title in g.topics.items():
        titles["topic:" + path] = title
        kinds["topic:" + path] = "topic"
    edges = _graph_edges(g)
    degree = Counter([e[0] for e in edges] + [e[1] for e in edges])
    # Most connected first (topics win ties): the renderer puts the first node
    # in the centre.
    ranked = [node for node, _ in sorted(
        degree.items(), key=lambda kv: (-kv[1], not kv[0].startswith("topic:"), kv[0]))]
    ranked = ranked[:_MAX_GRAPH_NODES]
    keep = set(ranked)
    nodes = [{"id": node[:_SHORT], "label": _cut(titles.get(node, node)), "kind": kinds.get(node, "note")}
             for node in ranked]
    data_edges = [{"source": a[:_SHORT], "target": b[:_SHORT],
                   "relation": _cut(_relation_label(kind), 60)}
                  for a, b, kind in edges if a in keep and b in keep]
    return {"nodes": nodes, "edges": data_edges}


def _relation_label(kind: str) -> str:
    return _label("ask.view.relation.", kind)


def _label(prefix: str, value: str) -> str:
    """Translated label for a known value, the raw value otherwise."""
    key = prefix + value
    label = t(key)
    return label if label != key else value


def _mind_map(g: Gathered, structure: dict | None, title: str = "") -> dict[str, Any]:
    if structure and structure["type"] == "mind_map":
        return structure["data"]
    no_topic = t("ask.view.no_topic")
    grouped: dict[str, list[str]] = {}
    for n in g.notes:
        label = g.topics.get(n.get("parent_path") or "", no_topic)
        grouped.setdefault(label, []).append(_cut(n["title"]))
    children = [
        {"label": _cut(label), "children": [{"label": x} for x in items[:_MAX_CHILDREN]]}
        for label, items in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ][:_MAX_CHILDREN]
    return {"root": {"label": _cut(title) or t("ask.view.root"), "children": children}}


def _evidence(g: Gathered, structure: dict | None) -> dict[str, Any]:
    titles = {n["path"]: n["title"] for n in g.notes}
    dates = {n["path"]: _event_date(n) for n in g.notes}
    claims = []
    for rel in g.relations:
        if rel["kind"] not in ("supports", "contradicts"):
            continue
        claims.append({
            "quote": _cut(rel["reason"] or t("briefing.view.no_reason"), 2000),
            "source": _cut(f"{titles[rel['from']]} ↔ {titles[rel['to']]}"),
            "date": dates.get(rel["from"], ""),
            "relation": rel["kind"],
        })
    return {"claims": claims[:20]}


def _from_structure(kind: str):
    def build(g: Gathered, structure: dict | None) -> dict[str, Any]:
        if not structure or structure["type"] != kind:
            raise InvalidView(f"no {kind} structure")
        return structure["data"]
    return build


_BUILDERS = {
    "timeline": _timeline,
    "counts": _counts,
    "graph_slice": _graph_slice,
    "evidence": _evidence,
    "comparison": _from_structure("comparison"),
    "flow": _from_structure("flow"),
}


def _reason(view: str, intent: str, g: Gathered, facts: dict[str, Any], data: dict) -> str:
    total = len(g.notes)
    params: dict[str, Any] = {"total": total}
    if view == "timeline":
        params["dated"] = len(data.get("events", []))
    elif view == "graph_slice":
        params["count"] = len(data.get("nodes", []))
    elif view == "mind_map":
        params["groups"] = len((data.get("root") or {}).get("children", []))
    elif view == "counts":
        by, _ = _groups(g)
        params["by"] = t("ask.view.by." + (by or "topic"))
    elif view == "comparison":
        params["options"] = len(data.get("options", []))
    elif view == "flow":
        params["steps"] = len(data.get("steps", []))
    elif view == "evidence":
        params["claims"] = len(data.get("claims", []))
    return _cut(t("ask.intent." + intent) + "; " + t("ask.reason." + view, **params))


@dataclass
class AskResult:
    intent: str
    facts: dict[str, Any]
    view: KnowledgeView | None
    variants: dict[str, KnowledgeView]
    refs: list[Ref]


def build_answer(
    g: Gathered,
    *,
    question: str,
    title: str = "",
    model_intent: str | None = None,
    model_hint: str | None = None,
    structure: Any = None,
) -> AskResult:
    """Pick and build the view for an answer, plus every valid alternative."""
    intent = classify_intent(question, model_intent)
    clean = clean_structure(structure)
    facts = compute_facts(g, clean)
    refs = [Ref(path=n["path"][:_SHORT], title=_cut(n["title"])) for n in g.notes[:_MAX_REFS]]
    if not g.notes:
        return AskResult(intent=intent, facts=facts, view=None, variants={}, refs=[])

    view_title = _cut(title) or _cut(question, 120)
    datas: dict[str, dict[str, Any]] = {}
    for candidate in view_choice.candidate_views(intent, facts):
        if candidate == view_choice.TEXT or candidate in SYSTEM_VIEWS:
            continue
        try:
            if candidate == "mind_map":
                data = _mind_map(g, clean, view_title)
            else:
                data = _BUILDERS[candidate](g, clean)
            validate_view_data(candidate, data)
        except (InvalidView, KeyError, TypeError, ValueError):
            logger.info("ask: %s not buildable for this data", candidate)
            continue
        datas[candidate] = data

    built = list(datas)
    if not built:
        return AskResult(intent=intent, facts=facts, view=None, variants={}, refs=refs)

    # The user's own words ("as a timeline") beat the model's hint; both are
    # accepted only inside the built candidates, otherwise the rule wins.
    hint = requested_view(question) or model_hint
    chosen = hint if isinstance(hint, str) and hint in built else built[0]

    variants: dict[str, KnowledgeView] = {}
    for name, data in datas.items():
        variants[name] = make_view(
            name,
            title=view_title,
            reason=_reason(name, intent, g, facts, data),
            data=data,
            refs=refs,
            alternatives=[v for v in built if v != name] + [view_choice.TEXT],
        )
    primary = variants.pop(chosen)
    return AskResult(intent=intent, facts=facts, view=primary, variants=variants, refs=refs)


def answer_payload(result: AskResult) -> dict[str, Any] | None:
    """Wire form for the web chat: the view plus prebuilt ``variants``.

    ``<mycelos-view>`` switches to a variant locally; clients without a
    renderer ignore ``variants`` and show the primary view's text fallback.
    """
    if result.view is None:
        return None
    payload = view_to_dict(result.view)
    payload["variants"] = {name: view_to_dict(v) for name, v in result.variants.items()}
    return payload
