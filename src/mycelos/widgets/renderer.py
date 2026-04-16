"""WidgetRenderer Protocol — interface for channel-specific rendering."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from mycelos.widgets.types import (
    ChoiceBox,
    CodeBlock,
    Compose,
    Confirm,
    ImageBlock,
    ProgressBar,
    StatusCard,
    Table,
    TextBlock,
)

# Widget type → renderer method name
_DISPATCH: dict[type, str] = {
    TextBlock: "render_text_block",
    Table: "render_table",
    ChoiceBox: "render_choice_box",
    StatusCard: "render_status_card",
    ProgressBar: "render_progress_bar",
    CodeBlock: "render_code_block",
    Confirm: "render_confirm",
    ImageBlock: "render_image_block",
    Compose: "render_compose",
}


@runtime_checkable
class WidgetRenderer(Protocol):
    """Channel-specific widget renderer."""

    def render_text_block(self, widget: TextBlock) -> Any: ...
    def render_table(self, widget: Table) -> Any: ...
    def render_choice_box(self, widget: ChoiceBox) -> Any: ...
    def render_status_card(self, widget: StatusCard) -> Any: ...
    def render_progress_bar(self, widget: ProgressBar) -> Any: ...
    def render_code_block(self, widget: CodeBlock) -> Any: ...
    def render_confirm(self, widget: Confirm) -> Any: ...
    def render_image_block(self, widget: ImageBlock) -> Any: ...
    def render_compose(self, widget: Compose) -> Any: ...


def dispatch_render(renderer: Any, widget: Any) -> Any:
    """Dispatch a widget to the correct renderer method."""
    method_name = _DISPATCH.get(type(widget))
    if method_name is None:
        raise ValueError(f"No renderer for widget type: {type(widget)}")
    return getattr(renderer, method_name)(widget)
