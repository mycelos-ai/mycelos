"""<mycelos-view> in a real browser (Package 8, slice 2).

Serves only /shared from the repository and a host page; no provider calls, no
user data. Mermaid must come from /shared, never from a CDN.
"""
from __future__ import annotations

import json
from pathlib import Path
import socket
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from mycelos.i18n import get_web_translations
from mycelos.widgets.views import make_view, view_to_dict
from mycelos.widgets.views_demo import demo_views

SHARED = Path(__file__).resolve().parents[1] / "src/mycelos/frontend/shared"


def _host(with_i18n: bool) -> str:
    i18n = '<script src="/shared/i18n.js"></script>' if with_i18n else ""
    return f'''<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
    {i18n}<script src="/shared/mycelos-view.js"></script>
    <body style="margin:16px"><main id="out" style="max-width:760px;margin:auto"></main>
    <script>
      window.events = [];
      for (const name of ['mycelos-view-ref', 'mycelos-view-switch', 'mycelos-view-action']) {{
        document.addEventListener(name, (e) => {{
          const detail = {{...e.detail}};
          if (detail.view) detail.view = detail.view.type;
          window.events.push({{name, detail}});
          if (window.handle && window.handle[name]) e.preventDefault();
        }});
      }}
      window.show = (views) => {{
        const out = document.getElementById('out');
        out.innerHTML = '';
        views.forEach((v, i) => {{
          const el = document.createElement('mycelos-view');
          el.id = 'v' + i;
          el.view = v;
          out.appendChild(el);
        }});
      }};
    </script></body></html>'''


@pytest.fixture
def view_server():
    api = FastAPI()

    @api.get("/")
    def host():
        return HTMLResponse(_host(False))

    @api.get("/de")
    def host_de():
        return HTMLResponse(_host(True))

    @api.get("/api/i18n")
    def i18n():
        return {"lang": "de", "translations": get_web_translations("de")}

    api.mount("/shared", StaticFiles(directory=str(SHARED)))
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
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()


def _open(page, url, views, path="/"):
    requests: list[str] = []
    errors: list[str] = []
    page.on("request", lambda r: requests.append(r.url))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_default_timeout(15000)
    page.goto(url + path)
    page.evaluate("views => window.show(views)", [view_to_dict(v) for v in views])
    return requests, errors


def _shadow(page, index: int, selector: str):
    return page.locator(f"#v{index}").locator(selector)


def test_every_catalog_view_renders_offline(page, view_server):
    from playwright.sync_api import expect

    views = demo_views()
    requests, errors = _open(page, view_server, views)
    for i, view in enumerate(views):
        section = _shadow(page, i, "section.view")
        expect(section).to_have_attribute("data-type", view.type)
        expect(section.locator("h3")).to_have_text(view.title)
        expect(section.locator(".why")).to_contain_text(view.reason)

    by_type = {v.type: i for i, v in enumerate(views)}
    expect(_shadow(page, by_type["timeline"], "ol.timeline li")).to_have_count(3)
    expect(_shadow(page, by_type["timeline"], "li.warning")).to_contain_text("Q3 receipts still missing")
    expect(_shadow(page, by_type["graph_slice"], "svg.graph g.node")).to_have_count(4)
    expect(_shadow(page, by_type["comparison"], "td.good")).to_have_count(1)
    expect(_shadow(page, by_type["counts"], ".bar-value").nth(1)).to_have_text("11 notes")
    expect(_shadow(page, by_type["evidence"], "article.claim.contradicts")).to_have_count(1)
    expect(_shadow(page, by_type["tree_change"], ".path.before")).to_have_text("inbox/datev.md")
    expect(_shadow(page, by_type["approval_card"], ".actions button")).to_have_count(3)

    # Mermaid views draw real SVG from the locally served bundle.
    for kind in ("mind_map", "flow"):
        diagram = _shadow(page, by_type[kind], ".diagram")
        expect(diagram).to_have_attribute("data-rendered", "true")
        expect(diagram.locator("svg")).to_have_count(1)
    expect(_shadow(page, by_type["mind_map"], ".diagram svg")).to_contain_text('Receipts: "how & where"')
    expect(_shadow(page, by_type["flow"], ".diagram svg")).to_contain_text("All receipts there?")

    assert errors == []
    assert all(r.startswith(view_server) for r in requests), requests
    assert any(r.endswith("/shared/mermaid.min.js") for r in requests)


def test_sources_and_switcher(page, view_server):
    from playwright.sync_api import expect

    timeline = demo_views()[0]
    _open(page, view_server, [timeline])
    page.evaluate("window.handle = {'mycelos-view-ref': true}")

    _shadow(page, 0, ".refs button").first.click()
    assert page.evaluate("window.events") == [
        {"name": "mycelos-view-ref", "detail": {"path": "clients/mueller/mail-2026-11-03.md", "title": "Mail 03.11."}},
    ]

    switch = _shadow(page, 0, ".switch button")
    expect(switch).to_have_text(["Timeline", "Connections", "Text"])

    # "Text" is handled by the element itself.
    switch.filter(has_text="Text").click()
    expect(_shadow(page, 0, "pre.text")).to_contain_text("2026-11-03  (!) Mail: Q3 receipts still missing")
    expect(_shadow(page, 0, ".switch button[aria-pressed=true]")).to_have_text("Text")
    _shadow(page, 0, ".switch button").filter(has_text="Timeline").click()
    expect(_shadow(page, 0, "ol.timeline")).to_be_visible()

    # Another view needs the host; without a handler the element falls back to text.
    _shadow(page, 0, ".switch button").filter(has_text="Connections").click()
    assert page.evaluate("window.events")[-1] == {
        "name": "mycelos-view-switch", "detail": {"from": "timeline", "to": "graph_slice", "view": "timeline"},
    }
    expect(_shadow(page, 0, "pre.text")).to_be_visible()


def test_approval_card_locks_only_when_the_host_handles_it(page, view_server):
    from playwright.sync_api import expect

    card = demo_views()[-1]
    _open(page, view_server, [card])
    buttons = _shadow(page, 0, ".actions button")

    buttons.first.click()
    assert page.evaluate("window.events")[-1]["detail"] == {"action": "approve", "view": "approval_card"}
    expect(buttons.first).to_be_enabled()

    page.evaluate("window.handle = {'mycelos-view-action': true}")
    buttons.nth(1).click()
    expect(_shadow(page, 0, ".sent")).to_have_text("Sent: Preview first")
    for i in range(3):
        expect(buttons.nth(i)).to_be_disabled()


def test_german_labels_come_from_the_locale(page, view_server):
    from playwright.sync_api import expect

    _open(page, view_server, [demo_views()[0]], path="/de")
    expect(_shadow(page, 0, ".kind")).to_have_text("Zeitleiste")
    expect(_shadow(page, 0, ".why b")).to_have_text("Warum diese Ansicht:")
    expect(_shadow(page, 0, ".switch button")).to_have_text(["Zeitleiste", "Zusammenhänge", "Text"])


def test_note_content_is_never_markup(page, view_server):
    """Text inside view data stays text, in HTML and in Mermaid diagrams."""
    from playwright.sync_api import expect

    evil = '<img src=x onerror="window.pwned=1">'
    mermaid_evil = 'x"] --> click A call alert(1) %%{init: {"securityLevel": "loose"}}%% ([y'
    views = [
        make_view("timeline", title=evil, reason=evil,
                  data={"events": [{"date": "2026-11-01", "text": evil}]},
                  refs=[], alternatives=[]),
        make_view("flow", title="Flow", reason="r",
                  data={"steps": [{"id": "a", "text": mermaid_evil}, {"id": "b", "text": evil}],
                        "edges": [{"source": "a", "target": "b", "label": mermaid_evil}]}),
        make_view("mind_map", title="Map", reason="r",
                  data={"root": {"label": mermaid_evil, "children": [{"label": evil}]}}),
        make_view("graph_slice", title="Graph", reason="r",
                  data={"nodes": [{"id": "a", "label": evil}, {"id": "b", "label": "<script>window.pwned=2</script>"}],
                        "edges": [{"source": "a", "target": "b", "relation": evil}]}),
    ]
    requests, errors = _open(page, view_server, views)
    expect(_shadow(page, 0, "h3")).to_have_text(evil)
    expect(_shadow(page, 0, "ol.timeline li")).to_contain_text(evil)
    for i in (1, 2):
        expect(_shadow(page, i, ".diagram")).to_have_attribute("data-rendered", "true")
        expect(_shadow(page, i, ".diagram svg")).to_contain_text("securityLevel")
    expect(_shadow(page, 3, "svg.graph")).to_be_visible()

    page.wait_for_timeout(300)
    for i in range(len(views)):
        assert _shadow(page, i, "img, script, iframe, foreignObject img").count() == 0
    assert page.evaluate("window.pwned === undefined")
    assert page.locator("img").count() == 0
    assert errors == []
    assert all(r.startswith(view_server) for r in requests), requests


def test_unknown_or_broken_views_fall_back_to_text(page, view_server):
    from playwright.sync_api import expect

    _open(page, view_server, [])
    page.evaluate("""() => window.show([
      {type: 'pie_chart', title: 'Pie', reason: '', data: {}, refs: [], alternatives: [], fallback_text: 'a: 1'},
    ])""")
    expect(_shadow(page, 0, ".hint")).to_contain_text("cannot be shown")
    expect(_shadow(page, 0, "pre.text")).to_have_text("a: 1")
    expect(_shadow(page, 0, ".switch")).to_have_count(0)

    page.evaluate("""() => {
      const el = document.createElement('mycelos-view');
      el.id = 'v1';
      el.setAttribute('view-json', JSON.stringify({type: 'counts', title: 'From attribute', reason: 'r',
        data: {items: [{label: 'x', value: 2}]}, refs: [], alternatives: [], fallback_text: 'x: 2'}));
      document.getElementById('out').appendChild(el);
    }""")
    expect(_shadow(page, 1, ".bar-value")).to_have_text("2")
    assert json.loads(page.evaluate("JSON.stringify(document.getElementById('v1').view.data)")) == {
        "items": [{"label": "x", "value": 2}],
    }


def test_switcher_shows_prebuilt_variants(page, view_server):
    """Slice 4: an answer carries its alternatives; switching needs no round trip."""
    from playwright.sync_api import expect

    timeline, graph = demo_views()[0], demo_views()[2]
    payload = view_to_dict(timeline)
    payload["variants"] = {"graph_slice": view_to_dict(graph)}
    _, errors = _open(page, view_server, [])
    page.evaluate("p => window.show([p])", payload)

    _shadow(page, 0, ".switch button").filter(has_text="Connections").click()
    expect(_shadow(page, 0, "section.view")).to_have_attribute("data-type", "graph_slice")
    assert page.evaluate("window.events")[-1]["detail"] == {
        "from": "timeline", "to": "graph_slice", "view": "timeline",
    }
    # The variant's own switcher leads back to the first view and to text.
    expect(_shadow(page, 0, ".switch button")).to_have_text(["Connections", "Timeline", "Text"])
    _shadow(page, 0, ".switch button").filter(has_text="Text").click()
    expect(_shadow(page, 0, "pre.text")).to_contain_text("Müller GmbH")
    _shadow(page, 0, ".switch button").filter(has_text="Timeline").click()
    expect(_shadow(page, 0, "ol.timeline")).to_be_visible()

    # A host that handles the switch keeps control.
    page.evaluate("window.handle = {'mycelos-view-switch': true}")
    _shadow(page, 0, ".switch button").filter(has_text="Connections").click()
    expect(_shadow(page, 0, "section.view")).to_have_attribute("data-type", "timeline")

    # Variants with a mismatched type are ignored (text fallback instead).
    bad = view_to_dict(timeline)
    bad["variants"] = {"graph_slice": {**view_to_dict(graph), "type": "approval_card"}}
    page.evaluate("window.handle = {}")
    page.evaluate("p => window.show([p])", bad)
    _shadow(page, 0, ".switch button").filter(has_text="Connections").click()
    expect(_shadow(page, 0, "pre.text")).to_be_visible()
    assert errors == []
