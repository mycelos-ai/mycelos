"""Sample knowledge views for ``/demo view`` and the renderer tests.

One view per catalog type, built through ``make_view`` so the samples always
match the real wire format. The data is invented; it touches no user knowledge.
"""

from __future__ import annotations

from mycelos.widgets.types import KnowledgeView, Ref
from mycelos.widgets.views import make_view


def demo_views() -> list[KnowledgeView]:
    """Return one validated sample view for every type in the catalog."""
    client = [Ref(path="clients/mueller/mail-2026-11-03.md", title="Mail 03.11."),
              Ref(path="clients/mueller/call-notes.md", title="Call notes")]
    return [
        make_view(
            "timeline",
            title="Müller GmbH: what is open",
            reason="Question about a course of events; every source has a date",
            data={"events": [
                {"date": "2026-10-28", "text": "Call: Q3 closing agreed for mid November"},
                {"date": "2026-11-03", "text": "Mail: Q3 receipts still missing", "kind": "warning"},
                {"date": "2026-11-15", "text": "Deadline: Q3 closing", "kind": "due"},
            ]},
            refs=client,
            alternatives=["graph_slice", "text"],
        ),
        make_view(
            "mind_map",
            title="Client onboarding",
            reason="Overview of one topic with a clear hierarchy",
            data={"root": {"label": "Client onboarding", "children": [
                {"label": "Contract", "children": [{"label": "Engagement letter"}, {"label": "Data processing (GDPR)"}]},
                {"label": "Access", "children": [{"label": "DATEV"}, {"label": "Bank feeds"}]},
                {"label": "First month", "children": [{"label": "Opening balance"}, {"label": "Receipts: \"how & where\""}]},
            ]}},
            refs=[Ref(path="processes/onboarding.md", title="Onboarding checklist")],
            alternatives=["flow", "text"],
        ),
        make_view(
            "graph_slice",
            title="How Müller relates to the Q3 closing",
            reason="Question about how several things relate",
            data={
                "nodes": [
                    {"id": "mueller", "label": "Müller GmbH", "kind": "client"},
                    {"id": "q3", "label": "Q3 closing", "kind": "task"},
                    {"id": "receipts", "label": "Missing receipts", "kind": "note"},
                    {"id": "datev", "label": "DATEV export", "kind": "note"},
                ],
                "edges": [
                    {"source": "mueller", "target": "q3", "relation": "due"},
                    {"source": "q3", "target": "receipts", "relation": "blocked by"},
                    {"source": "mueller", "target": "datev"},
                ],
            },
            refs=client,
            alternatives=["timeline", "text"],
        ),
        make_view(
            "comparison",
            title="Note apps for the vault",
            reason="Two to four options judged by the same criteria",
            data={
                "options": ["Obsidian", "Logseq"],
                "rows": [
                    {"criterion": "Plain Markdown files", "cells": [
                        {"value": "yes", "verdict": "good"}, {"value": "yes, with outliner syntax", "verdict": "neutral"}]},
                    {"criterion": "Mobile sync", "cells": [
                        {"value": "paid or self-built", "verdict": "bad"}, {"value": "self-built", "verdict": "bad"}]},
                ],
            },
            alternatives=["text"],
        ),
        make_view(
            "flow",
            title="Monthly receipts",
            reason="A process with one decision",
            data={
                "steps": [
                    {"id": "collect", "text": "Collect receipts"},
                    {"id": "check", "text": "All receipts there?"},
                    {"id": "remind", "text": "Remind client"},
                    {"id": "book", "text": "Book month"},
                ],
                "edges": [
                    {"source": "collect", "target": "check"},
                    {"source": "check", "target": "book", "label": "yes"},
                    {"source": "check", "target": "remind", "label": "no"},
                    {"source": "remind", "target": "collect"},
                ],
            },
            refs=[Ref(path="processes/receipts.md", title="Receipts process")],
            alternatives=["mind_map", "text"],
        ),
        make_view(
            "counts",
            title="What arrived since yesterday",
            reason="Quantities across sources",
            data={"items": [
                {"label": "yt-summary", "value": 4},
                {"label": "Gmail", "value": 11},
                {"label": "Telegram", "value": 2},
                {"label": "Obsidian vault", "value": 0},
            ], "unit": "notes"},
            alternatives=["text"],
        ),
        make_view(
            "evidence",
            title="When is the Q3 deadline?",
            reason="Sources contradict each other",
            data={"claims": [
                {"quote": "We agreed on November 15 for the Q3 closing.", "source": "Call notes", "date": "2026-10-28", "relation": "supports"},
                {"quote": "Please have Q3 ready by the end of November.", "source": "Mail 03.11.", "date": "2026-11-03", "relation": "contradicts"},
            ]},
            refs=client,
            alternatives=["timeline", "text"],
        ),
        make_view(
            "tree_change",
            title="Where your note went",
            reason="The filing structure changed",
            data={"changes": [
                {"title": "Client onboarding checklist", "after": "processes/onboarding.md", "confidence": 0.86},
                {"title": "DATEV export", "before": "inbox/datev.md", "after": "clients/mueller/datev.md", "confidence": 0.72},
            ]},
            alternatives=["text"],
        ),
        make_view(
            "approval_card",
            title="Attach Obsidian vault",
            reason="This would change your knowledge, so it needs your approval",
            data={
                "summary": "Import the vault ~/Notes into knowledge/obsidian and keep it in sync.",
                "effects": ["312 notes imported", "Rule: files under Clients/ go to clients/"],
                "actions": [{"id": "approve", "label": "Attach"}, {"id": "preview", "label": "Preview first"},
                            {"id": "cancel", "label": "Cancel"}],
            },
        ),
    ]
