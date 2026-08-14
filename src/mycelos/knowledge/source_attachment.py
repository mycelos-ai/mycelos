"""Source-to-folder attachment: pure subtree logic + the storage service.

The pure functions decide *where a source may file*; they know nothing
about storage or LLMs, mirroring ``organizer.py``. The service below owns
the two declarative-state tables (Constitution Rule 2).

An attachment opens a subtree: the attached folder and everything beneath
it, never above and never into a sibling branch. The empty string is the
root attachment and means "anywhere".
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mycelos.knowledge")


def _covers(attachment: str, path: str) -> bool:
    """True when `path` is `attachment` itself or lives beneath it.

    Segment-aware on purpose: a naive startswith would let an attachment
    on "topics/work" leak into "topics/workshop".
    """
    if attachment == "":
        return True
    return path == attachment or path.startswith(attachment + "/")


def permitted_paths(attachments: list[str], all_topics: list[str]) -> list[str]:
    """Every existing topic the source may file into, sorted, deduplicated."""
    if not attachments:
        return []
    permitted = {
        path for path in all_topics
        for attachment in attachments
        if _covers(attachment, path)
    }
    # An attachment may point at a topic that no longer exists; keep the
    # ones that do so the caller can still offer them.
    permitted |= {a for a in attachments if a and a in all_topics}
    return sorted(permitted)


def is_permitted(path: str, attachments: list[str]) -> bool:
    """Whether a proposed path lies inside any attachment's subtree."""
    if not path or not attachments:
        return False
    return any(_covers(a, path) for a in attachments)


def fallback_path(attachments: list[str]) -> str:
    """Where content lands when nothing fits: the first attachment, else root."""
    return attachments[0] if attachments else ""


def needs_confirmation(proposed_path: str, attachments: list[str]) -> bool:
    """True when a NEW folder would sit directly under an attachment.

    Those open a new main category for the source and always go to the
    inbox, regardless of confidence. Anything deeper is fine-sorting
    inside a category the user already accepted.
    """
    if not proposed_path:
        return False
    parent = proposed_path.rsplit("/", 1)[0] if "/" in proposed_path else ""
    for attachment in attachments:
        if attachment == "":
            # Root attachment: a top-level topic is a structural decision.
            if parent in ("", "topics"):
                return True
        elif parent == attachment:
            return True
    return False
