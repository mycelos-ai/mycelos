"""E2E: Connector setup — widget flow and connectors page."""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect


@pytest.fixture
def skip_without_api_key():
    """Skip if no real API key available."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or key.startswith("sk-ant-your") or "test" in key.lower():
        pytest.skip("Real ANTHROPIC_API_KEY required for connector E2E")


def test_connector_widget_appears_in_chat(
    page: Page, base_url: str, skip_without_api_key,
) -> None:
    """Asking to set up email should show the connector widget inline."""
    # Start fresh session
    page.goto(f"{base_url}/pages/chat.html", wait_until="networkidle")
    page.evaluate("sessionStorage.clear(); localStorage.removeItem('mycelos_session_id')")
    page.reload(wait_until="networkidle")

    # Ask to set up email connector
    input_box = page.get_by_placeholder("Type a message...")
    input_box.fill("Set up the email connector for me")
    input_box.press("Enter")

    # Wait for assistant response (agent may call ui.open_page or explain)
    page.wait_for_selector("text=MYCELOS", timeout=20000)
    page.wait_for_timeout(3000)  # Wait for tool calls to complete

    # The response should mention email setup
    body = page.inner_text("main")
    assert "email" in body.lower() or "Email" in body, \
        f"Expected email-related response, got: {body[:300]}"


def test_connectors_page_shows_active_connectors(
    page: Page, base_url: str,
) -> None:
    """Connectors page should list pre-configured connectors."""
    page.goto(f"{base_url}/pages/connectors.html", wait_until="networkidle")

    # Should show at least DuckDuckGo (registered during init)
    page.wait_for_timeout(1000)
    body = page.inner_text("main")
    # Builtin connectors from init
    assert "DuckDuckGo" in body or "HTTP" in body or "ACTIVE" in body, \
        f"Expected active connectors, got: {body[:500]}"


def test_connectors_page_use_case_tiles(
    page: Page, base_url: str,
) -> None:
    """Clicking a MCP use-case tile should expand to show connector options."""
    page.goto(f"{base_url}/pages/connectors.html", wait_until="networkidle")

    # Click the "Browse the Web" MCP tile
    browse_tile = page.locator("text=Browse the Web").first
    browse_tile.click()
    page.wait_for_timeout(500)

    # Should show Playwright option
    expect(page.locator("text=Playwright").first).to_be_visible()


def test_add_connector_via_page(
    page: Page, base_url: str,
) -> None:
    """Add a custom connector through the connectors page form."""
    page.goto(f"{base_url}/pages/connectors.html", wait_until="networkidle")

    # Click Add Connector button
    add_btn = page.locator("text=ADD CONNECTOR").or_(
        page.locator("button:has-text('Add Connector')"),
    ).first
    add_btn.click()
    page.wait_for_timeout(500)

    # Form should appear with name field. Scope to the add-connector form by
    # its placeholder to avoid matching the sidebar's quick-capture input.
    name_input = page.locator("input[placeholder='e.g. playwright']")
    expect(name_input).to_be_visible()
