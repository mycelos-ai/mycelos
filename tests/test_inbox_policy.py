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


def test_inbox_kinds_is_exactly_the_consequence_set() -> None:
    """Pin the contents, not just the membership property.

    A loop over INBOX_KINDS proves nothing, because needs_human fails
    closed and would answer True for any subset — including the empty
    one. Downstream code builds UI from this set, so losing a kind here
    must break a test.
    """
    assert INBOX_KINDS == frozenset({
        "merge",
        "new_topic",
        "new_topic_confirm",
        "scope_violation",
        "failed_run",
        "unclassifiable",
    })
    for kind in INBOX_KINDS:
        assert needs_human(kind) is True


def test_needs_human_is_true_beyond_inbox_kinds() -> None:
    """The asymmetry is deliberate.

    INBOX_KINDS is not the whole True-set: optimization kinds are absent
    and answer False, while an unknown kind is also absent but answers
    True.
    """
    for kind in ("move", "link", "refine_type"):
        assert kind not in INBOX_KINDS
        assert needs_human(kind) is False
    assert "frobnicate" not in INBOX_KINDS
    assert needs_human("frobnicate") is True


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
    e = {"kind": "link", "run_id": "r1", "source": "yt-summary"}
    f = {"kind": "refine_type", "run_id": "r1", "source": "yt-summary"}
    assert collapse_key(a) == collapse_key(b)
    assert collapse_key(a) != collapse_key(c)      # different run
    assert collapse_key(a) != collapse_key(d)      # different source
    assert collapse_key(a) != collapse_key(e)      # different kind
    assert collapse_key(a) != collapse_key(f)      # different kind
    assert collapse_key(e) != collapse_key(f)      # two kinds stay apart


def test_collapse_key_components_cannot_run_together() -> None:
    """A separator inside a value must not fake a different grouping.

    A source id is free text, so it may contain any character. Two
    unrelated groups sharing one key would merge two summary lines into
    one wrong line.
    """
    a = {"kind": "move", "run_id": "a|b", "source": "c"}
    b = {"kind": "move", "run_id": "a", "source": "b|c"}
    assert collapse_key(a) != collapse_key(b)


def test_missing_source_and_none_source_share_a_group() -> None:
    """Both say "no source", so both belong to the same group."""
    missing = {"kind": "move", "run_id": "r1"}
    explicit = {"kind": "move", "run_id": "r1", "source": None}
    assert collapse_key(missing) == collapse_key(explicit)
    assert collapse_key(missing) is not None


def test_entry_without_run_does_not_collapse() -> None:
    """No run id means no group — it stands alone rather than merging
    with unrelated entries."""
    assert collapse_key({"kind": "move", "source": "gmail"}) is None
