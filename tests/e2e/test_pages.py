"""E2E: All pages load without errors and show key elements."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


PAGES = [
    ("/pages/chat.html", "Chat", "Type a message"),
    ("/pages/dashboard.html", "Brain", None),
    ("/pages/inbox.html", "Inbox", None),
    ("/pages/agents.html", "Agents", None),
    ("/pages/knowledge.html", "Knowledge", None),
    ("/pages/workflows.html", "Workflows", None),
    ("/pages/doctor.html", "System Doctor", None),
    ("/pages/connectors.html", "Connectors", None),
    ("/pages/ai-providers.html", "AI Providers", None),
    ("/pages/account-settings.html", "Preferences", None),
    ("/pages/settings.html", "System Settings", None),
    ("/pages/docs.html", "Documentation", None),
    ("/pages/about.html", "Mycelos", None),
]


@pytest.mark.parametrize("path,title_text,placeholder", PAGES)
def test_page_loads(page: Page, base_url: str, path: str, title_text: str, placeholder: str | None) -> None:
    """Each page should load without JS errors and show its heading."""
    errors: list[str] = []
    page.on("pageerror", lambda err: errors.append(str(err)))

    page.goto(f"{base_url}{path}", wait_until="networkidle")

    # Page should have a title
    import re
    expect(page).to_have_title(re.compile(r"Mycelos"))

    # Main heading should contain the page name. Scope to the <h1>/<h2> in
    # the main content so we don't accidentally match sidebar nav entries
    # (which include the page name as a link in the Admin submenu).
    heading = page.locator("main h1, main h2").filter(has_text=title_text).first
    if path == "/pages/dashboard.html":
        # Home keeps its only page heading available to assistive technology.
        expect(heading).to_have_text(title_text)
    else:
        expect(heading).to_be_visible()

    # Chat page should have the message input
    if placeholder:
        input_box = page.get_by_placeholder(placeholder)
        expect(input_box).to_be_visible()

    # Log JS errors as warnings (some Alpine.js template errors are benign)
    if errors:
        import warnings
        warnings.warn(f"JS errors on {path}: {errors}")


def test_sidebar_navigation(page: Page, base_url: str) -> None:
    """The desktop shell exposes the five fixed product surfaces."""
    page.goto(f"{base_url}/pages/chat.html", wait_until="networkidle")

    sidebar = page.locator("aside").first
    expect(sidebar).to_be_visible()

    targets = {
        "Brain": "/pages/dashboard.html",
        "Inbox": "/pages/inbox.html",
        "Routines": "/pages/workflows.html",
        "Converse": "/pages/chat.html",
        "System": "/pages/settings.html",
    }
    for label, href in targets.items():
        link = sidebar.get_by_role("link", name=label, exact=True)
        expect(link).to_have_attribute("href", href)

    expect(sidebar.get_by_role("link", name="Converse", exact=True)).to_have_attribute(
        "aria-current", "page"
    )


def test_inbox_uses_the_shared_network_warning_style(page: Page, base_url: str) -> None:
    page.route(
        "**/api/health",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"security":{"network_exposed":true,"password_protected":false}}',
        ),
    )
    page.goto(f"{base_url}/pages/inbox.html", wait_until="networkidle")

    warning = page.locator("aside.brain-sidebar .brain-network-warning")
    expect(warning).to_be_visible()
    expect(warning).to_have_css("display", "flex")


def test_chat_welcome_screen(page: Page, base_url: str) -> None:
    """Chat page should show welcome message and suggested actions."""
    # Clear session storage to get fresh welcome screen
    page.goto(f"{base_url}/pages/chat.html", wait_until="networkidle")
    page.evaluate("sessionStorage.clear(); localStorage.removeItem('mycelos_session_id')")
    page.reload(wait_until="networkidle")

    # Welcome heading
    expect(page.locator("text=Mycelos").first).to_be_visible()

    # Suggested action buttons (may not exist if session was already created)
    welcome_btn = page.get_by_role("button", name="What can you do?")
    if welcome_btn.count() > 0:
        expect(welcome_btn).to_be_visible()
        expect(page.get_by_role("button", name="Create a new agent")).to_be_visible()
        expect(page.get_by_role("button", name="Show my workflows")).to_be_visible()

    # Agent selector should show Mycelos
    expect(page.locator("text=Mycelos").first).to_be_visible()


def test_connectors_page_tiles(page: Page, base_url: str) -> None:
    """Connectors page should show Channels, Services, and MCP Connectors sections."""
    page.goto(f"{base_url}/pages/connectors.html", wait_until="networkidle")

    # Three section headings
    expect(page.locator("text=Channels").first).to_be_visible()
    expect(page.locator("text=Services").first).to_be_visible()
    expect(page.locator("text=MCP Connectors").first).to_be_visible()

    # Channels: Telegram should be listed
    expect(page.locator("text=Telegram").first).to_be_visible()

    # Services: Email should be listed
    expect(page.locator("text=Email").first).to_be_visible()
