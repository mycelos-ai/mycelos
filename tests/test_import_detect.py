from __future__ import annotations

from mycelos.knowledge.import_pipeline import FileEntry, detect_import_mode


def _mk(paths: list[str]) -> list[FileEntry]:
    return [FileEntry(relpath=p, content=b"# x\nbody\n") for p in paths]


def test_flat_is_suggest() -> None:
    assert detect_import_mode(_mk(["a.md", "b.md", "c.md"])) == "suggest"


def test_one_folder_is_suggest() -> None:
    assert detect_import_mode(_mk(["notes/a.md", "notes/b.md"])) == "suggest"


def test_two_folders_is_suggest() -> None:
    assert detect_import_mode(_mk(["a/one.md", "b/two.md"])) == "suggest"


def test_three_folders_is_preserve() -> None:
    assert detect_import_mode(_mk(["a/x.md", "b/y.md", "c/z.md"])) == "preserve"


def test_mixed_counts_folders_correctly() -> None:
    files = _mk(["root.md", "a/x.md", "b/y.md", "c/z.md"])
    assert detect_import_mode(files) == "preserve"


def test_empty_is_suggest() -> None:
    assert detect_import_mode([]) == "suggest"
