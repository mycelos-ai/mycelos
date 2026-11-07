from click.testing import CliRunner
from mycelos.cli.main import cli


def test_sources_tool_uses_shared_service(tmp_path):
    from mycelos.app import App
    from mycelos.tools.registry import ToolRegistry
    app = App(tmp_path)
    app.storage.initialize()
    root = tmp_path / "documents"
    root.mkdir()
    result = ToolRegistry.execute("local_sources", {
        "action": "add", "name": "Docs", "kind": "folder", "root": str(root), "mode": "direct"
    }, {"app": app, "user_id": "default", "agent_id": "mycelos"})
    assert result["kind"] == "folder"
    listed = ToolRegistry.execute("local_sources", {"action": "list"},
                                 {"app": app, "user_id": "default", "agent_id": "mycelos"})
    assert listed[0]["id"] == result["id"]


def test_source_http_actions(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mycelos.app import App
    from mycelos.gateway.routers.local_sources import router
    app = App(tmp_path)
    app.storage.initialize()
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(router)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "a.md").write_text("Project evidence")
    with TestClient(api) as client:
        response = client.post("/api/local-sources", json={"name": "Docs", "kind": "folder",
                                                        "root": str(root), "mode": "direct"})
        assert response.status_code == 200, response.text
        source_id = response.json()["id"]
        assert client.get(f"/api/local-sources/{source_id}/preview").json()["units"] == 1
        # No fake success when the background worker is unavailable.
        assert client.post(f"/api/local-sources/{source_id}/import").status_code == 503


def test_source_cli_preview_import_and_search(tmp_path):
    import json
    root = tmp_path / "docs"
    root.mkdir()
    (root / "README.md").write_text("Sharewise imports portfolio CSV files.")
    args = ["source", "--data-dir", str(tmp_path / "data")]
    runner = CliRunner()
    added = runner.invoke(cli, args + ["add", "--name", "Projects", "--kind", "folder",
                                      "--path", str(root), "--mode", "direct"])
    assert added.exit_code == 0, added.output
    source_id = json.loads(added.output)["id"]
    preview = runner.invoke(cli, args + ["preview", source_id])
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.output)["units"] == 1
    imported = runner.invoke(cli, args + ["import", source_id])
    assert imported.exit_code == 0, imported.output
    result = runner.invoke(cli, args + ["search", "portfolio"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["origins"][0]["source_id"] == source_id
