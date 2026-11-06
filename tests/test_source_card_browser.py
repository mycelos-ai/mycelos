"""Real browser and local API; no provider requests or personal source data."""
import json
from pathlib import Path
import socket
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from huey import MemoryHuey
import uvicorn

from mycelos.app import App
from mycelos.gateway.routers.local_sources import router
from mycelos.scheduler.local_source_task import register_local_source_task


@pytest.fixture
def card_server(tmp_path):
    app = App(tmp_path / "data")
    app.storage.initialize()
    app.memory.set("default", "system", "knowledge_config", json.dumps({"embedding_provider": "none"}))
    queue = MemoryHuey()
    app.local_source_task = register_local_source_task(queue, app)
    root = tmp_path / "sharewise"
    root.mkdir()
    (root / "README.md").write_text("Sharewise imports portfolio CSV files. <script>bad()</script>")
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(router)

    @api.get("/api/models")
    def models():
        return {"models": []}

    @api.get("/api/models/settings")
    def model_settings():
        return {"providers": [{"id": "test", "configured": True}]}

    @api.get("/")
    def host():
        return HTMLResponse('''<!doctype html><html lang="de"><meta name="viewport" content="width=device-width">
        <script defer src="/shared/local-sources.js"></script>
        <body style="margin:16px;font-family:system-ui"><main style="max-width:700px;margin:auto">
        <h1>Chat</h1><mycelos-sources view="add" id="chat"></mycelos-sources>
        <h2>Einstellungen</h2><mycelos-sources id="settings"></mycelos-sources></main></body></html>''')

    api.mount("/shared", StaticFiles(directory=str(Path(__file__).resolve().parents[1] / "src/mycelos/frontend/shared")))
    api.mount("/pages", StaticFiles(directory=str(Path(__file__).resolve().parents[1] / "src/mycelos/frontend/pages")))
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
    yield f"http://127.0.0.1:{port}", root, app, queue
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()


def test_source_card_save_edit_and_background_import(page, card_server):
    from playwright.sync_api import expect
    url, root, app, queue = card_server
    page.goto(url)
    page.set_default_timeout(8000)
    chat = page.locator("#chat")
    chat.get_by_label("Ordnerpfad").fill(str(root))
    chat.get_by_role("button", name="Inhalte ansehen", exact=True).click()
    chat.locator(".result summary").first.click()
    expect(chat.get_by_text("Sharewise imports", exact=False)).to_be_visible()
    assert app.storage.fetchone("SELECT count(*) AS n FROM local_sources")["n"] == 0
    chat.get_by_label("Name", exact=True).fill("Sharewise")
    chat.get_by_label("Was möchtest du daraus erfahren?").fill("Entscheidungen zum Datenimport")
    chat.get_by_label("Verarbeitung", exact=True).select_option("direct")
    chat.get_by_role("button", name="Quelle speichern", exact=True).click()
    expect(chat.get_by_role("heading", name="Sharewise", exact=True)).to_be_visible()
    assert queue.pending_count() == 0
    settings = page.locator("#settings")
    settings.get_by_role("button", name="Sharewise", exact=False).click()
    settings.get_by_role("button", name="Bearbeiten", exact=True).click()
    settings.get_by_label("Was möchtest du daraus erfahren?").fill("Architektur und Datenimport")
    settings.get_by_role("button", name="Änderungen speichern").click()
    expect(chat.get_by_text("Architektur und Datenimport", exact=True)).to_be_visible()
    chat.get_by_role("button", name="Import starten", exact=True).click()
    expect(chat.get_by_text("Wartet auf Verarbeitung", exact=True)).to_be_visible()
    assert queue.pending_count() == 1
    queue.execute(queue.dequeue())
    expect(chat.get_by_text("Bereit", exact=True)).to_be_visible(timeout=10000)
    assert app.storage.fetchone("SELECT count(*) AS n FROM local_source_items")["n"] == 1
    chat.get_by_label("Weitere Aktionen", exact=True).click()
    chat.get_by_role("button", name="Pausieren", exact=True).click()
    expect(chat.get_by_text("Pausiert", exact=True)).to_be_visible()
    page.reload()
    expect(page.locator("#settings").get_by_role("button", name="Sharewise", exact=False)).to_be_visible()


def test_source_card_error_keeps_draft_and_fits_mobile(page, card_server):
    from playwright.sync_api import expect
    url, root, app, queue = card_server
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(url)
    page.set_default_timeout(8000)
    chat = page.locator("#chat")
    chat.get_by_label("Ordnerpfad").fill(str(root / "missing"))
    chat.get_by_role("button", name="Inhalte ansehen", exact=True).click()
    expect(chat.get_by_role("alert")).to_contain_text("Quellenordner")
    expect(chat.get_by_label("Ordnerpfad")).to_have_value(str(root / "missing"))
    chat.get_by_label("Ordnerpfad").fill(str(root))
    chat.get_by_role("button", name="Inhalte ansehen", exact=True).click()
    expect(chat.get_by_label("Name", exact=True)).to_be_visible()
    expect(chat.get_by_text("Kein Modell eingerichtet", exact=False)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert app.storage.fetchone("SELECT count(*) AS n FROM local_sources")["n"] == 0


def test_model_choice_enables_save_and_queue_error_keeps_source(page, card_server):
    from playwright.sync_api import expect
    url, root, app, queue = card_server
    page.route("**/api/models", lambda route: route.fulfill(json={"models": [{"id": "test-model", "name": "Lokales Testmodell", "provider": "test"}, {"id": "image-test", "provider": "test"}, {"id": "other-text", "provider": "absent"}, {"id": "disabled-text", "provider": "test", "enabled": False}]}))
    page.goto(url)
    page.set_default_timeout(8000)
    chat = page.locator("#chat")
    chat.get_by_label("Ordnerpfad").fill(str(root))
    chat.get_by_role("button", name="Inhalte ansehen", exact=True).click()
    expect(chat.get_by_role("button", name="Quelle speichern", exact=True)).to_be_disabled()
    expect(chat.get_by_label("Modell", exact=True).locator("option")).to_have_count(2)
    chat.get_by_label("Modell", exact=True).select_option("test-model")
    chat.get_by_role("button", name="Quelle speichern", exact=True).click()
    expect(chat.get_by_role("button", name="Import starten", exact=True)).to_be_enabled()
    app.local_source_task = None
    chat.get_by_role("button", name="Import starten", exact=True).click()
    expect(chat.get_by_role("alert")).to_contain_text("Hintergrundverarbeitung")
    assert app.storage.fetchone("SELECT count(*) AS n FROM local_sources")["n"] == 1
    assert queue.pending_count() == 0
    page.route("**/api/models", lambda route: route.fulfill(json={"models": []}))
    page.reload()
    settings = page.locator("#settings")
    settings.get_by_role("button", name="sharewise", exact=False).click()
    settings.get_by_role("button", name="Bearbeiten", exact=True).click()
    expect(settings.get_by_label("Modell", exact=True)).to_have_value("test-model")
    expect(settings.get_by_text("Das gespeicherte Modell ist nicht verfügbar.", exact=False)).to_be_visible()
    expect(settings.get_by_role("button", name="Änderungen speichern")).to_be_disabled()


def test_source_selection_hides_stale_actions_and_discards_old_response(page, card_server):
    from playwright.sync_api import expect
    from mycelos.knowledge.local_sources import LocalSourceService
    url, root, app, _ = card_server
    source = LocalSourceService(app).add("Sharewise", "folder", str(root), mode="direct")
    page.add_init_script('''const originalFetch = window.fetch;
      window.fetch = async (...args) => {
        const response = await originalFetch(...args);
        if (String(args[0]).match(/local-sources\\/[a-f0-9]+$/)) {
          await new Promise(resolve => window.releaseSource = resolve);
        }
        return response;
      };''')
    page.goto(url)
    settings = page.locator("#settings")
    settings.get_by_role("button", name="Sharewise", exact=False).click()
    page.wait_for_function("typeof window.releaseSource === 'function'")
    expect(settings.get_by_role("button", name="Quelle hinzufügen", exact=True)).to_have_count(0)
    expect(settings.get_by_text("Quellen laden …", exact=True)).to_be_visible()
    settings.evaluate("el => el.setAttribute('view', 'add')")
    expect(settings.get_by_label("Ordnerpfad")).to_be_visible()
    page.evaluate("window.releaseSource()")
    expect(settings.get_by_label("Ordnerpfad")).to_be_visible()
    settings.get_by_role("button", name="Schließen", exact=True).click()
    expect(settings.get_by_role("button", name="Sharewise", exact=False)).to_be_visible()


def test_source_rules_recheck_and_save_preserves_recipe(page, card_server):
    from playwright.sync_api import expect
    url, root, app, queue = card_server
    workflow = {
        "template_id": "folder", "version": 1,
        "description": "Liest die Texte im Ordner.",
        "include": ["**/*.md"], "exclude": ["private/**"],
        "reader": {"custom": "preserve-me"}, "project_documents_only": True,
    }
    page.set_default_timeout(5000)
    checks = []
    saves = []

    def inspect(route):
        body = route.request.post_data_json
        checks.append(body)
        route.fulfill(json={"units": 2, "files": 2, "samples": [{"title": "project", "path": "/project/README.md", "excerpt": "Read me"}, {"title": "project", "files": ["/project/DESIGN.md"], "excerpt": "Design"}], "truncated": True, "inventory_truncated": False,
                            "workflow": body.get("workflow", workflow), "analysis_performed": False})

    def source(route):
        if route.request.method == "POST":
            body = route.request.post_data_json
            saves.append(body)
            route.fulfill(json={**body, "id": "test-source", "status": "ready"})
        else:
            route.fulfill(json=[])

    page.route("**/api/local-sources", source)
    page.route("**/api/local-sources/inspect", inspect)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(url)
    chat = page.locator("#chat")
    chat.get_by_label("Quellenart").select_option("git")
    chat.get_by_label("Ordnerpfad").fill(str(root))
    chat.get_by_role("button", name="Inhalte ansehen", exact=True).click()
    expect(chat.get_by_text("Begrenzte Vorschau:", exact=False)).to_be_visible()
    expect(chat.get_by_text("Der Import prüft alle passenden Dateien.", exact=False)).to_be_visible()
    expect(chat.get_by_text("Die Zahlen zeigen nur", exact=False)).to_have_count(0)
    expect(chat.locator(".result summary")).to_have_text(["README.md", "DESIGN.md"])
    chat.get_by_text("Auswahl anpassen", exact=True).click()
    chat.get_by_label("In Projekten nur README, Architektur und Dokumentation auswählen").uncheck()
    expect(chat.get_by_text("Die Auswahl umfasst auch weitere", exact=False)).to_be_visible()
    chat.get_by_label("Einbeziehen", exact=True).fill("README.md\ndocs/**")
    expect(chat.locator(".result")).to_have_count(0)
    chat.get_by_role("button", name="Auswahl erneut prüfen").click()
    expect(chat.get_by_text("Auswahl geprüft.", exact=False)).to_be_visible()
    assert checks[-1]["workflow"]["include"] == ["README.md", "docs/**"]
    assert checks[-1]["workflow"]["reader"] == workflow["reader"]
    assert checks[-1]["workflow"]["project_documents_only"] is False
    chat.get_by_label("Verarbeitung", exact=True).select_option("direct")
    chat.get_by_role("button", name="Quelle speichern", exact=True).click()
    expect(chat.get_by_role("button", name="Import starten", exact=True)).to_be_visible()
    assert saves[-1]["workflow"] == checks[-1]["workflow"]
    chat.get_by_role("button", name="Bearbeiten", exact=True).click()
    chat.get_by_text("Auswahl anpassen", exact=True).click()
    expect(chat.get_by_label("Einbeziehen", exact=True)).to_have_value("README.md\ndocs/**")
    expect(chat.get_by_label("In Projekten nur README, Architektur und Dokumentation auswählen")).not_to_be_checked()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert queue.pending_count() == 0
