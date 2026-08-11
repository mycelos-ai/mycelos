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
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-export-api"

        from mycelos.app import App
        from mycelos.setup import web_init
        from mycelos.gateway.server import create_app

        app_obj = App(data_dir)
        app_obj.initialize()
        web_init(app_obj, api_key="sk-ant-api03-FAKETESTKEYFOREXP")

        fastapi_app = create_app(
            data_dir, no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True
        )
        client = TestClient(fastapi_app)
        yield client, fastapi_app.state.mycelos


def test_export_okf_returns_zip(api_client) -> None:
    client, mycelos = api_client
    mycelos.knowledge_base.write(title="Coffee", content="Dark roast.", type="note")

    resp = client.get("/api/knowledge/export?format=okf")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/zip")
    assert "attachment" in resp.headers.get("content-disposition", "")

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert "index.md" in names
    assert any(n.endswith("coffee.md") for n in names)


def test_export_bad_format_returns_422(api_client) -> None:
    client, _ = api_client
    resp = client.get("/api/knowledge/export?format=json")
    assert resp.status_code == 422


def test_export_defaults_to_okf(api_client) -> None:
    client, _ = api_client
    resp = client.get("/api/knowledge/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/zip")


def test_export_excludes_archived(api_client) -> None:
    client, mycelos = api_client
    kb = mycelos.knowledge_base
    kb.write(title="Keep me", content="body", type="note")
    archived = kb.write(title="Hide me", content="body", type="note")
    kb.archive_note(archived)

    resp = client.get("/api/knowledge/export?format=okf")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    blob = "\n".join(zf.read(n).decode("utf-8") for n in zf.namelist())
    assert "Keep me" in blob
    assert "Hide me" not in blob
