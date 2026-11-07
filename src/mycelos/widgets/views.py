"""Knowledge view catalog (Package 8, slice 1).

The model never writes UI. It may propose a view type and supply data; this
module validates that data against a fixed schema per view type and renders a
deterministic text fallback. Anything that does not validate degrades to a
plain ``TextBlock``. That is a valid outcome, not an error.

Content inside view data (note titles, quotes, event texts) is data. Nothing in
it can change the view type, add fields, or add actions: every schema forbids
extra fields, and ``approval_card`` actions come from a fixed allow-list.
Renderers must escape text fields; they never interpret them as markup.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from mycelos.i18n import t
from mycelos.widgets.types import KnowledgeView, Ref, TextBlock

VIEW_VERSION = 1

_SHORT = 300
_LONG = 2000
_MAX_ITEMS = 200

_ShortStr = Annotated[str, StringConstraints(max_length=_SHORT)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


# --- Per-view data schemas -------------------------------------------------


class TimelineEvent(_Strict):
    date: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=_LONG)
    kind: Literal["normal", "warning", "due"] = "normal"
    refs: list[_ShortStr] = Field(default_factory=list, max_length=20)


class TimelineData(_Strict):
    events: list[TimelineEvent] = Field(min_length=1, max_length=_MAX_ITEMS)


class MindMapNode(_Strict):
    label: str = Field(min_length=1, max_length=_SHORT)
    children: list[MindMapNode] = Field(default_factory=list, max_length=30)


class MindMapData(_Strict):
    root: MindMapNode

    @field_validator("root")
    @classmethod
    def _depth(cls, root: MindMapNode) -> MindMapNode:
        if _tree_depth(root) > 4:  # root plus three levels
            raise ValueError("mind map deeper than three levels")
        return root


class GraphNode(_Strict):
    id: str = Field(min_length=1, max_length=_SHORT)
    label: str = Field(min_length=1, max_length=_SHORT)
    kind: str = Field(default="note", max_length=40)


class GraphEdge(_Strict):
    source: str = Field(min_length=1, max_length=_SHORT)
    target: str = Field(min_length=1, max_length=_SHORT)
    relation: str = Field(default="related", max_length=60)


class GraphSliceData(_Strict):
    nodes: list[GraphNode] = Field(min_length=2, max_length=_MAX_ITEMS)
    edges: list[GraphEdge] = Field(min_length=1, max_length=_MAX_ITEMS)

    @field_validator("edges")
    @classmethod
    def _edges_known(cls, edges: list[GraphEdge], info: Any) -> list[GraphEdge]:
        ids = {n.id for n in info.data.get("nodes", [])}
        for edge in edges:
            if edge.source not in ids or edge.target not in ids:
                raise ValueError("edge points to an unknown node")
        return edges


class ComparisonCell(_Strict):
    value: str = Field(max_length=_SHORT)
    verdict: Literal["good", "bad", "neutral"] = "neutral"


class ComparisonRow(_Strict):
    criterion: str = Field(min_length=1, max_length=_SHORT)
    cells: list[ComparisonCell]


class ComparisonData(_Strict):
    options: list[str] = Field(min_length=2, max_length=4)
    rows: list[ComparisonRow] = Field(min_length=1, max_length=30)

    @field_validator("rows")
    @classmethod
    def _row_width(cls, rows: list[ComparisonRow], info: Any) -> list[ComparisonRow]:
        width = len(info.data.get("options", []))
        for row in rows:
            if len(row.cells) != width:
                raise ValueError("each row needs one cell per option")
        return rows


class FlowStep(_Strict):
    id: str = Field(min_length=1, max_length=60)
    text: str = Field(min_length=1, max_length=_SHORT)


class FlowEdge(_Strict):
    source: str = Field(min_length=1, max_length=60)
    target: str = Field(min_length=1, max_length=60)
    label: str = Field(default="", max_length=_SHORT)


class FlowData(_Strict):
    steps: list[FlowStep] = Field(min_length=1, max_length=60)
    edges: list[FlowEdge] = Field(default_factory=list, max_length=120)

    @field_validator("edges")
    @classmethod
    def _edges_known(cls, edges: list[FlowEdge], info: Any) -> list[FlowEdge]:
        ids = {s.id for s in info.data.get("steps", [])}
        for edge in edges:
            if edge.source not in ids or edge.target not in ids:
                raise ValueError("edge points to an unknown step")
        return edges


class CountItem(_Strict):
    label: str = Field(min_length=1, max_length=_SHORT)
    value: int = Field(ge=0)


class CountsData(_Strict):
    items: list[CountItem] = Field(min_length=1, max_length=60)
    unit: str = Field(default="", max_length=40)


class EvidenceClaim(_Strict):
    quote: str = Field(min_length=1, max_length=_LONG)
    source: str = Field(min_length=1, max_length=_SHORT)
    date: str = Field(default="", max_length=40)
    relation: Literal["supports", "contradicts", "neutral"] = "neutral"


class EvidenceData(_Strict):
    claims: list[EvidenceClaim] = Field(min_length=1, max_length=20)


class TreeChangeItem(_Strict):
    title: str = Field(min_length=1, max_length=_SHORT)
    before: str | None = Field(default=None, max_length=_SHORT)
    after: str = Field(min_length=1, max_length=_SHORT)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class TreeChangeData(_Strict):
    changes: list[TreeChangeItem] = Field(min_length=1, max_length=_MAX_ITEMS)


# Actions an approval card may offer. They map to existing resolve paths
# (capture, local-source import, move-with-teach, inbox). Data cannot add any.
APPROVAL_ACTIONS: frozenset[str] = frozenset({
    "approve",
    "reject",
    "accept",
    "elsewhere",
    "make_task",
    "teach_rule",
    "preview",
    "cancel",
})


class ApprovalAction(_Strict):
    id: str
    label: str = Field(min_length=1, max_length=80)

    @field_validator("id")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in APPROVAL_ACTIONS:
            raise ValueError(f"unknown approval action {value!r}")
        return value


class ApprovalCardData(_Strict):
    summary: str = Field(min_length=1, max_length=_LONG)
    effects: list[_ShortStr] = Field(default_factory=list, max_length=20)
    actions: list[ApprovalAction] = Field(min_length=1, max_length=4)


VIEW_SCHEMAS: dict[str, type[_Strict]] = {
    "timeline": TimelineData,
    "mind_map": MindMapData,
    "graph_slice": GraphSliceData,
    "comparison": ComparisonData,
    "flow": FlowData,
    "counts": CountsData,
    "evidence": EvidenceData,
    "tree_change": TreeChangeData,
    "approval_card": ApprovalCardData,
}

VIEW_TYPES: frozenset[str] = frozenset(VIEW_SCHEMAS)

# Views produced only by system paths (capture, import, move), never chosen
# for an answer and never accepted from a model hint.
SYSTEM_VIEWS: frozenset[str] = frozenset({"tree_change", "approval_card"})


class InvalidView(ValueError):
    """The view type is unknown or its data does not match the schema."""


# --- Construction ------------------------------------------------------------


def validate_view_data(view_type: str, data: Any) -> _Strict:
    schema = VIEW_SCHEMAS.get(view_type)
    if schema is None:
        raise InvalidView(f"unknown view type {view_type!r}")
    if not isinstance(data, dict):
        raise InvalidView("view data must be an object")
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise InvalidView(str(exc)) from exc


def make_view(
    view_type: str,
    *,
    title: str,
    reason: str,
    data: dict[str, Any],
    refs: list[Ref] | None = None,
    alternatives: list[str] | None = None,
) -> KnowledgeView:
    """Build a validated view. Raises ``InvalidView`` on bad data."""
    model = validate_view_data(view_type, data)
    clean = model.model_dump(mode="json")
    refs = list(refs or [])
    alts = [a for a in (alternatives or []) if a != view_type and (a in VIEW_TYPES or a == "text")]
    return KnowledgeView(
        type=view_type,
        version=VIEW_VERSION,
        title=title[:_SHORT],
        reason=reason[:_SHORT],
        data=clean,
        refs=refs,
        alternatives=list(dict.fromkeys(alts)),
        fallback_text=fallback_text(view_type, model, refs),
    )


def view_from_payload(
    payload: Any,
    *,
    allowed: list[str] | None = None,
    fallback: str = "",
) -> KnowledgeView | TextBlock:
    """Turn an untrusted payload (from a model or a client) into a widget.

    ``allowed`` limits the view types accepted, usually the candidates from
    ``knowledge.view_choice``. The payload's own ``fallback_text`` is ignored;
    the text fallback is always recomputed from the validated data.
    Anything invalid becomes a ``TextBlock`` with ``fallback``.
    """
    if not isinstance(payload, dict):
        return TextBlock(text=fallback)
    view_type = payload.get("type")
    if not isinstance(view_type, str) or view_type not in VIEW_TYPES:
        return TextBlock(text=fallback)
    if allowed is not None and view_type not in allowed:
        return TextBlock(text=fallback)
    try:
        refs = [_ref(r) for r in payload.get("refs") or []]
        title = payload.get("title") or ""
        reason = payload.get("reason") or ""
        alternatives = payload.get("alternatives") or []
        if not isinstance(title, str) or not isinstance(reason, str):
            raise InvalidView("title and reason must be text")
        if not isinstance(alternatives, list):
            raise InvalidView("alternatives must be a list")
        if allowed is not None:
            alternatives = [a for a in alternatives if a in allowed]
        return make_view(
            view_type,
            title=title,
            reason=reason,
            data=payload.get("data"),
            refs=refs,
            alternatives=[a for a in alternatives if isinstance(a, str)],
        )
    except InvalidView:
        return TextBlock(text=fallback)


def view_to_dict(view: KnowledgeView) -> dict[str, Any]:
    return {
        "type": view.type,
        "version": view.version,
        "title": view.title,
        "reason": view.reason,
        "alternatives": list(view.alternatives),
        "data": view.data,
        "refs": [{"path": r.path, "title": r.title} for r in view.refs],
        "fallback_text": view.fallback_text,
    }


def _ref(raw: Any) -> Ref:
    if isinstance(raw, str):
        return Ref(path=raw[:_SHORT])
    if isinstance(raw, dict) and isinstance(raw.get("path"), str):
        title = raw.get("title") if isinstance(raw.get("title"), str) else ""
        return Ref(path=raw["path"][:_SHORT], title=title[:_SHORT])
    raise InvalidView("bad ref")


# --- Text fallback -----------------------------------------------------------


def fallback_text(view_type: str, model: _Strict, refs: list[Ref]) -> str:
    """Deterministic plain text for Telegram, the CLI and clients without a renderer."""
    body = _FALLBACKS[view_type](model)
    if refs:
        names = ", ".join(r.title or r.path for r in refs)
        body += "\n\n" + t("widgets.view.sources", sources=names)
    return body


def _tree_depth(node: MindMapNode) -> int:
    return 1 + max((_tree_depth(c) for c in node.children), default=0)


def _fb_timeline(d: TimelineData) -> str:
    marks = {"normal": "", "warning": "(!) ", "due": "(→) "}
    return "\n".join(f"{e.date}  {marks[e.kind]}{e.text}" for e in d.events)


def _fb_mind_map(d: MindMapData) -> str:
    lines: list[str] = []

    def walk(node: MindMapNode, depth: int) -> None:
        lines.append(("  " * depth) + ("- " if depth else "") + node.label)
        for child in node.children:
            walk(child, depth + 1)

    walk(d.root, 0)
    return "\n".join(lines)


def _fb_graph_slice(d: GraphSliceData) -> str:
    labels = {n.id: n.label for n in d.nodes}
    return "\n".join(
        f"{labels[e.source]} —{e.relation}→ {labels[e.target]}" for e in d.edges
    )


def _fb_comparison(d: ComparisonData) -> str:
    marks = {"good": "+", "bad": "−", "neutral": "·"}
    lines: list[str] = []
    for row in d.rows:
        cells = "; ".join(
            f"{opt}: {marks[c.verdict]} {c.value}".rstrip() for opt, c in zip(d.options, row.cells)
        )
        lines.append(f"{row.criterion}: {cells}")
    return "\n".join(lines)


def _fb_flow(d: FlowData) -> str:
    numbers = {s.id: i for i, s in enumerate(d.steps, 1)}
    lines: list[str] = []
    for step in d.steps:
        lines.append(f"{numbers[step.id]}. {step.text}")
        outgoing = [e for e in d.edges if e.source == step.id]
        branching = len(outgoing) > 1 or any(e.label for e in outgoing)
        for edge in outgoing if branching else []:
            label = f"{edge.label}: " if edge.label else ""
            lines.append(f"   → {label}{t('widgets.view.step', n=numbers[edge.target])}")
    return "\n".join(lines)


def _fb_counts(d: CountsData) -> str:
    unit = f" {d.unit}" if d.unit else ""
    return "\n".join(f"{i.label}: {i.value}{unit}" for i in d.items)


def _fb_evidence(d: EvidenceData) -> str:
    lines: list[str] = []
    for c in d.claims:
        when = f", {c.date}" if c.date else ""
        rel = f" [{t('widgets.view.' + c.relation)}]" if c.relation != "neutral" else ""
        lines.append(f"„{c.quote}“ ({c.source}{when}){rel}")
    return "\n".join(lines)


def _fb_tree_change(d: TreeChangeData) -> str:
    lines: list[str] = []
    for c in d.changes:
        if c.before:
            line = t("widgets.view.moved", title=c.title, before=c.before, after=c.after)
        else:
            line = t("widgets.view.added", title=c.title, after=c.after)
        if c.confidence is not None:
            line += f" ({round(c.confidence * 100)}%)"
        lines.append(line)
    return "\n".join(lines)


def _fb_approval_card(d: ApprovalCardData) -> str:
    lines = [d.summary]
    lines += [f"• {e}" for e in d.effects]
    lines.append("")
    lines += [f"{i}. {a.label}" for i, a in enumerate(d.actions, 1)]
    return "\n".join(lines)


_FALLBACKS: dict[str, Any] = {
    "timeline": _fb_timeline,
    "mind_map": _fb_mind_map,
    "graph_slice": _fb_graph_slice,
    "comparison": _fb_comparison,
    "flow": _fb_flow,
    "counts": _fb_counts,
    "evidence": _fb_evidence,
    "tree_change": _fb_tree_change,
    "approval_card": _fb_approval_card,
}
