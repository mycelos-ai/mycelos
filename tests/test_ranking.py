from __future__ import annotations

from mycelos.knowledge.ranking import rrf_fuse


def _r(path: str, **extra) -> dict:
    return {"path": path, "title": path, **extra}


def test_single_list_preserves_order_and_scores() -> None:
    fused = rrf_fuse([[_r("a"), _r("b"), _r("c")]], limit=10)
    assert [x["path"] for x in fused] == ["a", "b", "c"]
    assert fused[0]["rrf_score"] == 1 / 61  # k=60, rank 0
    assert fused[1]["rrf_score"] == 1 / 62


def test_result_in_both_lists_outranks_single_list_results() -> None:
    fts = [_r("only-fts"), _r("both")]
    vec = [_r("both"), _r("only-vec")]
    fused = rrf_fuse([fts, vec], limit=10)
    # "both": 1/62 + 1/61 > "only-fts": 1/61 > "only-vec": 1/62
    assert [x["path"] for x in fused] == ["both", "only-fts", "only-vec"]


def test_dedup_keeps_first_seen_dict() -> None:
    fts = [_r("x", origin="fts")]
    vec = [_r("x", origin="vec")]
    fused = rrf_fuse([fts, vec], limit=10)
    assert len(fused) == 1
    assert fused[0]["origin"] == "fts"  # earlier list wins


def test_empty_lists_are_skipped() -> None:
    assert rrf_fuse([[], []], limit=10) == []
    fused = rrf_fuse([[], [_r("a")]], limit=10)
    assert [x["path"] for x in fused] == ["a"]


def test_limit_truncates() -> None:
    fused = rrf_fuse([[_r(f"n{i}") for i in range(20)]], limit=5)
    assert len(fused) == 5


def test_input_dicts_are_not_mutated() -> None:
    original = _r("a")
    rrf_fuse([[original]], limit=10)
    assert "rrf_score" not in original
