"""API tests for persisted knowledge graph node positions."""
from __future__ import annotations

import os
import tempfile
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
