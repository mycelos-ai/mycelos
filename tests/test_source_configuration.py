import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mycelos.app import App
from mycelos.gateway.routers.local_sources import router
from mycelos.knowledge.local_sources import LocalSourceService


@pytest.fixture
def source_env(tmp_path):
    app = App(tmp_path / "data")
    app.storage.initialize()
    root = tmp_path / "docs"
    root.mkdir()
    (root / "README.md").write_text("Sharewise imports portfolio files.")
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(router)
    return app, root, TestClient(api)


def test_inspect_before_save_leaves_no_source_or_knowledge(source_env):
    app, root, client = source_env
    response = client.post("/api/local-sources/inspect", json={"kind": "folder", "root": str(root)})
    assert response.status_code == 200, response.text
    assert response.json()["units"] == 1
    assert "Sharewise" in response.json()["samples"][0]["excerpt"]
    assert LocalSourceService(app).list() == []
    assert app.storage.fetchone("SELECT count(*) AS n FROM knowledge_notes")["n"] == 0


def test_inspect_requires_explicit_nonempty_folder(source_env):
    _, _, client = source_env
    for root in ("", "   "):
        with pytest.raises(ValueError, match="Quellenordner"):
            LocalSourceService._validate_root("folder", root)
        assert client.post("/api/local-sources/inspect", json={"kind": "folder", "root": root}).status_code == 422


def test_edit_source_persists_and_cannot_change_active_import(source_env):
    app, root, client = source_env
    svc = LocalSourceService(app)
    source = svc.add("Docs", "folder", str(root), mode="direct")
    url = "/api/local-sources/" + source["id"]
    patch = {"name": "Sharewise", "purpose": "Architecture", "mode": "analyze", "model": "local-model"}
    response = client.put(url, json=patch)
    assert response.status_code == 200, response.text
    assert client.get(url).json()["purpose"] == "Architecture"
    assert svc.get(source["id"])["model"] == "local-model"
    for state in ("running", "queued"):
        app.storage.execute("UPDATE local_sources SET status=? WHERE id=?", (state, source["id"]))
        assert client.put(url, json={**patch, "name": "Changed"}).status_code == 422
        assert svc.get(source["id"])["name"] == "Sharewise"


def test_edit_checks_owner_and_required_model(source_env):
    app, root, client = source_env
    source = LocalSourceService(app).add("Docs", "folder", str(root), mode="direct")
    with pytest.raises(ValueError, match="nicht gefunden"):
        LocalSourceService(app, "other").update(source["id"], name="Other", purpose="", mode="direct")
    response = client.put("/api/local-sources/" + source["id"], json={
        "name": "Docs", "purpose": "", "mode": "analyze", "model": None})
    assert response.status_code == 422
    assert LocalSourceService(app).get(source["id"])["mode"] == "direct"


def test_edit_respects_import_lock_before_running_status(source_env):
    from mycelos.knowledge.source_lock import source_lock
    app, root, _ = source_env
    svc = LocalSourceService(app)
    source = svc.add("Docs", "folder", str(root), mode="direct")
    with source_lock(app.data_dir, source["id"]):
        with pytest.raises(ValueError):
            svc.update(source["id"], name="Changed", purpose="", mode="direct")
    assert svc.get(source["id"])["name"] == "Docs"


def test_tool_can_open_card_without_import(source_env):
    from mycelos.tools.local_sources import execute
    app, root, _ = source_env
    context = {"app": app, "_pending_events": []}
    result = execute({"action": "configure"}, context)
    assert result["status"] == "form_opened"
    event = context["_pending_events"][0]
    assert event.type == "widget"
    assert event.data["widget"] == {"type": "local_sources", "view": "add"}
    assert LocalSourceService(app).list() == []
