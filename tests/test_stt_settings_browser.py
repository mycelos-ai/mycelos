"""Real browser and local API: speech input through the custom provider."""
from pathlib import Path
import socket
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from mycelos.app import App
from mycelos.gateway.routers.models import router


@pytest.fixture
def settings_server(tmp_path, monkeypatch):
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "stt-browser")
    monkeypatch.setenv("LITELLM_HOST", "http://proxy:4000")
    monkeypatch.setenv("LITELLM_SECRET", "env-secret-value")
    monkeypatch.delenv("MYCELOS_STT_PROVIDER", raising=False)
    app = App(tmp_path / "data")
    app.storage.initialize()
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(router)

    @api.get("/")
    def host():
        return HTMLResponse('''<!doctype html><html lang="de"><meta name="viewport" content="width=device-width">
        <script defer src="/shared/model-settings.js"></script>
        <body style="margin:16px"><main style="max-width:700px;margin:auto"><mycelos-models></mycelos-models></main></body></html>''')

    api.mount("/shared", StaticFiles(directory=str(Path(__file__).resolve().parents[1] / "src/mycelos/frontend/shared")))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(api, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.02)
    assert server.started
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()


def test_speech_input_can_use_the_custom_proxy(page, settings_server):
    from playwright.sync_api import expect
    url, app = settings_server
    page.goto(url)
    page.set_default_timeout(8000)
    settings = page.locator("mycelos-models")
    # LITELLM_HOST + LITELLM_SECRET alone make the custom provider usable.
    expect(settings.get_by_text("http://proxy:4000/v1")).to_be_visible()
    expect(settings.get_by_text("Standard (openai)")).to_be_visible()
    settings.get_by_role("button", name="Spracheingabe ändern").click()
    settings.get_by_label("Spracherkennung").select_option("custom")
    settings.get_by_label("Modellname im Proxy (Alias)").fill("whisper-large")
    settings.get_by_role("button", name="Speichern").click()
    expect(settings.get_by_text("Spracheingabe gespeichert.")).to_be_visible()
    expect(settings.get_by_text("Eigener Server · whisper-large")).to_be_visible()
    assert app.memory.get("default", "system", "stt_provider") == "custom"
    assert app.memory.get("default", "system", "stt_model") == "whisper-large"
    assert "env-secret-value" not in page.content()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
