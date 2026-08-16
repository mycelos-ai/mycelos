"""Live Home flow against the temporary FastAPI server and SQLite database."""

from __future__ import annotations

import time
from urllib.parse import urlparse

from playwright.sync_api import Page, expect


def _drag_to_center(page: Page, source, target) -> None:
    source_box = source.bounding_box()
    target_box = target.bounding_box()
    assert source_box is not None and target_box is not None
    page.mouse.move(
        source_box["x"] + source_box["width"] / 2,
        source_box["y"] + source_box["height"] / 2,
    )
    page.mouse.down()
    page.mouse.move(
        target_box["x"] + target_box["width"] / 2,
        target_box["y"] + target_box["height"] / 2,
        steps=12,
    )
    page.mouse.up()


def test_live_capture_and_graph_position_survive_reload(
    page: Page, base_url: str
) -> None:
    """Home capture and a saved graph position must survive real reloads."""
    unique = str(time.time_ns())
    note_title = f"Live Home capture {unique}"
    topic_title = f"Live topic {unique}"
    page.add_init_script(
        "localStorage.setItem('mycelos.home.mode', 'tree');"
        "localStorage.removeItem('mycelos.home.expanded');"
        "localStorage.removeItem('mycelos.home.graph.topics');"
        "localStorage.removeItem('mycelos.home.graph.viewport');"
    )
    page.goto(f"{base_url}/pages/dashboard.html", wait_until="networkidle")
    omnibox = page.locator(".home-omnibox input")

    omnibox.fill(note_title)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).path == "/api/knowledge/notes"
    ) as capture_info:
        omnibox.press("Shift+Enter")
    assert capture_info.value.status == 200
    created_path = capture_info.value.json()["path"]

    page.reload(wait_until="networkidle")
    page.locator(".home-omnibox input").fill(note_title)
    expect(
        page.locator(".home-tree-row").filter(has_text=note_title)
    ).to_be_visible(timeout=5000)
    assert created_path.startswith("notes/")

    topic_response = page.request.post(
        f"{base_url}/api/knowledge/topics", data={"name": topic_title}
    )
    assert topic_response.status == 200
    topic_path = topic_response.json()["path"]
    assert page.request.put(
        f"{base_url}/api/knowledge/graph/positions/{created_path}",
        data={"x": 320, "y": 300},
    ).status == 200
    assert page.request.put(
        f"{base_url}/api/knowledge/graph/positions/{topic_path}",
        data={"x": 820, "y": 300},
    ).status == 200

    page.goto(f"{base_url}/pages/dashboard.html", wait_until="networkidle")
    page.get_by_role("button", name="Graph", exact=True).click()
    topic = page.locator(f'.home-graph-node[data-graph-id="{topic_path}"]')
    expect(topic).to_be_visible()
    page.locator(".home-omnibox input").fill(note_title)
    node = page.locator(f'.home-graph-node[data-graph-id="{created_path}"]')
    expect(node).to_be_visible(timeout=5000)

    with page.expect_response(
        lambda response: response.request.method == "PUT"
        and urlparse(response.url).path
        == f"/api/knowledge/graph/positions/{created_path}"
    ) as position_info:
        with page.expect_response(
            lambda response: response.request.method == "PUT"
            and urlparse(response.url).path
            == f"/api/knowledge/notes/{created_path}"
        ) as parent_info:
            _drag_to_center(page, node, topic)
    assert parent_info.value.status == 200
    assert position_info.value.status == 200
    dropped_x = node.get_attribute("data-x")
    dropped_y = node.get_attribute("data-y")
    assert dropped_x is not None and dropped_y is not None

    page.reload(wait_until="networkidle")
    page.locator(".home-omnibox input").fill(note_title)
    reloaded = page.locator(f'.home-graph-node[data-graph-id="{created_path}"]')
    expect(reloaded).to_have_attribute("data-x", dropped_x, timeout=5000)
    expect(reloaded).to_have_attribute("data-y", dropped_y)
