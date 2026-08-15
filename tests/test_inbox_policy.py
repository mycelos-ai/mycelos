"""Which entries need a human, and which may collapse into one."""
from __future__ import annotations

from mycelos.knowledge.inbox_policy import (
    INBOX_KINDS, collapse_key, is_collapsible, needs_human,
)


def test_consequence_kinds_need_a_human() -> None:
    assert needs_human("merge") is True            # destructive
    assert needs_human("new_topic_confirm") is True  # structural
    assert needs_human("new_topic") is True
    assert needs_human("scope_violation") is True
    assert needs_human("failed_run") is True
    assert needs_human("unclassifiable") is True


def test_optimization_kinds_do_not() -> None:
    """A placement suggestion is a nice-to-have: ignoring it breaks nothing."""
    assert needs_human("move") is False
    assert needs_human("link") is False
    assert needs_human("refine_type") is False


def test_unknown_kind_needs_a_human() -> None:
    """Fail closed: an unrecognised entry is shown, not hidden."""
    assert needs_human("frobnicate") is True


def test_inbox_kinds_matches_needs_human() -> None:
    for kind in INBOX_KINDS:
        assert needs_human(kind) is True
    assert "move" not in INBOX_KINDS


def test_consequence_entries_never_collapse() -> None:
    """Ten merges are ten irreversible decisions, not one summary line."""
    assert is_collapsible("merge") is False
    assert is_collapsible("failed_run") is False
    assert is_collapsible("new_topic_confirm") is False
    assert collapse_key({"kind": "merge", "run_id": "r1",
                         "source": "yt-summary"}) is None


def test_optimization_entries_collapse_per_run_source_kind() -> None:
    a = {"kind": "move", "run_id": "r1", "source": "yt-summary"}
    b = {"kind": "move", "run_id": "r1", "source": "yt-summary"}
    c = {"kind": "move", "run_id": "r2", "source": "yt-summary"}
    d = {"kind": "move", "run_id": "r1", "source": "gmail"}
    assert collapse_key(a) == collapse_key(b)
    assert collapse_key(a) != collapse_key(c)      # different run
    assert collapse_key(a) != collapse_key(d)      # different source


def test_entry_without_run_does_not_collapse() -> None:
    """No run id means no group — it stands alone rather than merging
    with unrelated entries."""
    assert collapse_key({"kind": "move", "source": "gmail"}) is None
