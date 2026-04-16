"""E2E: Quick Capture Cmd+K modal and save flow."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


PAGES = [
    "/pages/chat.html",
    "/pages/knowledge.html",
    "/pages/workflows.html",
    "/pages/connectors.html",
]


@pytest.mark.parametrize("page_path", PAGES)
def test_cmd_k_opens_modal(page: Page, base_url: str, page_path: str) -> None:
    """Cmd+K opens the Quick Capture modal on every sidebar-layout page."""
    page.goto(f"{base_url}{page_path}", wait_until="networkidle")
    page.wait_for_function(
        "document.getElementById('quick-capture-root') !== null",
        timeout=3000,
    )

    root = page.locator("#quick-capture-root")
    expect(root).to_be_hidden()

    page.keyboard.press("Meta+K")

    expect(root).to_be_visible(timeout=2000)
    expect(page.locator("#quick-capture-input")).to_be_focused()


def test_esc_closes_modal(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/pages/chat.html", wait_until="networkidle")
    page.wait_for_function(
        "document.getElementById('quick-capture-root') !== null",
        timeout=3000,
    )

    page.keyboard.press("Meta+K")
    root = page.locator("#quick-capture-root")
    expect(root).to_be_visible(timeout=2000)

    page.locator("#quick-capture-input").fill("nothing to save here")
    page.keyboard.press("Escape")

    expect(root).to_be_hidden(timeout=2000)


def test_quick_capture_save_flow(page: Page, base_url: str) -> None:
    """Typing + Enter posts to /api/knowledge/notes and shows a toast."""
    page.goto(f"{base_url}/pages/chat.html", wait_until="networkidle")
    page.wait_for_function(
        "document.getElementById('quick-capture-root') !== null",
        timeout=3000,
    )

    page.keyboard.press("Meta+K")
    expect(page.locator("#quick-capture-root")).to_be_visible(timeout=2000)

    page.locator("#quick-capture-input").fill("Quick capture e2e test")
    page.keyboard.press("Enter")

    toast = page.locator("#quick-capture-toast")
    expect(toast).to_be_visible(timeout=3000)
    expect(toast).to_contain_text("notes/", timeout=2000)


def test_quick_capture_parses_due_chip(page: Page, base_url: str) -> None:
    """Typing 'tomorrow 2pm' should surface a task chip in the modal."""
    page.goto(f"{base_url}/pages/chat.html", wait_until="networkidle")
    page.wait_for_function(
        "document.getElementById('quick-capture-root') !== null",
        timeout=3000,
    )

    page.keyboard.press("Meta+K")
    expect(page.locator("#quick-capture-root")).to_be_visible(timeout=2000)

    page.locator("#quick-capture-input").fill("Call Lisa tomorrow 2pm")
    page.wait_for_timeout(300)

    chips = page.locator("#quick-capture-chips")
    expect(chips).to_contain_text("task", timeout=2000)
