"""API tests for /api/inbox/* — the inbox and the placement review view."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mycelos.knowledge.inbox import InboxService


@pytest.fixture
def api_client():
    """Mirrors the fixture in tests/test_organizer_api.py."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-inbox-api"

        from mycelos.app import App
        from mycelos.setup import web_init
        from mycelos.gateway.server import create_app

        app_obj = App(data_dir)
        app_obj.initialize()
        web_init(app_obj, api_key="sk-ant-api03-FAKETESTKEYFORINBOX")

        fastapi_app = create_app(
            data_dir, no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True
        )
        client = TestClient(fastapi_app)
        yield client, fastapi_app.state.mycelos


# ---- GET /api/inbox -----------------------------------------------------


def test_inbox_lists_only_entries_needing_a_human(api_client) -> None:
    client, app_obj = api_client
    path = app_obj.knowledge_base.write(title="X", content="y", topic="notes")
    inbox = InboxService(app_obj.storage)
    # An optimization row in the legacy shape: it must stay hidden.
    inbox.add(path, "move",
              {"target": "topics/a", "alternatives": ["topics/b"],
               "reason": "low_confidence"}, 0.6)
    inbox.add(path, "merge", {"duplicate_path": "notes/z"}, 0.95)

    resp = client.get("/api/inbox")
    assert resp.status_code == 200
    kinds = [e["kind"] for e in resp.json()["entries"]]
    assert "merge" in kinds
    assert "move" not in kinds


def test_inbox_entry_carries_the_suggestion_id(api_client) -> None:
    """Resolving goes through the existing accept/dismiss routes, so the
    entry must name the suggestion those routes take."""
    client, app_obj = api_client
    path = app_obj.knowledge_base.write(title="X", content="y", topic="notes")
    sid = InboxService(app_obj.storage).add(
        path, "merge", {"duplicate_path": "notes/z"}, 0.95)

    entry = next(e for e in client.get("/api/inbox").json()["entries"]
                 if e["kind"] == "merge")
    assert entry["id"] == f"suggestion:{sid}"
    assert "accept" in [a["id"] for a in entry["actions"]]


def test_count_matches_the_listed_entries(api_client) -> None:
    client, app_obj = api_client
    path = app_obj.knowledge_base.write(title="X", content="y", topic="notes")
    InboxService(app_obj.storage).add(
        path, "merge", {"duplicate_path": "notes/z"}, 0.95)

    entries = client.get("/api/inbox").json()["entries"]
    count = client.get("/api/inbox/count").json()
    assert count["count"] == len(entries)
    assert count["count"] == 1


def test_count_is_zero_on_an_empty_inbox(api_client) -> None:
    client, _ = api_client
    assert client.get("/api/inbox/count").json() == {"count": 0}
    assert client.get("/api/inbox").json()["entries"] == []


def test_uncertain_placements_are_not_in_the_inbox_or_the_count(api_client) -> None:
    """The count must mean 'things that need you' — nothing else."""
    client, app_obj = api_client
    path = app_obj.knowledge_base.write(title="Shaky", content="y", topic="notes")
    app_obj.storage.execute(
        "UPDATE knowledge_notes SET placement_confidence=0.5 WHERE path=?", (path,))

    assert client.get("/api/inbox").json()["entries"] == []
    assert client.get("/api/inbox/count").json()["count"] == 0


# ---- GET /api/inbox/placements ------------------------------------------


def test_placements_view_lists_uncertain_notes(api_client) -> None:
    client, app_obj = api_client
    path = app_obj.knowledge_base.write(title="Shaky", content="y", topic="notes")
    app_obj.storage.execute(
        "UPDATE knowledge_notes SET placement_confidence=0.5 WHERE path=?", (path,))

    resp = client.get("/api/inbox/placements")
    assert resp.status_code == 200
    assert any(p["path"] == path for p in resp.json()["placements"])


def test_placements_are_shakiest_first_and_honour_the_limit(api_client) -> None:
    client, app_obj = api_client
    kb = app_obj.knowledge_base
    a = kb.write(title="A", content="x", topic="notes")
    b = kb.write(title="B", content="x", topic="notes")
    app_obj.storage.execute(
        "UPDATE knowledge_notes SET placement_confidence=0.7 WHERE path=?", (a,))
    app_obj.storage.execute(
        "UPDATE knowledge_notes SET placement_confidence=0.4 WHERE path=?", (b,))

    got = client.get("/api/inbox/placements").json()["placements"]
    assert [p["path"] for p in got] == [b, a]

    limited = client.get("/api/inbox/placements?limit=1").json()["placements"]
    assert [p["path"] for p in limited] == [b]


def test_placements_limit_is_clamped_not_trusted(api_client) -> None:
    """A hostile limit must not become an unbounded query or a crash."""
    client, _ = api_client
    assert client.get("/api/inbox/placements?limit=-5").status_code == 200
    assert client.get("/api/inbox/placements?limit=999999").status_code == 200
    assert client.get("/api/inbox/placements?limit=abc").status_code == 422


# ---- POST /api/inbox/placements/{path}/confirm --------------------------


def test_confirming_a_placement_clears_the_marker(api_client) -> None:
    """Confirming means 'this is right' — it leaves the review list."""
    client, app_obj = api_client
    path = app_obj.knowledge_base.write(title="Shaky", content="y", topic="notes")
    app_obj.storage.execute(
        "UPDATE knowledge_notes SET placement_confidence=0.5 WHERE path=?", (path,))

    resp = client.post(f"/api/inbox/placements/{path}/confirm")
    assert resp.status_code == 200
    row = app_obj.storage.fetchone(
        "SELECT placement_confidence FROM knowledge_notes WHERE path=?", (path,))
    assert row["placement_confidence"] is None
    assert client.get("/api/inbox/placements").json()["placements"] == []


def test_confirming_a_nested_path_resolves(api_client) -> None:
    """The route is {path:path}: a note path has slashes in it."""
    client, app_obj = api_client
    app_obj.storage.execute(
        "INSERT INTO knowledge_notes (path, title, type, placement_confidence) "
        "VALUES ('notes/a/b', 'Nested', 'note', 0.45)")

    resp = client.post("/api/inbox/placements/notes/a/b/confirm")
    assert resp.status_code == 200
    assert resp.json()["path"] == "notes/a/b"
    row = app_obj.storage.fetchone(
        "SELECT placement_confidence FROM knowledge_notes WHERE path=?", ("notes/a/b",))
    assert row["placement_confidence"] is None


def test_confirming_a_note_without_a_marker_is_still_200(api_client) -> None:
    """Pinned choice: confirming an already-certain note is idempotent,
    not an error. The note exists and the post-state is what was asked
    for; a 404 here would make a double click look like a failure."""
    client, app_obj = api_client
    path = app_obj.knowledge_base.write(title="Certain", content="y", topic="notes")

    resp = client.post(f"/api/inbox/placements/{path}/confirm")
    assert resp.status_code == 200
    row = app_obj.storage.fetchone(
        "SELECT placement_confidence FROM knowledge_notes WHERE path=?", (path,))
    assert row["placement_confidence"] is None


def test_confirming_an_unknown_path_is_404(api_client) -> None:
    client, _ = api_client
    assert client.post("/api/inbox/placements/notes/nope/confirm").status_code == 404


def test_confirming_audits_the_path(api_client) -> None:
    client, app_obj = api_client
    path = app_obj.knowledge_base.write(title="Shaky", content="y", topic="notes")
    app_obj.storage.execute(
        "UPDATE knowledge_notes SET placement_confidence=0.5 WHERE path=?", (path,))

    client.post(f"/api/inbox/placements/{path}/confirm")
    row = app_obj.storage.fetchone(
        "SELECT details FROM audit_events "
        "WHERE event_type='knowledge.placement_confirmed' ORDER BY id DESC LIMIT 1")
    assert row is not None
    assert path in row["details"]
    assert "Shaky" not in row["details"]      # Rule 1: no note content


# ---- Path traversal on the {path:path} route ----------------------------


# Percent-encoded traversal is the case that matters. A literal
# "../.." is removed by any RFC 3986 client before the request is sent,
# so it never reaches the handler; "%2e%2e%2f" survives decoding and
# arrives as "../../etc/passwd" in the path parameter. Verified against
# a bare FastAPI app: the encoded forms hit the handler with the dots
# intact. Both families are pinned so a future router change that stops
# normalizing cannot open the hole quietly.
@pytest.mark.parametrize("attempt", [
    "..%2f..%2fetc%2fpasswd",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "notes/..%2F..%2Fsecrets",
    "notes%2f..%2f..%2fx",
    "..%5c..%5cwindows",
])
def test_confirm_rejects_encoded_path_traversal(api_client, attempt: str) -> None:
    """These reach the handler with the traversal intact. It must refuse."""
    client, _ = api_client
    resp = client.post(f"/api/inbox/placements/{attempt}/confirm")
    assert resp.status_code == 400
    assert resp.json() == {"error": "invalid path"}


@pytest.mark.parametrize("attempt", [
    "../../etc/passwd",
    "notes/../../../etc/passwd",
    "./../../etc/passwd",
])
def test_confirm_rejects_literal_path_traversal(api_client, attempt: str) -> None:
    """Normalized away by the client, so it never matches this route."""
    client, _ = api_client
    resp = client.post(f"/api/inbox/placements/{attempt}/confirm")
    assert resp.status_code in (404, 405)
    assert "root:" not in resp.text          # never a file's contents


def test_confirm_traversal_changes_no_row(api_client) -> None:
    """Fail closed: a rejected path must not clear anyone's marker."""
    client, app_obj = api_client
    path = app_obj.knowledge_base.write(title="Shaky", content="y", topic="notes")
    app_obj.storage.execute(
        "UPDATE knowledge_notes SET placement_confidence=0.5 WHERE path=?", (path,))

    client.post("/api/inbox/placements/..%2f..%2fetc%2fpasswd/confirm")
    client.post("/api/inbox/placements/notes%2f..%2f..%2fsecrets/confirm")
    client.post("/api/inbox/placements/../../etc/passwd/confirm")

    row = app_obj.storage.fetchone(
        "SELECT placement_confidence FROM knowledge_notes WHERE path=?", (path,))
    assert row["placement_confidence"] == 0.5


def test_confirm_rejects_an_absolute_path(api_client) -> None:
    """A leading slash survives routing: '//etc/passwd' arrives as
    '/etc/passwd' in the path parameter."""
    client, _ = api_client
    resp = client.post("/api/inbox/placements//etc/passwd/confirm")
    assert resp.status_code == 400
    assert resp.json() == {"error": "invalid path"}
