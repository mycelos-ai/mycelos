"""API tests for persisted knowledge graph node positions."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mycelos.gateway.server import create_app
from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def graph_api_client() -> TestClient:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-knowledge-graph-api"

        from mycelos.app import App
        from mycelos.setup import web_init

        app = App(data_dir)
        app.initialize()
        web_init(app, api_key="sk-ant-api03-FAKETESTKEYFORGRAPHAPI")

        fastapi_app = create_app(
            data_dir,
            no_scheduler=True,
            host="0.0.0.0",
            allow_insecure_bind=True,
        )
        yield TestClient(fastapi_app, raise_server_exceptions=False)


def _create_note(client: TestClient, title: str = "Graph node") -> str:
    response = client.post("/api/knowledge/notes", json={"title": title})
    assert response.status_code == 200, response.text
    return response.json()["path"]


def _create_topic(
    client: TestClient, name: str, parent: str | None = None
) -> str:
    body: dict[str, str] = {"name": name}
    if parent is not None:
        body["parent"] = parent
    response = client.post("/api/knowledge/topics", json=body)
    assert response.status_code == 200, response.text
    return response.json()["path"]


def _move_request(
    client: TestClient, route: str, path: str, target: str | None
):
    if route == "update":
        return client.put(
            f"/api/knowledge/notes/{path}", json={"parent_path": target}
        )
    return client.post(
        f"/api/knowledge/notes/{path}/move", json={"topic": target}
    )


def _parent_and_markdown(client: TestClient, path: str) -> tuple[str, str]:
    kb = client.app.state.mycelos.knowledge_base
    meta = kb._indexer.get_note_meta(path)
    assert meta is not None
    return meta["parent_path"], kb._safe_path(path).read_text(encoding="utf-8")


def test_home_summary_counts_today_imports_and_groups_attached_sources(
    graph_api_client: TestClient,
) -> None:
    """Home must read today's imports and grouped source attachments from SQLite."""
    kb = graph_api_client.app.state.mycelos.knowledge_base
    storage = graph_api_client.app.state.mycelos.storage
    work = _create_topic(graph_api_client, "Work")
    research = _create_topic(graph_api_client, "Research")
    imported_today = kb.write(
        "Imported today",
        "Today",
        created_by="import",
        source={"kind": "connector", "connector": "gmail"},
    )
    imported_before = kb.write(
        "Imported before",
        "Before",
        created_by="import",
        source={"kind": "connector", "connector": "gmail"},
    )
    kb.write("User note", "Not an import", created_by="user")
    storage.execute(
        "UPDATE knowledge_notes SET created_at='2000-01-01T00:00:00.000Z' WHERE path=?",
        (imported_before,),
    )
    storage.execute(
        "UPDATE knowledge_notes SET created_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE path=?",
        (imported_today,),
    )
    for source_id, topic_path in [
        ("gmail", work),
        ("yt-summary", work),
        ("github", research),
    ]:
        storage.execute(
            "INSERT INTO source_attachments (source_id, user_id, topic_path) VALUES (?, 'default', ?)",
            (source_id, topic_path),
        )

    response = graph_api_client.get("/api/knowledge/home-summary")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "imports_today": 1,
        "sources_by_topic": {
            research: ["github"],
            work: ["gmail", "yt-summary"],
        },
    }


def test_home_summary_returns_zero_for_a_day_without_imports(
    graph_api_client: TestClient,
) -> None:
    """An old import must not create a nonzero Today segment."""
    kb = graph_api_client.app.state.mycelos.knowledge_base
    storage = graph_api_client.app.state.mycelos.storage
    old_path = kb.write("Old import", "Old", created_by="import")
    storage.execute(
        "UPDATE knowledge_notes SET created_at='2000-01-01T00:00:00.000Z' WHERE path=?",
        (old_path,),
    )

    response = graph_api_client.get("/api/knowledge/home-summary")

    assert response.status_code == 200, response.text
    assert response.json() == {"imports_today": 0, "sources_by_topic": {}}


def test_home_summary_filters_source_attachments_for_the_current_user(
    graph_api_client: TestClient,
) -> None:
    """One user must not receive another user's source attachments."""
    storage = graph_api_client.app.state.mycelos.storage
    default_topic = _create_topic(graph_api_client, "Default topic")
    alice_topic = _create_topic(graph_api_client, "Alice topic")
    storage.execute(
        "INSERT OR IGNORE INTO users (id, name, status) VALUES ('alice', 'Alice', 'active')"
    )
    storage.execute(
        "INSERT INTO source_attachments (source_id, user_id, topic_path) VALUES ('gmail', 'default', ?)",
        (default_topic,),
    )
    storage.execute(
        "INSERT INTO source_attachments (source_id, user_id, topic_path) VALUES ('yt-summary', 'alice', ?)",
        (alice_topic,),
    )

    default_summary = graph_api_client.get("/api/knowledge/home-summary")
    alice_summary = graph_api_client.get(
        "/api/knowledge/home-summary", headers={"X-User-Id": "alice"}
    )

    assert default_summary.json()["sources_by_topic"] == {
        default_topic: ["gmail"]
    }
    assert alice_summary.json()["sources_by_topic"] == {
        alice_topic: ["yt-summary"]
    }


@pytest.mark.parametrize("route", ["update", "move"])
def test_note_move_rejects_a_missing_topic_without_changes(
    graph_api_client: TestClient, route: str
) -> None:
    """A missing target must not change the source note or its Markdown file."""
    source = _create_note(graph_api_client)
    before = _parent_and_markdown(graph_api_client, source)

    response = _move_request(graph_api_client, route, source, "topics/missing")

    assert response.status_code == 422
    assert response.json()["error"] == "move_rejected"
    assert _parent_and_markdown(graph_api_client, source) == before


@pytest.mark.parametrize("route", ["update", "move"])
def test_note_move_rejects_a_note_target_without_changes(
    graph_api_client: TestClient, route: str
) -> None:
    """Only an active topic can become the new parent."""
    source = _create_note(graph_api_client, "Source")
    target = _create_note(graph_api_client, "Not a topic")
    before = _parent_and_markdown(graph_api_client, source)

    response = _move_request(graph_api_client, route, source, target)

    assert response.status_code == 422
    assert response.json()["error"] == "move_rejected"
    assert _parent_and_markdown(graph_api_client, source) == before


@pytest.mark.parametrize("route", ["update", "move"])
def test_note_move_rejects_an_inactive_topic_without_changes(
    graph_api_client: TestClient, route: str
) -> None:
    """An inactive topic must not become the new parent."""
    source = _create_note(graph_api_client, "Source")
    target = _create_topic(graph_api_client, "Inactive target")
    graph_api_client.app.state.mycelos.knowledge_base.update(target, status="archived")
    before = _parent_and_markdown(graph_api_client, source)

    response = _move_request(graph_api_client, route, source, target)

    assert response.status_code == 422
    assert response.json()["error"] == "move_rejected"
    assert _parent_and_markdown(graph_api_client, source) == before


@pytest.mark.parametrize("route", ["update", "move"])
def test_note_move_rejects_a_topic_without_markdown_without_changes(
    graph_api_client: TestClient, route: str
) -> None:
    """A target topic must keep its Markdown file before it can receive a note."""
    source = _create_note(graph_api_client, "Source")
    target = _create_topic(graph_api_client, "Target without Markdown")
    graph_api_client.app.state.mycelos.knowledge_base._safe_path(target).unlink()
    before = _parent_and_markdown(graph_api_client, source)

    response = _move_request(graph_api_client, route, source, target)

    assert response.status_code == 422
    assert response.json()["error"] == "move_rejected"
    assert _parent_and_markdown(graph_api_client, source) == before


@pytest.mark.parametrize("route", ["update", "move"])
def test_topic_move_rejects_itself_without_changes(
    graph_api_client: TestClient, route: str
) -> None:
    """A topic must not become its own parent."""
    source = _create_topic(graph_api_client, "Source topic")
    before = _parent_and_markdown(graph_api_client, source)

    response = _move_request(graph_api_client, route, source, source)

    assert response.status_code == 422
    assert response.json()["error"] == "move_rejected"
    assert _parent_and_markdown(graph_api_client, source) == before


@pytest.mark.parametrize("route", ["update", "move"])
def test_topic_move_rejects_a_descendant_without_changes(
    graph_api_client: TestClient, route: str
) -> None:
    """A topic must not become a child of one of its own children."""
    source = _create_topic(graph_api_client, "Parent")
    descendant = _create_topic(graph_api_client, "Child", parent=source)
    before = _parent_and_markdown(graph_api_client, source)

    response = _move_request(graph_api_client, route, source, descendant)

    assert response.status_code == 422
    assert response.json()["error"] == "move_rejected"
    assert _parent_and_markdown(graph_api_client, source) == before


@pytest.mark.parametrize("route", ["update", "move"])
@pytest.mark.parametrize("source_type", ["note", "topic"])
def test_note_move_accepts_a_note_or_topic_for_an_active_topic(
    graph_api_client: TestClient, route: str, source_type: str
) -> None:
    """A note or topic can move under a different active topic."""
    target = _create_topic(graph_api_client, "Target")
    if source_type == "topic":
        source = _create_topic(graph_api_client, "Source topic")
    else:
        source = _create_note(graph_api_client, "Source note")

    response = _move_request(graph_api_client, route, source, target)

    assert response.status_code == 200, response.text
    assert _parent_and_markdown(graph_api_client, source)[0] == target


def test_root_note_move_can_be_undone_to_its_stored_notes_parent(
    graph_api_client: TestClient,
) -> None:
    """Undo must restore the stored system parent in the DB and Markdown."""
    source = _create_note(graph_api_client, "Root source")
    target = _create_topic(graph_api_client, "Move target")
    root_parent, root_markdown = _parent_and_markdown(graph_api_client, source)
    assert root_parent == "notes"
    assert "parent_path: notes" in root_markdown

    graph = graph_api_client.get("/api/knowledge/graph")
    graph_node = next(node for node in graph.json()["nodes"] if node["id"] == source)
    assert graph_node["parent_path"] == "notes"

    moved = graph_api_client.put(
        f"/api/knowledge/notes/{source}", json={"parent_path": target}
    )
    assert moved.status_code == 200, moved.text
    moved_parent, moved_markdown = _parent_and_markdown(graph_api_client, source)
    assert moved_parent == target
    assert f"parent_path: {target}" in moved_markdown

    undone = graph_api_client.put(
        f"/api/knowledge/notes/{source}", json={"parent_path": "notes"}
    )
    assert undone.status_code == 200, undone.text
    restored_parent, restored_markdown = _parent_and_markdown(
        graph_api_client, source
    )
    assert restored_parent == "notes"
    assert "parent_path: notes" in restored_markdown


@pytest.mark.parametrize("system_root", ["notes", "tasks"])
@pytest.mark.parametrize("route", ["update", "move"])
def test_note_move_accepts_only_fixed_system_roots(
    graph_api_client: TestClient, system_root: str, route: str
) -> None:
    """The two fixed system roots can receive a note without topic metadata."""
    source = _create_note(graph_api_client, f"Source for {system_root}")
    target = _create_topic(graph_api_client, f"Target for {system_root}")
    assert graph_api_client.put(
        f"/api/knowledge/notes/{source}", json={"parent_path": target}
    ).status_code == 200

    response = _move_request(graph_api_client, route, source, system_root)

    assert response.status_code == 200, response.text
    parent, markdown = _parent_and_markdown(graph_api_client, source)
    assert parent == system_root
    assert f"parent_path: {system_root}" in markdown


@pytest.mark.parametrize("route", ["update", "move"])
def test_root_topic_move_can_be_undone_to_null_parent(
    graph_api_client: TestClient, route: str
) -> None:
    """Only a topic can use null to return to the topic root."""
    source = _create_topic(graph_api_client, "Root topic")
    target = _create_topic(graph_api_client, "Other root topic")
    kb = graph_api_client.app.state.mycelos.knowledge_base
    assert kb._indexer.set_parent(source, None) is True
    before_parent, before_markdown = _parent_and_markdown(graph_api_client, source)
    assert before_parent is None
    assert "parent_path:" not in before_markdown

    graph = graph_api_client.get("/api/knowledge/graph").json()
    graph_node = next(node for node in graph["nodes"] if node["id"] == source)
    assert graph_node["parent_path"] is None

    moved = _move_request(graph_api_client, route, source, target)
    assert moved.status_code == 200, moved.text

    undone = _move_request(graph_api_client, route, source, None)
    assert undone.status_code == 200, undone.text
    restored_parent, restored_markdown = _parent_and_markdown(
        graph_api_client, source
    )
    assert restored_parent is None
    assert "parent_path:" not in restored_markdown


@pytest.mark.parametrize("route", ["update", "move"])
def test_note_move_rejects_null_parent_without_changes(
    graph_api_client: TestClient, route: str
) -> None:
    """A normal note cannot detach from its fixed system root."""
    source = _create_note(graph_api_client, "Root note")
    before = _parent_and_markdown(graph_api_client, source)

    response = _move_request(graph_api_client, route, source, None)

    assert response.status_code == 422
    assert response.json()["error"] == "move_rejected"
    assert _parent_and_markdown(graph_api_client, source) == before


@pytest.mark.parametrize("system_root", ["notes", "tasks"])
def test_topic_parent_chain_accepts_notes_and_tasks_as_terminal_roots(
    graph_api_client: TestClient, system_root: str
) -> None:
    """A valid topic below a fixed system root must remain a valid target."""
    kb = graph_api_client.app.state.mycelos.knowledge_base
    target = kb.write(
        title=f"Nested {system_root} target",
        content=f"# Nested {system_root} target\n",
        type="topic",
        topic=system_root,
    )
    source = _create_topic(graph_api_client, f"{system_root} source topic")

    response = graph_api_client.put(
        f"/api/knowledge/notes/{source}", json={"parent_path": target}
    )

    assert response.status_code == 200, response.text
    assert _parent_and_markdown(graph_api_client, source)[0] == target


@pytest.mark.parametrize(
    ("route", "body"),
    [
        ("update", []),
        ("update", {"parent_path": []}),
        ("move", []),
        ("move", {"topic": []}),
    ],
)
def test_parent_change_rejects_invalid_json_types(
    graph_api_client: TestClient, route: str, body: object
) -> None:
    """Invalid JSON shapes must return 422 instead of a server error."""
    source = _create_note(graph_api_client, "Typed source")
    before = _parent_and_markdown(graph_api_client, source)

    if route == "update":
        response = graph_api_client.put(
            f"/api/knowledge/notes/{source}", json=body
        )
    else:
        response = graph_api_client.post(
            f"/api/knowledge/notes/{source}/move", json=body
        )

    assert response.status_code == 422
    assert _parent_and_markdown(graph_api_client, source) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content", []),
        ("organizer_state", []),
        ("status", []),
        ("tags", "not-a-list"),
        ("tags", ["valid", 7]),
        ("priority", "not-a-number"),
        ("priority", -1),
        ("priority", 4),
        ("priority", 10**400),
        ("archive", "yes"),
    ],
)
def test_parent_change_validates_all_update_fields_before_moving(
    graph_api_client: TestClient, field: str, value: object
) -> None:
    """A later invalid field must not leave an earlier parent change behind."""
    source = _create_note(graph_api_client, f"Invalid {field} source")
    target = _create_topic(graph_api_client, f"Invalid {field} target")
    before = _parent_and_markdown(graph_api_client, source)

    response = graph_api_client.put(
        f"/api/knowledge/notes/{source}",
        json={"parent_path": target, field: value},
    )

    assert response.status_code == 422
    assert _parent_and_markdown(graph_api_client, source) == before


def test_parent_change_leaves_database_and_file_unchanged_on_file_write_failure(
    graph_api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A staged Markdown write failure must happen before the parent row changes."""
    source = _create_note(graph_api_client, "File failure source")
    target = _create_topic(graph_api_client, "File failure target")
    before = _parent_and_markdown(graph_api_client, source)

    def fail_write(*_args, **_kwargs):
        raise OSError("staged write failed")

    monkeypatch.setattr(Path, "write_text", fail_write)

    response = graph_api_client.put(
        f"/api/knowledge/notes/{source}", json={"parent_path": target}
    )

    assert response.status_code == 422
    assert _parent_and_markdown(graph_api_client, source) == before


def test_parent_change_rolls_back_database_and_file_on_database_failure(
    graph_api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed parent-row write must restore the original Markdown file."""
    source = _create_note(graph_api_client, "Database failure source")
    target = _create_topic(graph_api_client, "Database failure target")
    before = _parent_and_markdown(graph_api_client, source)
    indexer = graph_api_client.app.state.mycelos.knowledge_base._indexer
    original_set_parent = indexer.set_parent

    def update_then_fail(path: str, parent: str | None) -> bool:
        original_set_parent(path, parent)
        raise sqlite3.OperationalError("database write failed")

    monkeypatch.setattr(indexer, "set_parent", update_then_fail)

    response = graph_api_client.put(
        f"/api/knowledge/notes/{source}", json={"parent_path": target}
    )

    assert response.status_code == 422
    assert _parent_and_markdown(graph_api_client, source) == before


def test_parent_change_succeeds_when_the_audit_write_fails(
    graph_api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An audit failure after the durable change must not report a failed move."""
    source = _create_note(graph_api_client, "Audit failure source")
    target = _create_topic(graph_api_client, "Audit failure target")

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(
        graph_api_client.app.state.mycelos.audit, "log", fail_audit
    )

    response = graph_api_client.put(
        f"/api/knowledge/notes/{source}", json={"parent_path": target}
    )

    assert response.status_code == 200, response.text
    assert _parent_and_markdown(graph_api_client, source)[0] == target


def test_two_concurrent_opposite_topic_moves_cannot_create_a_cycle(
    graph_api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The validation and update must share one service lock."""
    kb = graph_api_client.app.state.mycelos.knowledge_base
    left = _create_topic(graph_api_client, "Concurrent left")
    right = _create_topic(graph_api_client, "Concurrent right")
    original_get_meta = kb._indexer.get_note_meta
    validation_barrier = threading.Barrier(2)
    local = threading.local()

    def synchronized_get_meta(path: str):
        result = original_get_meta(path)
        local.calls = getattr(local, "calls", 0) + 1
        if local.calls == 3:
            try:
                validation_barrier.wait(timeout=0.2)
            except threading.BrokenBarrierError:
                pass
        return result

    monkeypatch.setattr(kb._indexer, "get_note_meta", synchronized_get_meta)
    start = threading.Barrier(2)
    results: list[bool] = []

    def move(path: str, target: str) -> None:
        start.wait()
        results.append(kb.move_to_topic(path, target))

    first = threading.Thread(target=move, args=(left, right))
    second = threading.Thread(target=move, args=(right, left))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(results) == [False, True]
    left_parent = original_get_meta(left)["parent_path"]
    right_parent = original_get_meta(right)["parent_path"]
    assert not (left_parent == right and right_parent == left)


def test_graph_position_write_is_read_for_the_current_user(
    graph_api_client: TestClient,
) -> None:
    """Each resolved user must read only the position that user saved."""
    path = _create_note(graph_api_client)
    storage = graph_api_client.app.state.mycelos.storage
    storage.execute("DELETE FROM users WHERE id=?", ("alice",))

    default_response = graph_api_client.put(
        f"/api/knowledge/graph/positions/{path}",
        json={"x": 120.5, "y": -42},
    )
    assert default_response.status_code == 200, default_response.text

    alice_response = graph_api_client.put(
        f"/api/knowledge/graph/positions/{path}",
        json={"x": 15, "y": 30},
        headers={"X-User-Id": "alice"},
    )
    assert alice_response.status_code == 200, alice_response.text
    assert alice_response.json() == {"path": path, "x": 15.0, "y": 30.0}

    default_graph = graph_api_client.get("/api/knowledge/graph")
    assert default_graph.status_code == 200, default_graph.text
    assert default_graph.json()["positions"] == {path: {"x": 120.5, "y": -42.0}}

    alice_graph = graph_api_client.get(
        "/api/knowledge/graph", headers={"X-User-Id": "alice"}
    )
    assert alice_graph.status_code == 200, alice_graph.text
    assert alice_graph.json()["positions"] == {path: {"x": 15.0, "y": 30.0}}
    assert storage.fetchone("SELECT id FROM users WHERE id=?", ("alice",)) is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"x": 0},
        {"x": "0", "y": 0},
        {"x": True, "y": 0},
        '{"x": NaN, "y": 0}',
        '{"x": Infinity, "y": 0}',
        {"x": 1_000_001, "y": 0},
        {"x": 10**400, "y": 0},
        {"x": 0, "y": -1_000_001},
    ],
)
def test_graph_position_rejects_invalid_coordinates(
    graph_api_client: TestClient, body: dict[object, object] | str
) -> None:
    """Missing, non-finite, and out-of-range coordinates must not persist."""
    path = _create_note(graph_api_client)

    if isinstance(body, str):
        response = graph_api_client.put(
            f"/api/knowledge/graph/positions/{path}",
            content=body,
            headers={"Content-Type": "application/json"},
        )
    else:
        response = graph_api_client.put(
            f"/api/knowledge/graph/positions/{path}", json=body
        )

    assert response.status_code == 422
    graph = graph_api_client.get("/api/knowledge/graph")
    assert graph.json()["positions"] == {}


def test_graph_position_rejects_an_unknown_node(graph_api_client: TestClient) -> None:
    """A position for a missing graph node must not create a database row."""
    response = graph_api_client.put(
        "/api/knowledge/graph/positions/notes/missing",
        json={"x": 20, "y": 40},
    )

    assert response.status_code == 404
    assert graph_api_client.get("/api/knowledge/graph").json()["positions"] == {}


def test_graph_position_converts_an_integrity_race_to_not_found(
    graph_api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node deletion race must return 404 instead of leaking an integrity error."""
    path = _create_note(graph_api_client, "Position race")
    storage = graph_api_client.app.state.mycelos.storage
    original_execute = storage.execute

    def fail_position_insert(sql: str, params: tuple = ()):
        if "INSERT INTO knowledge_graph_positions" in sql:
            raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")
        return original_execute(sql, params)

    monkeypatch.setattr(storage, "execute", fail_position_insert)

    response = graph_api_client.put(
        f"/api/knowledge/graph/positions/{path}", json={"x": 20, "y": 40}
    )

    assert response.status_code == 404
    assert response.json() == {"error": "not_found", "path": path}


def test_graph_position_integrity_race_rolls_back_the_sqlite_transaction(
    graph_api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected position statement must not leave SQLite in a transaction."""
    path = _create_note(graph_api_client, "Position transaction race")
    kb = graph_api_client.app.state.mycelos.knowledge_base
    storage = graph_api_client.app.state.mycelos.storage
    original_execute = storage.execute

    def fail_with_real_integrity_error(sql: str, params: tuple = ()):
        if "INSERT INTO knowledge_graph_positions" in sql:
            return original_execute(
                """INSERT INTO knowledge_graph_positions
                       (user_id, note_path, x, y)
                       VALUES (NULL, 'notes/race', 1, 2)"""
            )
        return original_execute(sql, params)

    monkeypatch.setattr(storage, "execute", fail_with_real_integrity_error)

    assert kb.store_graph_position("default", path, 20, 40) is False
    assert storage._get_connection().in_transaction is False


def test_graph_position_and_note_delete_cannot_leave_an_orphan(
    graph_api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existence check and position insert must share one write transaction."""
    path = _create_note(graph_api_client, "Position delete race")
    kb = graph_api_client.app.state.mycelos.knowledge_base
    storage = graph_api_client.app.state.mycelos.storage
    original_get_meta = kb._indexer.get_note_meta
    checked = threading.Event()
    deletion_finished = threading.Event()
    intercepted = False

    def pause_after_check(note_path: str):
        nonlocal intercepted
        result = original_get_meta(note_path)
        if note_path == path and not intercepted:
            intercepted = True
            checked.set()
            deletion_finished.wait(timeout=0.5)
        return result

    def delete_note() -> None:
        assert checked.wait(timeout=5)
        storage.execute("DELETE FROM knowledge_notes WHERE path = ?", (path,))
        deletion_finished.set()

    monkeypatch.setattr(kb._indexer, "get_note_meta", pause_after_check)
    delete_thread = threading.Thread(target=delete_note)
    delete_thread.start()

    kb.store_graph_position("default", path, 20, 40)
    delete_thread.join(timeout=5)

    assert not delete_thread.is_alive()
    assert storage.fetchone(
        "SELECT note_path FROM knowledge_graph_positions WHERE note_path = ?",
        (path,),
    ) is None


def test_graph_position_rejects_invalid_json(graph_api_client: TestClient) -> None:
    """A malformed position request must return a client error, not a server error."""
    path = _create_note(graph_api_client)

    response = graph_api_client.put(
        f"/api/knowledge/graph/positions/{path}",
        content='{"x":',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert graph_api_client.get("/api/knowledge/graph").json()["positions"] == {}


def test_graph_position_follows_a_rename_and_disappears_on_delete(
    graph_api_client: TestClient,
) -> None:
    """A topic path change must retain its position and a deletion must remove it."""
    kb = graph_api_client.app.state.mycelos.knowledge_base
    topic = kb.create_topic("Old name")
    assert kb.store_graph_position("default", topic, 5, 10) is True

    new_path = kb.rename_topic(topic, "New name")
    assert kb.get_graph_positions("default") == {new_path: {"x": 5.0, "y": 10.0}}

    assert kb.delete_topic(new_path) is True
    assert kb.get_graph_positions("default") == {}


def test_existing_database_adds_the_position_table(tmp_path: Path) -> None:
    """Database initialization must restore the new table for an old database."""
    db_path = tmp_path / "mycelos.db"
    SQLiteStorage(db_path).initialize()

    old_database = SQLiteStorage(db_path)
    old_database.initialize()
    old_database.execute("DROP TABLE knowledge_graph_positions")
    old_database.execute(
        """CREATE TABLE knowledge_graph_positions (
               user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
               note_path TEXT NOT NULL,
               x REAL NOT NULL,
               y REAL NOT NULL,
               updated_at TEXT NOT NULL,
               PRIMARY KEY (user_id, note_path)
           )"""
    )
    old_database.close()

    restored = SQLiteStorage(db_path)
    restored.initialize()
    table = restored.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        ("knowledge_graph_positions",),
    )
    foreign_keys = restored.fetchall("PRAGMA foreign_key_list(knowledge_graph_positions)")
    restored.close()

    assert table == {"name": "knowledge_graph_positions"}
    assert foreign_keys == []
