"""JSON serialization for Widget IR — to_dict / from_dict roundtrip."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mycelos.widgets.types import (
    Choice,
    ChoiceBox,
    CodeBlock,
    Compose,
    Confirm,
    ImageBlock,
    KnowledgeView,
    ProgressBar,
    StatusCard,
    Table,
    TextBlock,
)

# Map class → type string (snake_case)
_TYPE_MAP: dict[type, str] = {
    TextBlock: "text_block",
    Table: "table",
    ChoiceBox: "choice_box",
    StatusCard: "status_card",
    ProgressBar: "progress_bar",
    CodeBlock: "code_block",
    Confirm: "confirm",
    ImageBlock: "image_block",
    Compose: "compose",
}

# Knowledge views carry their catalog type ("timeline", ...) on the wire.
# They are not in _CLASS_MAP: widget_from_dict routes them through
# views.view_from_payload, which validates and recomputes the fallback.

# Reverse map
_CLASS_MAP: dict[str, type] = {v: k for k, v in _TYPE_MAP.items()}


def widget_to_dict(widget: Any) -> dict[str, Any]:
    """Serialize a widget to a JSON-compatible dict."""
    type_name = _TYPE_MAP.get(type(widget))
    if type_name is None and not isinstance(widget, KnowledgeView):
        raise ValueError(f"Unknown widget class: {type(widget)}")

    if isinstance(widget, KnowledgeView):
        from mycelos.widgets.views import view_to_dict

        return view_to_dict(widget)

    if isinstance(widget, Compose):
        return {
            "type": "compose",
            "children": [widget_to_dict(c) for c in widget.children],
        }

    if isinstance(widget, ChoiceBox):
        return {
            "type": "choice_box",
            "prompt": widget.prompt,
            "options": [asdict(o) for o in widget.options],
        }

    d = asdict(widget)
    d["type"] = type_name
    return d


def widget_from_dict(d: dict[str, Any]) -> Any:
    """Deserialize a dict back to a widget instance."""
    type_name = d.get("type")
    cls = _CLASS_MAP.get(type_name)  # type: ignore[arg-type]
    if cls is None:
        from mycelos.widgets.views import VIEW_TYPES, view_from_payload

        if type_name in VIEW_TYPES:
            fallback = d.get("fallback_text") if isinstance(d.get("fallback_text"), str) else ""
            return view_from_payload(d, fallback=fallback)
        raise ValueError(f"Unknown widget type: {type_name!r}")

    fields = {k: v for k, v in d.items() if k != "type"}

    if cls is Compose:
        return Compose(children=[widget_from_dict(c) for c in fields["children"]])

    if cls is ChoiceBox:
        return ChoiceBox(
            prompt=fields["prompt"],
            options=[Choice(**o) for o in fields["options"]],
        )

    return cls(**fields)
