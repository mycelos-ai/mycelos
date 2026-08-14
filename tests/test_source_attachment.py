"""Pure subtree logic for source attachments."""
from __future__ import annotations

from mycelos.knowledge.source_attachment import (
    fallback_path,
    is_permitted,
    needs_confirmation,
    permitted_paths,
)

TOPICS = [
    "topics/work",
    "topics/work/vorfina",
    "topics/work/vorfina/mandanten",
    "topics/work/vorfina/mandanten/mueller",
    "topics/workshop",          # the prefix trap
    "topics/private",
]


def test_attachment_permits_itself_and_its_subtree() -> None:
    got = permitted_paths(["topics/work/vorfina"], TOPICS)
    assert got == [
        "topics/work/vorfina",
        "topics/work/vorfina/mandanten",
        "topics/work/vorfina/mandanten/mueller",
    ]


def test_prefix_trap_workshop_is_not_under_work() -> None:
    got = permitted_paths(["topics/work"], TOPICS)
    assert "topics/workshop" not in got
    assert "topics/work" in got
    assert "topics/work/vorfina" in got


def test_root_attachment_permits_everything() -> None:
    assert set(permitted_paths([""], TOPICS)) == set(TOPICS)


def test_several_attachments_union_their_subtrees() -> None:
    got = permitted_paths(["topics/private", "topics/workshop"], TOPICS)
    assert got == ["topics/private", "topics/workshop"]


def test_no_attachments_permits_nothing() -> None:
    # The caller treats this as "root" via fallback_path; the pure
    # resolver reports the literal truth.
    assert permitted_paths([], TOPICS) == []


def test_is_permitted_exact_descendant_ancestor_sibling() -> None:
    att = ["topics/work/vorfina"]
    assert is_permitted("topics/work/vorfina", att) is True
    assert is_permitted("topics/work/vorfina/mandanten", att) is True
    assert is_permitted("topics/work", att) is False           # upwards
    assert is_permitted("topics/private", att) is False        # sideways
    assert is_permitted("topics/work/vorfina2", att) is False  # prefix trap


def test_is_permitted_under_root_attachment() -> None:
    assert is_permitted("topics/anything/deep", [""]) is True


def test_fallback_is_first_attachment_else_root() -> None:
    assert fallback_path(["topics/b", "topics/a"]) == "topics/b"
    assert fallback_path([]) == ""
    assert fallback_path([""]) == ""


def test_needs_confirmation_directly_under_attachment() -> None:
    att = ["topics/work/vorfina"]
    assert needs_confirmation("topics/work/vorfina/schmidt", att) is True


def test_no_confirmation_deeper_than_attachment() -> None:
    att = ["topics/work/vorfina"]
    assert needs_confirmation(
        "topics/work/vorfina/mandanten/schmidt", att) is False


def test_needs_confirmation_applies_per_attachment() -> None:
    att = ["topics/work/vorfina", "topics/research"]
    assert needs_confirmation("topics/work/vorfina/x", att) is True
    assert needs_confirmation("topics/research/y", att) is True
    assert needs_confirmation("topics/work/vorfina/mandanten/x", att) is False


def test_needs_confirmation_under_root_attachment() -> None:
    """Root's direct children are top-level topics — a structural decision."""
    assert needs_confirmation("topics/newthing", [""]) is True
    assert needs_confirmation("topics/newthing/sub", [""]) is False
