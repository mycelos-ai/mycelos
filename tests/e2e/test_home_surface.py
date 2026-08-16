"""E2E contract for Home, Package 4a."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Page, Route, expect

TREE_GRAPH = {
    "nodes": [
        {"id": "topics/work", "title": "Work", "type": "topic"},
        {"id": "topics/projects", "title": "Projects", "type": "topic"},
        {"id": "notes/muller", "title": "Müller Filing", "type": "note"},
    ],
    "edges": [
        {
            "source": "topics/projects",
            "target": "topics/work",
            "kind": "parent",
        },
        {
            "source": "notes/muller",
            "target": "topics/work",
            "kind": "parent",
        },
    ],
    "stats": {"notes": 3, "links": 2},
}

SEARCH_RESULTS = [
    {
        "path": "notes/muller",
        "title": "Müller Filing",
        "type": "note",
        "parent_path": "topics/work",
    }
]


def _json(route: Route, payload: Any) -> None:
    route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(payload),
    )


def _mock_home(
    page: Page,
    *,
    graph: dict[str, Any] | None = None,
    search_results: list[dict[str, Any]] | None = None,
    inbox_count: int = 0,
    inbox_entries: list[dict[str, Any]] | None = None,
    placements: list[dict[str, Any]] | None = None,
    health: dict[str, Any] | None = None,
    kept_note: dict[str, Any] | None = None,
    answer: str = "The filing is due today.",
) -> dict[str, list[Any]]:
    """Install deterministic routes before Home starts its first request."""
    calls: dict[str, list[Any]] = {"queries": [], "kept": [], "asked": []}
    graph_payload = TREE_GRAPH if graph is None else graph
    results_payload = SEARCH_RESULTS if search_results is None else search_results

    page.route(
        "**/api/knowledge/graph",
        lambda route: _json(route, graph_payload),
    )
    page.route(
        "**/api/inbox/count",
        lambda route: _json(route, {"count": inbox_count}),
    )
    page.route(
        "**/api/inbox/placements**",
        lambda route: _json(route, {"placements": placements or []}),
    )
    page.route("**/api/health", lambda route: _json(route, health or {}))
    page.route(
        "**/api/inbox",
        lambda route: _json(route, {"entries": inbox_entries or []}),
    )

    def notes_route(route: Route) -> None:
        request = route.request
        if request.method == "POST":
            payload = request.post_data_json
            calls["kept"].append(payload)
            _json(
                route,
                kept_note
                or {
                    "path": "notes/kept-from-home",
                    "parent_path": "notes",
                    "organizer_state": "pending",
                },
            )
            return

        query = parse_qs(urlparse(request.url).query).get("query", [""])[0]
        calls["queries"].append(query)
        _json(route, results_payload if query else [])

    page.route("**/api/knowledge/notes**", notes_route)

    def chat_route(route: Route) -> None:
        calls["asked"].append(route.request.post_data_json)
        body = (
            'event: session\ndata: {"session_id":"home-session"}\n\n'
            f"event: text\ndata: {json.dumps({'content': answer})}\n\n"
            'event: done\ndata: {"tokens":12}\n\n'
        )
        route.fulfill(status=200, content_type="text/event-stream", body=body)

    page.route("**/api/chat", chat_route)
    return calls


def _open_home(page: Page, base_url: str, *, root: bool = False) -> None:
    page.add_init_script(
        "localStorage.removeItem('mycelos.home.mode');"
        "localStorage.removeItem('mycelos.home.expanded');"
    )
    path = "/" if root else "/pages/dashboard.html"
    page.goto(f"{base_url}{path}", wait_until="networkidle")
    expect(page.locator(".home-workspace")).to_be_visible()


def test_root_lands_on_home_with_five_surface_shell(page: Page, base_url: str) -> None:
    _mock_home(page)
    _open_home(page, base_url, root=True)

    page.wait_for_url("**/pages/dashboard.html")
    expect(page.locator("main h1").first).to_have_text("Brain")

    sidebar = page.locator("aside.brain-sidebar")
    expect(sidebar).to_be_visible()
    assert sidebar.locator("nav > a").count() == 5
    expect(sidebar.get_by_role("link", name="Brain", exact=True)).to_have_attribute(
        "aria-current", "page"
    )


def test_empty_graph_shows_a_calm_start_state(page: Page, base_url: str) -> None:
    _mock_home(
        page,
        graph={"nodes": [], "edges": [], "stats": {"notes": 0, "links": 0}},
    )
    _open_home(page, base_url)

    empty = page.locator(".home-empty").first
    expect(empty).to_be_visible()
    expect(empty).to_contain_text("Your brain is ready")
    expect(empty).to_contain_text("Capture the first thought")
    expect(page.locator(".home-today")).to_contain_text(
        "Nothing needs you right now."
    )


def test_large_topic_keeps_the_tree_light(page: Page, base_url: str) -> None:
    nodes = [{"id": "topics/archive", "title": "Archive", "type": "topic"}]
    edges = []
    for index in range(600):
        note_id = f"notes/archive-{index}"
        nodes.append({"id": note_id, "title": f"Archive note {index}", "type": "note"})
        edges.append(
            {"source": note_id, "target": "topics/archive", "kind": "parent"}
        )

    _mock_home(
        page,
        graph={
            "nodes": nodes,
            "edges": edges,
            "stats": {"notes": 601, "links": 600},
        },
    )
    _open_home(page, base_url)

    archive = page.locator(".home-tree-row").filter(has_text="Archive").first
    expect(archive).to_be_visible()
    expect(archive.locator(".home-tree-count")).to_have_text("600")
    assert page.locator(".home-tree-row").count() <= 2


def test_search_uses_backend_and_keeps_diacritics(page: Page, base_url: str) -> None:
    calls = _mock_home(page)
    _open_home(page, base_url)

    omnibox = page.locator(".home-omnibox input")
    omnibox.fill("muller")

    expect(page.get_by_text("Müller Filing", exact=True)).to_be_visible(timeout=3000)
    assert "muller" in calls["queries"]


def test_search_marks_an_uncertain_placement(page: Page, base_url: str) -> None:
    _mock_home(
        page,
        placements=[
            {
                "path": "notes/muller",
                "confidence": 0.54,
                "parent": "topics/work",
            }
        ],
    )
    _open_home(page, base_url)

    page.locator(".home-omnibox input").fill("muller")
    row = page.locator(".home-tree-row.is-uncertain").filter(
        has_text="Müller Filing"
    )
    expect(row).to_be_visible(timeout=3000)
    expect(row.locator(".home-uncertain-mark")).to_have_attribute(
        "title", "This placement is ready for an optional review"
    )


def test_enter_asks_and_shows_related_note_chip(page: Page, base_url: str) -> None:
    calls = _mock_home(page, answer="Müller's filing is due today.")
    _open_home(page, base_url)

    omnibox = page.locator(".home-omnibox input")
    omnibox.fill("muller filing")
    omnibox.press("Enter")

    answer = page.locator(".home-answer")
    expect(answer).to_be_visible(timeout=3000)
    expect(answer.locator(".home-answer-text")).to_have_text(
        "Müller's filing is due today."
    )
    chip = answer.get_by_role("link", name="Müller Filing", exact=True)
    expect(chip).to_be_visible()
    expect(chip).to_have_attribute("href", "/pages/knowledge.html?note=notes%2Fmuller")
    assert calls["asked"] == [{"message": "muller filing"}]


def test_shift_enter_keeps_the_text(page: Page, base_url: str) -> None:
    _mock_home(page)
    _open_home(page, base_url)

    omnibox = page.locator(".home-omnibox input")
    omnibox.fill("A thought worth keeping")
    with page.expect_request(
        lambda request: request.method == "POST"
        and urlparse(request.url).path == "/api/knowledge/notes"
    ) as request_info:
        omnibox.press("Shift+Enter")

    assert request_info.value.post_data_json == {
        "title": "A thought worth keeping",
        "content": "A thought worth keeping",
    }
    expect(page.locator(".home-toast")).to_contain_text("Kept", timeout=3000)
    expect(omnibox).to_have_value("")


def test_keep_reports_the_initial_location_and_pending_organizer(
    page: Page, base_url: str
) -> None:
    _mock_home(
        page,
        kept_note={
            "path": "notes/kept-from-home",
            "parent_path": "notes",
            "organizer_state": "pending",
        },
    )
    _open_home(page, base_url)

    omnibox = page.locator(".home-omnibox input")
    omnibox.fill("A thought worth keeping")
    omnibox.press("Shift+Enter")

    expect(page.locator(".home-toast")).to_contain_text(
        "Kept in Notes. Mycelos will still check it.", timeout=3000
    )


@pytest.mark.parametrize("shortcut", ["Control+K", "Meta+K"])
def test_command_k_focuses_the_omnibox(
    page: Page, base_url: str, shortcut: str
) -> None:
    _mock_home(page)
    _open_home(page, base_url)

    page.locator(".home-workspace").click(position={"x": 20, "y": 100})
    omnibox = page.locator(".home-omnibox input")
    expect(omnibox).not_to_be_focused()
    page.keyboard.press(shortcut)
    expect(omnibox).to_be_focused()


def test_escape_closes_then_clears_then_unfocuses(page: Page, base_url: str) -> None:
    _mock_home(page)
    _open_home(page, base_url)

    omnibox = page.locator(".home-omnibox input")
    omnibox.fill("muller")
    omnibox.press("Enter")
    expect(page.locator(".home-answer")).to_be_visible(timeout=3000)

    page.keyboard.press("Escape")
    expect(page.locator(".home-answer")).to_be_hidden()
    expect(omnibox).to_have_value("muller")
    expect(omnibox).to_be_focused()

    page.keyboard.press("Escape")
    expect(omnibox).to_have_value("")
    expect(omnibox).to_be_focused()

    page.keyboard.press("Escape")
    expect(omnibox).not_to_be_focused()


def test_today_has_a_calm_empty_state(page: Page, base_url: str) -> None:
    _mock_home(page, inbox_count=0, inbox_entries=[])
    _open_home(page, base_url)

    today = page.locator(".home-today")
    expect(today).to_contain_text("Today")
    expect(today).to_contain_text("Nothing needs you right now.")
    expect(today).not_to_contain_text("0 need you")


def test_today_shows_real_inbox_and_due_counts(page: Page, base_url: str) -> None:
    entries = [
        {
            "id": "reminder:notes/muller",
            "kind": "reminder",
            "source": {"path": "notes/muller"},
        },
        {
            "id": "task:tasks/tax",
            "kind": "overdue_task",
            "source": {"path": "tasks/tax"},
        },
        {"id": "suggestion:4", "kind": "merge", "source": {}},
    ]
    _mock_home(page, inbox_count=3, inbox_entries=entries)
    _open_home(page, base_url)

    today = page.locator(".home-today")
    expect(today).to_contain_text("3 need you")
    expect(today).to_contain_text("2 due today")
    expect(today.get_by_role("link", name="3 need you")).to_have_attribute(
        "href", "/pages/inbox.html"
    )


def test_today_keeps_the_count_endpoint_value_when_fewer_rows_load(
    page: Page, base_url: str
) -> None:
    _mock_home(
        page,
        inbox_count=7,
        inbox_entries=[
            {"id": "suggestion:4", "kind": "merge", "source": {}},
        ],
    )
    _open_home(page, base_url)

    expect(page.locator(".home-today")).to_contain_text("7 need you")
    expect(page.locator(".home-today")).not_to_contain_text("1 need you")


def test_today_uses_singular_search_result_text(page: Page, base_url: str) -> None:
    _mock_home(page)
    _open_home(page, base_url)

    page.locator(".home-omnibox input").fill("muller")

    expect(page.locator(".home-match-hint")).to_contain_text(
        "1 match in your brain", timeout=3000
    )


def test_tree_expands_and_collapses_topics(page: Page, base_url: str) -> None:
    _mock_home(page)
    _open_home(page, base_url)

    expect(page.get_by_text("Work", exact=True)).to_be_visible()
    expect(page.get_by_text("Projects", exact=True)).to_be_hidden()

    work_row = page.locator(".home-tree-row").filter(has_text="Work").first
    toggle = work_row.locator(".home-tree-toggle")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    toggle.click()
    expect(page.get_by_text("Projects", exact=True)).to_be_visible()
    expect(toggle).to_have_attribute("aria-expanded", "true")

    toggle.click()
    expect(page.get_by_text("Projects", exact=True)).to_be_hidden()
    expect(toggle).to_have_attribute("aria-expanded", "false")


def test_root_notes_have_a_more_action_after_the_first_batch(
    page: Page, base_url: str
) -> None:
    root_notes = [
        {"id": f"notes/root-{index}", "title": f"Root note {index}", "type": "note"}
        for index in range(201)
    ]
    _mock_home(
        page,
        graph={"nodes": root_notes, "edges": [], "stats": {"notes": 201, "links": 0}},
    )
    _open_home(page, base_url)

    unfiled = page.locator(".home-tree-row").filter(has_text="Not filed yet").first
    unfiled.locator(".home-tree-toggle").click()
    expect(page.get_by_role("button", name="More (1 remaining)")).to_be_visible()
    expect(page.locator(".home-tree-row.is-note")).to_have_count(200)

    page.get_by_role("button", name="More (1 remaining)").click()
    expect(page.locator(".home-tree-row.is-note")).to_have_count(201)
    expect(page.get_by_role("button", name="More (1 remaining)")).to_be_hidden()


def test_shell_warns_about_an_unprotected_network_service(
    page: Page, base_url: str
) -> None:
    _mock_home(
        page,
        health={
            "security": {
                "network_exposed": True,
                "password_protected": False,
            }
        },
    )
    _open_home(page, base_url)

    warning = page.locator(".brain-network-warning")
    expect(warning).to_be_visible()
    expect(warning).to_contain_text("Network exposed")
    expect(warning).to_contain_text("No password set")
    expect(warning).to_have_attribute(
        "href", "/pages/docs.html?doc=raspberry-pi-setup#network-access"
    )


def test_graph_toggle_shows_placeholder_and_remembers_choice(
    page: Page, base_url: str
) -> None:
    _mock_home(page)
    _open_home(page, base_url)

    page.locator(".home-view-toggle").get_by_role(
        "button", name="Graph", exact=True
    ).click()
    placeholder = page.locator(".home-graph-placeholder")
    expect(placeholder).to_be_visible()
    expect(placeholder).to_contain_text("The graph workbench comes next")
    assert page.evaluate("localStorage.getItem('mycelos.home.mode')") == "graph"


def test_mobile_home_uses_five_item_bottom_navigation(
    page: Page, base_url: str
) -> None:
    page.set_viewport_size({"width": 375, "height": 812})
    _mock_home(page)
    _open_home(page, base_url)

    expect(page.locator("aside.brain-sidebar")).to_be_hidden()
    mobile_nav = page.locator(".brain-mobile-nav")
    expect(mobile_nav).to_be_visible()
    assert mobile_nav.locator("a").count() == 5

    omnibox = page.locator(".home-omnibox input")
    expect(omnibox).to_be_visible()
    box = omnibox.bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= 375
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    ) is True

    work_row = page.locator(".home-tree-row").filter(has_text="Work").first
    toggle_box = work_row.locator(".home-tree-toggle").bounding_box()
    assert toggle_box is not None
    assert toggle_box["width"] >= 44
    assert toggle_box["height"] >= 44

    omnibox.fill("muller")
    omnibox.press("Enter")
    chip_box = page.get_by_role("link", name="Müller Filing", exact=True).bounding_box()
    assert chip_box is not None
    assert chip_box["height"] >= 44


def test_home_uses_the_selected_german_language(page: Page, base_url: str) -> None:
    from mycelos.i18n import get_web_translations

    _mock_home(page)
    _open_home(page, base_url)
    expect(page.locator(".home-omnibox input")).to_have_attribute(
        "placeholder", "Search · Ask · Capture…"
    )

    payload = json.dumps(
        {"lang": "de", "translations": get_web_translations("de")}
    )
    response_text = json.dumps(payload)
    page.add_init_script(
        """
        window.XMLHttpRequest = class {
          open(method, url) { this.url = url; }
          send() {
            if (!this.url.endsWith('/api/i18n')) throw new Error('Unexpected XHR');
            this.status = 200;
            this.responseText = RESPONSE_TEXT;
            this.readyState = 4;
          }
        };
        """.replace("RESPONSE_TEXT", response_text)
    )
    page.reload(wait_until="networkidle")

    expect(page.locator(".home-omnibox input")).to_have_attribute(
        "placeholder", "Suchen · Fragen · Festhalten…"
    )
    expect(page.locator(".home-today")).to_contain_text(
        "Im Moment braucht nichts deine Aufmerksamkeit."
    )
    expect(page.locator(".home-view-toggle")).to_contain_text("Baum")
