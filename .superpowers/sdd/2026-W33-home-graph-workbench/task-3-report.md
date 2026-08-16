# Task 3 Report: Safe Parent Changes

## Red Tests

The missing-topic test failed before the code change.

```text
FAILED tests/test_knowledge_graph_api.py::test_note_move_rejects_a_missing_topic_without_changes[update]
assert 200 == 422
1 failed, 1 warning in 3.25s
```

The inactive-topic test also failed before the code change.

```text
FAILED tests/test_knowledge_graph_api.py::test_note_move_rejects_an_inactive_topic_without_changes[update]
assert 200 == 422
1 failed, 1 warning in 3.71s
```

## Green Tests

```text
10 passed, 1 warning in 17.34s
```

This run covered the missing target, non-topic target, inactive target,
self move, and descendant cycle tests through both HTTP routes.

```text
37 passed, 1 warning in 9.41s
```

This run covered valid note and topic moves, legacy move behavior, path
protection, graph parent links, and organizer callers.

```text
38 passed, 1 warning in 21.67s
```

This run covered the graph API and the Knowledge V2 tests.

```text
2 passed in 0.24s
```

This run covered the path traversal test and graph parent-link test.

The warnings come from the existing FastAPI TestClient deprecation warning.

## Changed Files

- `src/mycelos/knowledge/service.py`
- `src/mycelos/gateway/routers/knowledge.py`
- `tests/test_knowledge_graph_api.py`
- `.superpowers/sdd/2026-W33-home-graph-workbench/task-3-report.md`

## Commit

`fix(knowledge): validate parent moves`

## Self-Review

- The service checks that the source and target exist before it writes data.
- The target must be an active topic.
- The service walks from the target to the root with a visited set.
- A self move, a descendant move, and an existing parent loop return `False`.
- A rejected move returns before the database or Markdown write.
- Both HTTP move routes return a `422` response with `move_rejected`.
- Valid note and topic moves still return success.
- I checked the organizer, tool, merge, and API callers of `move_to_topic`.
