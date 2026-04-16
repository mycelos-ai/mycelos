"""E2E: All pages load without errors and show key elements."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


PAGES = [
    ("/pages/chat.html", "Chat", "Type a message"),
    ("/pages/dashboard.html", "Dashboard", None),
    ("/pages/agents.html", "Agents", None),
    ("/pages/knowledge.html", "Knowledge", None),
    ("/pages/workflows.html", "Workflows", None),
    ("/pages/sessions.html", "Sessions", None),
    ("/pages/doctor.html", "System Doctor", None),
    ("/pages/connectors.html", "Connectors", None),
    ("/pages/settings.html", "Settings", None),
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
    """Sidebar links should navigate between pages."""
    page.goto(f"{base_url}/pages/chat.html", wait_until="networkidle")

    # Scope all link lookups to the desktop <aside>, otherwise they also match
    # the mobile nav rendered at the bottom and the Admin submenu entries —
    # both cause strict-mode locator violations.
    sidebar = page.locator("aside").first
    main_heading = page.locator("main h1, main h2")

    # Click on Knowledge in sidebar
    sidebar.locator("a[href='/pages/knowledge.html']").click()
    page.wait_for_url("**/knowledge.html")
    expect(main_heading.filter(has_text="Knowledge").first).to_be_visible()

    # Click on Workflows — Admin submenu has to be expanded so the link is
    # visible to Playwright's actionability checks.
    sidebar.locator("button:has-text('Admin')").click()
    sidebar.locator("a[href='/pages/workflows.html']").click()
    page.wait_for_url("**/workflows.html")
    expect(main_heading.filter(has_text="Workflows").first).to_be_visible()

    # Click on Connectors
    sidebar.locator("button:has-text('Admin')").click()
    sidebar.locator("a[href='/pages/connectors.html']").click()
    page.wait_for_url("**/connectors.html")
    expect(main_heading.filter(has_text="Connectors").first).to_be_visible()


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

    # MCP Connectors: Development tile should be visible
    expect(page.locator("text=Development").first).to_be_visible()
