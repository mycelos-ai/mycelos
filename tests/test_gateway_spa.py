"""Tests for SPA static file serving."""

from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mycelos.gateway.spa import SPAStaticFiles


def _make_spa_app(static_dir: Path):
    """Create a minimal FastAPI app with SPA serving."""
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    app.mount("/", SPAStaticFiles(directory=str(static_dir), html=True), name="spa")
    return app


class TestSPAStaticFiles:
    def test_serves_index_html(self, tmp_path):
        (tmp_path / "index.html").write_text("<h1>Mycelos</h1>")
        client = TestClient(_make_spa_app(tmp_path))
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Mycelos" in resp.text

    def test_serves_static_asset(self, tmp_path):
        (tmp_path / "index.html").write_text("<h1>Mycelos</h1>")
        sub = tmp_path / "_next" / "static"
        sub.mkdir(parents=True)
        (sub / "chunk.js").write_text("console.log('hi')")
        client = TestClient(_make_spa_app(tmp_path))
        resp = client.get("/_next/static/chunk.js")
        assert resp.status_code == 200
        assert "console.log" in resp.text

    def test_spa_fallback_for_unknown_routes(self, tmp_path):
        (tmp_path / "index.html").write_text("<h1>SPA</h1>")
        client = TestClient(_make_spa_app(tmp_path))
        resp = client.get("/chat/session/abc123")
        assert resp.status_code == 200
        assert "SPA" in resp.text

    def test_api_routes_not_intercepted(self, tmp_path):
        (tmp_path / "index.html").write_text("<h1>SPA</h1>")
        client = TestClient(_make_spa_app(tmp_path))
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
