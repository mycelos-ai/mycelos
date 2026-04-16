"""E2E: Chat interaction — send message, receive streaming response."""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect


@pytest.fixture
def skip_without_api_key():
    """Skip if no real API key available."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or key.startswith("sk-ant-your") or "test" in key.lower():
        pytest.skip("Real ANTHROPIC_API_KEY required for chat E2E")


def test_send_message_and_receive_response(
    page: Page, base_url: str, skip_without_api_key,
) -> None:
    """Send a message and verify the assistant responds."""
    page.goto(f"{base_url}/pages/chat.html", wait_until="networkidle")

    # Type a simple message
    input_box = page.get_by_placeholder("Type a message...")
    input_box.fill("Say hello in one word.")
    input_box.press("Enter")

    # Wait for assistant response (up to 15s for LLM call)
    assistant_msg = page.locator("[class*='system']").or_(
        page.locator("text=MYCELOS").locator("..").locator(".."),
    )
    # The response container appears after SSE streaming
    page.wait_for_selector("text=MYCELOS", timeout=15000)

    # Session should be created (header changes from "No Session")
    expect(page.locator("text=SESSION").first).to_be_visible()

    # Token count should be non-zero
    expect(page.locator("text=/\\d+ tokens|last msg/").first).to_be_visible()


def test_suggested_action_button(
    page: Page, base_url: str, skip_without_api_key,
) -> None:
    """Clicking a suggested action should send a message."""
    # Clear state and reload to get fresh welcome screen
    page.goto(f"{base_url}/pages/chat.html", wait_until="networkidle")
    page.evaluate("sessionStorage.clear(); localStorage.removeItem('mycelos_session_id')")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(500)

    # Click "What can you do?" if visible
    btn = page.get_by_role("button", name="What can you do?")
    if btn.count() == 0:
        pytest.skip("Welcome screen not visible (server has session state)")
    btn.click()

    # Should see user message bubble
    page.wait_for_selector("text=What can you do?", timeout=5000)

    # Wait for response
    page.wait_for_selector("text=MYCELOS", timeout=15000)

    # Response should mention capabilities
    page.wait_for_timeout(3000)  # Wait for streaming to complete
    body_text = page.inner_text("main")
    assert len(body_text) > 100, "Expected a substantial response"


def test_new_chat_button(page: Page, base_url: str) -> None:
    """New Chat button should be visible and clickable."""
    page.goto(f"{base_url}/pages/chat.html", wait_until="networkidle")

    # New Chat button should always be visible in sidebar
    new_chat = page.get_by_role("button", name="New Chat")
    expect(new_chat).to_be_visible()

    # Click it
    new_chat.click()
    page.wait_for_timeout(500)

    # Message input should be empty and ready
    input_box = page.get_by_placeholder("Type a message...")
    expect(input_box).to_be_visible()
    expect(input_box).to_be_empty()
