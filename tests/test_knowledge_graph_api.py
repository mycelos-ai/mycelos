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
        yield TestClient(fastapi_app)


def _create_note(client: TestClient, title: str = "Graph node") -> str:
    response = client.post("/api/knowledge/notes", json={"title": title})
    assert response.status_code == 200, response.text
    return response.json()["path"]


def test_graph_position_write_is_read_for_the_current_user(
    graph_api_client: TestClient,
) -> None:
    """A moved node must return at its saved position to its owner."""
    path = _create_note(graph_api_client)

    response = graph_api_client.put(
        f"/api/knowledge/graph/positions/{path}",
        json={"x": 120.5, "y": -42},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"path": path, "x": 120.5, "y": -42.0}

    graph = graph_api_client.get("/api/knowledge/graph")
    assert graph.status_code == 200, graph.text
    assert graph.json()["positions"] == {path: {"x": 120.5, "y": -42.0}}

    other_user = graph_api_client.get(
        "/api/knowledge/graph", headers={"X-User-Id": "alice"}
    )
    assert other_user.status_code == 200, other_user.text
    assert other_user.json()["positions"] == {}


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

    connection = SQLiteStorage(db_path)
    connection.initialize()
    connection.execute("DROP TABLE knowledge_graph_positions")
    connection.close()

    restored = SQLiteStorage(db_path)
    restored.initialize()
    row = restored.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        ("knowledge_graph_positions",),
    )
    restored.close()

    assert row == {"name": "knowledge_graph_positions"}
