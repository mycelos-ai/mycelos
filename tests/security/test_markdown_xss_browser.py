"""Security invariant: rendered Markdown never runs script (stored XSS).

Notes, chat replies and workflow results contain outside text (imported
mail, web pages, LLM output). Each payload below sets ``window.__xss``
if any of it ever executes. The pages are served with the gateway's real
security headers, so the test also proves the pages still work under the
Content-Security-Policy.

Real browser (Playwright), local static files, no network: every
stylesheet, script and font is bundled with the gateway.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from mycelos.gateway.security_headers import SecurityHeadersMiddleware

FRONTEND = Path(__file__).resolve().parents[2] / "src" / "mycelos" / "frontend"

PAYLOADS = [
    # Attributes in double quotes, otherwise marked escapes the tag and
    # the test would pass for the wrong reason.
    '<img src="x" onerror="window.__xss=1">',
    "Text\n\n```mermaid\ngraph TD\n  A[\"<img src=x onerror=window.__xss=2>\"] --> B\n```\n",
    "Before <img src=\"x\" onerror=\"window.__xss=3\"> "
    "[action] type: functions.connector_setup args: {connector_id: 'gmail'} [/action] "
    'after <svg onload="window.__xss=3"></svg>',
    "Run `/connector <img src=x onerror=window.__xss=4>` now",
    "```\n/connector setup <img src=x onerror=window.__xss=5>\n```",
    "[click me](javascript:window.__xss=6)",
    '<a href="#" onclick="window.__xss=7">x</a><iframe srcdoc="<script>parent.__xss=7</script>"></iframe>',
    "[[<img src=\"x\" onerror=\"window.__xss=8\">]] and [[Note|<b onmouseover=\"window.__xss=8\">x</b>]]",
]


@pytest.fixture
def ui_server():
    api = FastAPI()
    api.add_middleware(SecurityHeadersMiddleware)

    @api.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
    def any_api(rest: str):
        return JSONResponse({})

    api.mount("/shared", StaticFiles(directory=str(FRONTEND / "shared")))
    api.mount("/pages", StaticFiles(directory=str(FRONTEND / "pages")))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(api, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()


def _open(page, url: str) -> list[str]:
    """Open a page and return CSP violations."""
    violations: list[str] = []
    page.on("console", lambda m: violations.append(m.text)
            if "Content Security Policy" in m.text else None)
    page.goto(url)
    page.wait_for_function("typeof window.MycelosSafeHtml === 'object'")
    return violations


def _render_all(page, fn: str) -> None:
    page.evaluate(
        """([fn, payloads]) => {
            const host = document.createElement('div');
            host.id = 'xss-host';
            document.body.appendChild(host);
            for (const p of payloads) {
              const box = document.createElement('div');
              box.innerHTML = (fn === 'safe') ? window.MycelosSafeHtml.markdown(p) : window[fn](p);
              host.appendChild(box);
            }
            // Fire hover/load handlers if any survived.
            host.querySelectorAll('*').forEach(el => {
              el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
            });
        }""",
        [fn, PAYLOADS],
    )
    # Give onerror / onload / mermaid.run() time to fire.
    page.wait_for_timeout(600)


def _assert_clean(page) -> None:
    assert page.evaluate("window.__xss") is None
    leftovers = page.evaluate(
        """() => {
            const host = document.getElementById('xss-host');
            const bad = [];
            host.querySelectorAll('*').forEach(el => {
              for (const a of el.attributes) {
                if (a.name.startsWith('on') || a.name === 'srcdoc') bad.push(el.tagName + '[' + a.name + ']');
                if (a.name === 'href' && /^\\s*javascript:/i.test(a.value)) bad.push('javascript-href');
              }
              if (['SCRIPT', 'IFRAME'].includes(el.tagName)) bad.push(el.tagName);
            });
            return bad;
        }"""
    )
    assert leftovers == []


def test_chat_replies_cannot_run_script(page, ui_server):
    violations = _open(page, ui_server + "/pages/chat.html")
    page.wait_for_function("typeof window.renderMarkdown === 'function'")
    _render_all(page, "renderMarkdown")
    _assert_clean(page)
    assert violations == []


def test_chat_keeps_command_and_retry_buttons(page, ui_server):
    _open(page, ui_server + "/pages/chat.html")
    page.wait_for_function("typeof window.renderMarkdown === 'function'")
    html = page.evaluate(
        """() => {
            const box = document.createElement('div');
            box.id = 'ok-host';
            // The page is full-height with its own scroll areas, so a box
            // appended to <body> sits off-screen; pin it into view to click.
            box.style.cssText = 'position:fixed;top:0;left:0;z-index:99999;background:#fff';
            box.innerHTML = renderMarkdown("**bold** run `/connector setup gmail`")
              + renderMarkdown("Try: [action] type: functions.connector_setup args: {connector_id: 'gmail'} [/action]");
            document.body.appendChild(box);
            window.__retried = null;
            window.__retryToolCall = (btn) => { window.__retried = btn.getAttribute('data-fn'); };
            return box.innerHTML;
        }"""
    )
    assert "<strong>bold</strong>" in html
    assert page.locator("#ok-host .cmd-link").get_attribute("data-cmd") == "/connector setup gmail"
    page.locator("#ok-host .mycelos-retry-action").click()
    assert page.evaluate("window.__retried") == "connector_setup"


def test_knowledge_notes_cannot_run_script(page, ui_server):
    violations = _open(page, ui_server + "/pages/knowledge.html")
    page.wait_for_function("typeof window.renderMarkdown === 'function'")
    _render_all(page, "renderMarkdown")
    _assert_clean(page)
    assert violations == []


def test_knowledge_wikilinks_still_work(page, ui_server):
    _open(page, ui_server + "/pages/knowledge.html")
    page.wait_for_function("typeof window.renderMarkdown === 'function'")
    html = page.evaluate("() => renderMarkdown('See [[Tom & Jerry]] and [[Plan|the plan]].')")
    assert 'data-note="Tom%20%26%20Jerry"' in html
    assert ">the plan</a>" in html


def test_workflow_results_cannot_run_script(page, ui_server):
    violations = _open(page, ui_server + "/pages/workflows.html")
    _render_all(page, "safe")
    _assert_clean(page)
    assert violations == []


def test_sanitizer_fails_closed_without_dompurify(page, ui_server):
    _open(page, ui_server + "/pages/workflows.html")
    out = page.evaluate(
        """() => { const saved = window.DOMPurify; window.DOMPurify = undefined;
                   try { return MycelosSafeHtml.sanitize('<img src=x onerror=alert(1)>'); }
                   finally { window.DOMPurify = saved; } }"""
    )
    assert "<img" not in out and "&lt;img" in out
