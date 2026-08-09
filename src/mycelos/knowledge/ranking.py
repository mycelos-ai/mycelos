"""Pure ranking logic — Reciprocal Rank Fusion.

No storage, no LLM, no I/O. This module is the testable core, mirroring
``organizer.py``'s pattern. Spec: docs/superpowers/specs/
2026-W32-hybrid-retrieval-design.md (WP1).
"""
from __future__ import annotations

RRF_K = 60


def rrf_fuse(
    ranked_lists: list[list[dict]],
    k: int = RRF_K,
    limit: int = 10,
) -> list[dict]:
    """Reciprocal Rank Fusion over result lists.

    Each result dict must carry "path". A result at zero-based rank r in
    one list contributes 1/(k + r + 1); contributions sum across lists.
    Returns the first-seen dict per path (earlier lists win) with
    "rrf_score" added, ordered by descending score, truncated to limit.
    Input dicts are not mutated.
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, dict] = {}
    for results in ranked_lists:
        for rank, result in enumerate(results):
            path = result.get("path")
            if not path:
                continue
            scores[path] = scores.get(path, 0.0) + 1.0 / (k + rank + 1)
            if path not in first_seen:
                first_seen[path] = result
    fused = []
    for path, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        entry = dict(first_seen[path])
        entry["rrf_score"] = score
        fused.append(entry)
    return fused[:limit]
