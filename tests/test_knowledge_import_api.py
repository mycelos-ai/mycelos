from __future__ import annotations

import io
import os
import tempfile
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-import-api"

        from mycelos.app import App
        from mycelos.setup import web_init
        from mycelos.gateway.server import create_app

        app_obj = App(data_dir)
        app_obj.initialize()
        web_init(app_obj, api_key="sk-ant-api03-FAKETESTKEYFORIMP")

        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0")
        client = TestClient(fastapi_app)
        app_obj_from_state = fastapi_app.state.mycelos
        yield client, app_obj_from_state


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for relpath, body in entries.items():
            zf.writestr(relpath, body)
    return buf.getvalue()


def test_import_auto_preserve_three_folders(api_client) -> None:
    client, _ = api_client
    zip_data = _zip_bytes({
        "journal/a.md": "# A\nbody",
        "projects/b.md": "# B\nbody",
        "recipes/c.md": "# C\nbody",
    })
    resp = client.post(
        "/api/knowledge/import",
        data={"mode": "auto"},
        files={"file": ("vault.zip", zip_data, "application/zip")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "preserve"
    assert len(data["created"]) == 3


def test_import_auto_flat_is_suggest(api_client, monkeypatch) -> None:
    client, app_obj = api_client
    if getattr(app_obj, "knowledge_organizer", None) is not None:
        monkeypatch.setattr(
            app_obj.knowledge_organizer,
            "run",
            lambda user_id="default": {"processed": 0, "archived": 0,
                                       "moved": 0, "suggested": 0, "linked": 0},
        )
    zip_data = _zip_bytes({f"note_{i}.md": f"# {i}\nbody" for i in range(5)})
    resp = client.post(
        "/api/knowledge/import",
        data={"mode": "auto"},
        files={"file": ("flat.zip", zip_data, "application/zip")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "suggest"
    assert len(data["created"]) == 5


def test_import_forced_preserve_overrides_detection(api_client) -> None:
    client, _ = api_client
    zip_data = _zip_bytes({"a.md": "# A\nbody", "b.md": "# B\nbody"})
    resp = client.post(
        "/api/knowledge/import",
        data={"mode": "preserve"},
        files={"file": ("flat.zip", zip_data, "application/zip")},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "preserve"


def test_import_bad_zip_returns_422(api_client) -> None:
    client, _ = api_client
    resp = client.post(
        "/api/knowledge/import",
        data={"mode": "auto"},
        files={"file": ("broken.zip", b"not a zip", "application/zip")},
    )
    assert resp.status_code == 422


def test_import_missing_file_returns_422(api_client) -> None:
    client, _ = api_client
    resp = client.post("/api/knowledge/import", data={"mode": "auto"})
    assert resp.status_code == 422
