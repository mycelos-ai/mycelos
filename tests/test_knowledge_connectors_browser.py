"""MCP settings use the HTTP contract without remote tool calls."""
import json
from pathlib import Path

from playwright.sync_api import expect

SCRIPT = Path(__file__).resolve().parents[1] / "src/mycelos/frontend/shared/knowledge-connectors.js"


def test_connections_add_and_lazy_tool_list(page):
    page.set_default_timeout(5000)
    calls = []
    saved = []
    connectors = [{"id": "docs", "name": "Dokumente <script>", "connector_type": "mcp",
                   "operational_state": "healthy", "tool_count": 1},
                  {"id": "telegram", "name": "Telegram", "connector_type": "channel"}]

    def route(request):
        path = request.request.url.split("example.test")[-1]
        calls.append(path)
        if path == "/api/connectors" and request.request.method == "POST":
            saved.append(request.request.post_data_json)
            connectors.append({"id": "brave-search", "name": "Brave Search", "connector_type": "mcp",
                               "operational_state": "unknown", "tool_count": 0})
            data = {"status": "registered", "connector": "brave-search"}
        elif path == "/api/connectors":
            data = connectors
        elif path == "/api/connectors/recipes":
            data = {"channels": [], "mcp": [{"id": "brave-search", "name": "Brave Search", "setup_flow": "secret",
                    "credentials": [{"env_var": "BRAVE_API_KEY", "name": "API Key"}]}]}
        elif path == "/api/connectors/docs/tools":
            data = {"tools": [{"name": "search", "description": "Dokumente suchen", "blocked": False}]}
        else:
            raise AssertionError(path)
        request.fulfill(json=data)

    page.route("https://example.test/api/**", route)
    page.route("https://example.test/", lambda r: r.fulfill(content_type="text/html", body='<html lang="de"><mycelos-connectors></mycelos-connectors></html>'))
    page.goto("https://example.test/")
    page.add_script_tag(path=str(SCRIPT))
    card = page.locator("mycelos-connectors")
    expect(card.get_by_text("Dokumente <script>", exact=True)).to_be_visible()
    expect(card.get_by_text("Telegram", exact=True)).to_have_count(0)
    assert "/api/connectors/docs/tools" not in calls
    card.get_by_text("Werkzeuge anzeigen", exact=True).click()
    expect(card.get_by_text("Dokumente suchen", exact=True)).to_be_visible()
    card.get_by_role("button", name="Verbindung hinzufügen", exact=True).click()
    card.get_by_label("Dienst", exact=True).select_option("brave-search")
    card.get_by_label("Zugangsschlüssel", exact=False).fill("test-secret")
    card.get_by_role("button", name="Verbindung speichern", exact=True).click()
    expect(card.get_by_text("Brave Search", exact=True)).to_be_visible()
    assert saved == [{"name": "brave-search", "env_vars": {"BRAVE_API_KEY": "test-secret"}}]
    assert not any("/call" in path or "ingest" in path for path in calls)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
