"""Security invariant: the Web UI loads nothing from third-party hosts.

Mycelos often runs on a home network without internet access, and every
outside host the UI loads code or styles from is a supply-chain risk.
Tailwind is compiled ahead of time (``tools/tailwind``) and the icon font
is bundled, both under ``frontend/shared/vendor/``. These tests keep it
that way:

- no page or shared file references a CDN for scripts, styles or fonts,
- the Content-Security-Policy allows scripts, styles and fonts only from
  the gateway itself,
- in a real browser with all outside traffic blocked, the pages make no
  outside request and still get their Tailwind styles and icon font.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from pathlib import Path

import pytest

from mycelos.gateway.security_headers import build_csp

FRONTEND = Path(__file__).resolve().parents[2] / "src" / "mycelos" / "frontend"
VENDOR = FRONTEND / "shared" / "vendor"

# Pages that use Tailwind utility classes and Material Symbols icons.
TAILWIND_PAGES = [
    "account-settings.html",
    "agents.html",
    "chat.html",
    "connectors-advanced.html",
    "doctor.html",
    "inbox.html",
    "knowledge.html",
    "system-advanced.html",
    "workflows.html",
]

# A resource tag or CSS rule that pulls something from another host.
_EXTERNAL_RESOURCE = re.compile(
    r"""<script[^>]+src=["']?(?:https?:)?//"""
    r"""|<link[^>]+href=["']?(?:https?:)?//"""
    r"""|@import\s+(?:url\()?["']?(?:https?:)?//"""
    r"""|url\(\s*["']?(?:https?:)?//""",
    re.IGNORECASE,
)


def _own_files() -> list[Path]:
    files = [FRONTEND / "index.html"]
    files += sorted((FRONTEND / "pages").glob("*.html"))
    for pattern in ("*.html", "*.js", "*.css"):
        files += sorted((FRONTEND / "shared").glob(pattern))
    files += sorted(VENDOR.rglob("*.css"))
    return [f for f in files if f.name != "mermaid.min.js"]


@pytest.mark.parametrize("path", _own_files(), ids=lambda p: str(p.relative_to(FRONTEND)))
def test_frontend_files_load_nothing_from_other_hosts(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    hits = [m.group(0) for m in _EXTERNAL_RESOURCE.finditer(text)]
    assert not hits, f"{path.name} loads from another host: {hits}"


@pytest.mark.parametrize("directive", ["script-src", "style-src", "font-src"])
def test_csp_allows_code_styles_and_fonts_only_from_gateway(directive: str) -> None:
    value = next(d.strip() for d in build_csp().split(";") if d.strip().startswith(directive))
    sources = value.split()[1:]
    assert "'self'" in sources
    outside = [s for s in sources if "://" in s or s in ("*", "https:", "http:")]
    assert not outside, f"{directive} allows outside hosts: {outside}"


def test_bundled_tailwind_contains_project_theme() -> None:
    css = (VENDOR / "tailwind.css").read_text(encoding="utf-8")
    # Utilities from the Mycelos theme (colors are CSS variables).
    assert ".bg-surface-container{" in css
    assert "var(--surface-container)" in css
    assert ".text-on-surface-variant{" in css
    # @tailwindcss/forms base styles (the CDN used ?plugins=forms).
    assert "[type=checkbox]" in css or "[type='checkbox']" in css


@pytest.mark.parametrize("name", TAILWIND_PAGES)
def test_tailwind_is_the_last_stylesheet_in_head(name: str) -> None:
    """The CDN injected its styles at the end of <head>, so utilities beat
    base.css. Keep that cascade order, or spacing and colors change."""
    head = (FRONTEND / "pages" / name).read_text(encoding="utf-8").split("</head>", 1)[0]
    sheets = re.findall(r"""<link[^>]+rel=["']stylesheet["'][^>]*>|<style\b""", head)
    assert sheets and "/shared/vendor/tailwind.css" in sheets[-1], sheets[-3:]


def test_bundled_icon_font_is_present() -> None:
    css = (VENDOR / "material-symbols" / "material-symbols.css").read_text(encoding="utf-8")
    assert "font-family: 'Material Symbols Outlined'" in css
    assert "url(material-symbols-outlined.woff2)" in css
    font = VENDOR / "material-symbols" / "material-symbols-outlined.woff2"
    assert font.read_bytes()[:4] == b"wOF2"


# --- real browser, outside network blocked ---------------------------------


@pytest.fixture
def ui_server():
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles

    from mycelos.gateway.security_headers import SecurityHeadersMiddleware

    api = FastAPI()
    api.add_middleware(SecurityHeadersMiddleware)

    @api.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
    def any_api(rest: str):
        return JSONResponse({})

    api.mount("/shared", StaticFiles(directory=str(FRONTEND / "shared")))
    api.mount("/pages", StaticFiles(directory=str(FRONTEND / "pages")))
    api.mount("/assets", StaticFiles(directory=str(FRONTEND / "assets")))
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


def _block_outside(page, base: str) -> list[str]:
    outside: list[str] = []

    def handle(route):
        url = route.request.url
        if url.startswith(base) or url.startswith(("data:", "blob:")):
            route.continue_()
        else:
            outside.append(url)
            route.abort()

    page.route("**/*", handle)
    return outside


@pytest.mark.parametrize("name", TAILWIND_PAGES)
def test_page_makes_no_outside_request(page, ui_server, name: str) -> None:
    outside = _block_outside(page, ui_server)
    page.goto(f"{ui_server}/pages/{name}", wait_until="load")
    page.wait_for_timeout(300)
    assert outside == []
    # Tailwind utilities apply without the CDN.
    styled = page.evaluate(
        """() => {
            const el = document.createElement('div');
            el.className = 'flex p-4 rounded-xl bg-surface-container';
            document.body.appendChild(el);
            const cs = getComputedStyle(el);
            const out = {display: cs.display, padding: cs.paddingTop, radius: cs.borderTopLeftRadius};
            el.remove();
            return out;
        }"""
    )
    assert styled == {"display": "flex", "padding": "16px", "radius": "12px"}


def test_icon_font_loads_from_gateway(page, ui_server) -> None:
    outside = _block_outside(page, ui_server)
    page.goto(f"{ui_server}/pages/chat.html", wait_until="load")
    loaded = page.evaluate(
        """async () => {
            const span = document.createElement('span');
            span.className = 'material-symbols-outlined';
            span.textContent = 'home';
            document.body.appendChild(span);
            await document.fonts.load('24px "Material Symbols Outlined"', 'home');
            await document.fonts.ready;
            return document.fonts.check('24px "Material Symbols Outlined"', 'home')
                && [...document.fonts].some(f => f.family.includes('Material Symbols') && f.status === 'loaded');
        }"""
    )
    assert loaded is True
    assert outside == []
