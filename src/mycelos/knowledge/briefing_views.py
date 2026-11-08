"""The daily briefing as catalog views (Package 8, slice 3).

Turns the deterministic briefing facts into views from the knowledge view
catalog (``mycelos.widgets.views``). No LLM, no renderer dependency: the
result is a list of validated ``KnowledgeView`` objects, serialised with
``view_to_dict`` for the web chat and rendered as text by the CLI and
Telegram renderers.

Order of the visual briefing (spec 2026-W45, "The daily briefing as the
first message"):

1. Lead text (the synthesis, carried as ``lead`` next to the views).
2. ``counts``: arrivals since the last briefing by source.
3. Cards that need the user: live inbox entries (``needs_you``), carried
   next to the views because they keep their own resolve actions.
4. ``evidence``: new contradictions, as a teaser.
5. ``graph_slice``: one rediscovered older note and the arrival it relates to.

A fact group without data yields no view. Data that does not validate is
dropped (logged), never shown half-built.
"""

from __future__ import annotations

import logging
from typing import Any

from mycelos.i18n import t
from mycelos.widgets.types import KnowledgeView, Ref
from mycelos.widgets.views import InvalidView, make_view

logger = logging.getLogger("mycelos.briefing")

_MAX_COUNT_ITEMS = 8
_SHORT = 300


def _cut(text: Any, limit: int = _SHORT) -> str:
    """Note titles and labels are user data of any length; views have limits."""
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def counts_view(facts: dict) -> KnowledgeView | None:
    arrivals = facts.get("arrivals_by_source") or []
    if not arrivals:
        return None
    items = arrivals[:_MAX_COUNT_ITEMS]
    rest = arrivals[_MAX_COUNT_ITEMS:]
    data_items = [{"label": _cut(a["label"]), "value": int(a["count"])} for a in items]
    if rest:
        data_items.append({
            "label": t("briefing.view.other_sources"),
            "value": sum(int(a["count"]) for a in rest),
        })
    return make_view(
        "counts",
        title=t("briefing.view.arrivals_title"),
        reason=t("briefing.view.arrivals_reason"),
        data={"items": data_items, "unit": t("briefing.view.notes_unit")},
        alternatives=["text"],
    )


def evidence_view(facts: dict) -> KnowledgeView | None:
    contradictions = facts.get("contradictions") or []
    if not contradictions:
        return None
    claims: list[dict[str, Any]] = []
    refs: list[Ref] = []
    for c in contradictions:
        claims.append({
            "quote": _cut(c.get("reason") or t("briefing.view.no_reason"), 2000),
            "source": _cut(f"{c['from_title']} ↔ {c['to_title']}"),
            "date": (c.get("changed_at") or "")[:10],
            "relation": "contradicts",
        })
        refs.append(Ref(path=c["from_path"], title=c["from_title"]))
        refs.append(Ref(path=c["to_path"], title=c["to_title"]))
    return make_view(
        "evidence",
        title=t("briefing.view.contradictions_title"),
        reason=t("briefing.view.contradictions_reason"),
        data={"claims": claims},
        refs=_unique(refs),
        alternatives=["text"],
    )


def rediscovered_view(facts: dict) -> KnowledgeView | None:
    found = facts.get("rediscovered")
    if not found:
        return None
    return make_view(
        "graph_slice",
        title=t("briefing.view.rediscovered_title", title=found["title"]),
        reason=t(
            "briefing.view.rediscovered_reason",
            year=(found.get("created_at") or "")[:4] or "?",
        ),
        data={
            "nodes": [
                {"id": found["because_path"], "label": _cut(found["because_title"]), "kind": "new"},
                {"id": found["path"], "label": _cut(found["title"]), "kind": "note"},
            ],
            "edges": [{
                "source": found["because_path"],
                "target": found["path"],
                "relation": _cut(t("briefing.view.related"), 60),
            }],
        },
        refs=[
            Ref(path=found["path"], title=found["title"]),
            Ref(path=found["because_path"], title=found["because_title"]),
        ],
        alternatives=["text"],
    )


_BUILDERS = (counts_view, evidence_view, rediscovered_view)


def build_views(facts: dict) -> list[KnowledgeView]:
    """All views of the visual briefing that have data, in display order."""
    views: list[KnowledgeView] = []
    for builder in _BUILDERS:
        try:
            view = builder(facts)
        except (InvalidView, KeyError, TypeError, ValueError):
            logger.warning("briefing view %s dropped", builder.__name__, exc_info=True)
            continue
        if view is not None:
            views.append(view)
    return views


def _unique(refs: list[Ref]) -> list[Ref]:
    seen: set[str] = set()
    out: list[Ref] = []
    for ref in refs:
        if ref.path not in seen:
            seen.add(ref.path)
            out.append(ref)
    return out
