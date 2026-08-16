"""E2E contract for the accessible Home graph workbench."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, Route, expect


GRAPH = {
    "nodes": [
        {"id": "topics/work", "title": "Work", "type": "topic", "status": "active", "parent_path": None},
        {"id": "topics/projects", "title": "Projects", "type": "topic", "status": "active", "parent_path": "topics/work"},
        {"id": "notes/alpha", "title": "Alpha note", "type": "note", "status": "active", "parent_path": "topics/work"},
        {"id": "notes/beta", "title": "Beta note", "type": "note", "status": "active", "parent_path": "topics/projects"},
    ],
    "edges": [
        {"source": "topics/projects", "target": "topics/work", "kind": "parent"},
        {"source": "notes/alpha", "target": "topics/work", "kind": "parent"},
        {"source": "notes/beta", "target": "topics/projects", "kind": "parent"},
        {"source": "notes/alpha", "target": "notes/beta", "kind": "related"},
    ],
    "positions": {
        "topics/work": {"x": 320, "y": 220},
        "notes/alpha": {"x": 560, "y": 260},
    },
    "stats": {"notes": 4, "links": 4},
}


def _json(route: Route, payload: Any, *, status: int = 200) -> None:
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(payload),
    )


def _mock_graph_home(
    page: Page,
    *,
    graph: dict[str, Any] | None = None,
    graph_responses: list[tuple[int, dict[str, Any]]] | None = None,
    search_results: list[dict[str, Any]] | None = None,
    position_status: int = 200,
    parent_status: int = 200,
    defer_parent_response: bool = False,
    summary: dict[str, Any] | None = None,
    summary_responses: list[tuple[int, dict[str, Any]]] | None = None,
) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {
        "graph": [],
        "summary": [],
        "positions": [],
        "parents": [],
        "writes": [],
        "kept": [],
        "queries": [],
        "pending_parents": [],
    }
    graph_payload = GRAPH if graph is None else graph
    results = search_results or []
    summary_payload = (
        {"imports_today": 0, "sources_by_topic": {}}
        if summary is None
        else summary
    )

    def graph_route(route: Route) -> None:
        calls["graph"].append(route.request.url)
        if graph_responses:
            index = min(len(calls["graph"]) - 1, len(graph_responses) - 1)
            status, payload = graph_responses[index]
            _json(route, payload, status=status)
            return
        _json(route, graph_payload)

    page.route("**/api/knowledge/graph", graph_route)
    page.route("**/api/inbox/count", lambda route: _json(route, {"count": 0}))
    page.route("**/api/inbox/placements**", lambda route: _json(route, {"placements": []}))
    page.route("**/api/inbox", lambda route: _json(route, {"entries": []}))
    page.route("**/api/health", lambda route: _json(route, {}))

    def summary_route(route: Route) -> None:
        calls["summary"].append(route.request.url)
        if summary_responses:
            index = min(len(calls["summary"]) - 1, len(summary_responses) - 1)
            status, payload = summary_responses[index]
            _json(route, payload, status=status)
            return
        _json(route, summary_payload)

    page.route("**/api/knowledge/home-summary", summary_route)

    def position_route(route: Route) -> None:
        calls["positions"].append(route.request.post_data_json)
        calls["writes"].append(
            {
                "kind": "position",
                "path": urlparse(route.request.url).path,
                "body": route.request.post_data_json,
            }
        )
        _json(route, {"ok": position_status == 200}, status=position_status)

    page.route("**/api/knowledge/graph/positions/**", position_route)

    def notes_route(route: Route) -> None:
        request = route.request
        if request.method == "PUT":
            calls["parents"].append(request.post_data_json)
            calls["writes"].append(
                {
                    "kind": "parent",
                    "path": urlparse(request.url).path,
                    "body": request.post_data_json,
                }
            )
            if defer_parent_response:
                calls["pending_parents"].append(route)
                return
            _json(route, {"ok": parent_status == 200}, status=parent_status)
            return
        if request.method == "POST":
            calls["kept"].append(request.post_data_json)
            _json(
                route,
                {
                    "path": "notes/kept-from-home",
                    "parent_path": "notes",
                    "type": "note",
                    "organizer_state": "pending",
                },
            )
            return
        query = parse_qs(urlparse(request.url).query).get("query", [""])[0]
        calls["queries"].append(query)
        _json(route, results if query else [])

    page.route("**/api/knowledge/notes**", notes_route)
    return calls


def _open_graph(
    page: Page,
    base_url: str,
    *,
    stored_mode: str | None = None,
) -> None:
    mode_script = (
        "localStorage.removeItem('mycelos.home.mode');"
        if stored_mode is None
        else f"localStorage.setItem('mycelos.home.mode', {json.dumps(stored_mode)});"
    )
    page.add_init_script(
        mode_script
        + "localStorage.removeItem('mycelos.home.graph.topics');"
        + "localStorage.removeItem('mycelos.home.graph.viewport');"
    )
    page.goto(f"{base_url}/pages/dashboard.html", wait_until="networkidle")
    expect(page.locator(".home-workspace")).to_be_visible()


def _node(page: Page, path: str):
    return page.locator(f'.home-graph-node[data-graph-id="{path}"]')


def _expand(page: Page, path: str) -> None:
    _node(page, path).locator(".home-graph-expand").click()


def _drag(page: Page, source, target_box: dict[str, float]) -> None:
    source_box = source.bounding_box()
    assert source_box is not None
    page.mouse.move(
        source_box["x"] + source_box["width"] / 2,
        source_box["y"] + source_box["height"] / 2,
    )
    page.mouse.down()
    page.mouse.move(target_box["x"], target_box["y"], steps=12)
    page.mouse.up()


def test_first_desktop_visit_defaults_to_graph(page: Page, base_url: str) -> None:
    _mock_graph_home(page)
    _open_graph(page, base_url)

    expect(page.locator(".home-graph-surface")).to_be_visible()
    expect(page.get_by_role("button", name="Graph", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )


def test_graph_parent_edges_render_without_javascript_errors(
    page: Page, base_url: str
) -> None:
    """Removing the SVG loop scope must make this test fail."""
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _mock_graph_home(page)

    _open_graph(page, base_url)
    _expand(page, "topics/work")

    expect(page.locator(".home-graph-edge.is-parent")).to_have_count(2)
    assert errors == []


def test_initial_graph_failure_shows_retry_instead_of_the_empty_state(
    page: Page, base_url: str
) -> None:
    _mock_graph_home(
        page,
        graph_responses=[(500, {"error": "graph unavailable"})],
    )
    _open_graph(page, base_url)

    error = page.locator(".home-graph-load-error")
    expect(error).to_be_visible(timeout=2000)
    expect(error).to_contain_text("could not load")
    expect(error.get_by_role("button", name="Retry", exact=True)).to_be_visible()
    expect(page.locator(".home-graph-empty")).to_be_hidden()


def test_graph_retry_loads_data_after_an_initial_failure(
    page: Page, base_url: str
) -> None:
    _mock_graph_home(
        page,
        graph_responses=[
            (500, {"error": "graph unavailable"}),
            (200, GRAPH),
        ],
    )
    _open_graph(page, base_url)

    error = page.locator(".home-graph-load-error")
    expect(error).to_be_visible(timeout=2000)
    error.get_by_role("button", name="Retry", exact=True).click()

    expect(_node(page, "topics/work")).to_be_visible(timeout=2000)
    expect(error).to_be_hidden()


def test_graph_refresh_failure_keeps_the_last_usable_graph(
    page: Page, base_url: str
) -> None:
    _mock_graph_home(
        page,
        graph_responses=[
            (200, GRAPH),
            (500, {"error": "graph refresh unavailable"}),
        ],
    )
    _open_graph(page, base_url)
    expect(_node(page, "topics/work")).to_be_visible()

    omnibox = page.locator(".home-omnibox input")
    omnibox.fill("Keep the loaded graph")
    omnibox.press("Shift+Enter")
    expect(page.locator(".home-toast")).to_contain_text("Kept", timeout=3000)

    expect(_node(page, "topics/work")).to_be_visible()
    expect(page.locator(".home-graph-load-error")).to_be_visible(timeout=2000)
    expect(page.locator(".home-graph-empty")).to_be_hidden()

    work_button = _node(page, "topics/work").locator(".home-graph-node-main")
    work_button.click()
    expect(work_button).to_have_attribute("aria-pressed", "true")


def test_stored_mode_wins_on_a_later_desktop_visit(page: Page, base_url: str) -> None:
    _mock_graph_home(page)
    _open_graph(page, base_url, stored_mode="tree")

    expect(page.locator(".home-tree-pane")).to_be_visible()
    expect(page.locator(".home-graph-surface")).to_be_hidden()

    page.get_by_role("button", name="Graph", exact=True).click()
    expect(page.locator(".home-graph-surface")).to_be_visible()
    assert page.evaluate("localStorage.getItem('mycelos.home.mode')") == "graph"


def test_mobile_forces_tree_and_hides_graph_control(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 375, "height": 812})
    _mock_graph_home(page)
    _open_graph(page, base_url, stored_mode="graph")

    expect(page.locator(".home-tree-pane")).to_be_visible()
    expect(page.get_by_role("button", name="Graph", exact=True)).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_graph_starts_with_topic_nodes_only(page: Page, base_url: str) -> None:
    _mock_graph_home(page)
    _open_graph(page, base_url)

    expect(page.locator(".home-graph-node")).to_have_count(1)
    expect(_node(page, "topics/work")).to_be_visible()
    expect(_node(page, "notes/alpha")).to_have_count(0)
    expect(_node(page, "topics/work").locator(".home-graph-expand")).to_have_attribute(
        "aria-label", "Expand Work"
    )


def test_notes_without_any_topic_show_a_calm_explanation(
    page: Page, base_url: str
) -> None:
    _mock_graph_home(
        page,
        graph={
            "nodes": [
                {
                    "id": "notes/unfiled",
                    "title": "Unfiled note",
                    "type": "note",
                    "status": "active",
                    "parent_path": "notes",
                }
            ],
            "edges": [],
            "positions": {},
            "stats": {"notes": 1, "links": 0},
        },
    )
    _open_graph(page, base_url)

    explanation = page.locator(".home-graph-no-topics")
    expect(explanation).to_be_visible(timeout=2000)
    expect(explanation).to_contain_text(re.compile("topic", re.IGNORECASE))


def test_topic_children_load_in_batches_of_fifty(page: Page, base_url: str) -> None:
    nodes = [{"id": "topics/archive", "title": "Archive", "type": "topic"}]
    edges = []
    for index in range(101):
        path = f"notes/archive-{index:03}"
        nodes.append({"id": path, "title": f"Archive note {index:03}", "type": "note"})
        edges.append({"source": path, "target": "topics/archive", "kind": "parent"})
    _mock_graph_home(
        page,
        graph={"nodes": nodes, "edges": edges, "positions": {}, "stats": {"notes": 102, "links": 101}},
    )
    _open_graph(page, base_url)

    _expand(page, "topics/archive")
    expect(page.locator(".home-graph-node")).to_have_count(51)
    assert json.loads(
        page.evaluate("localStorage.getItem('mycelos.home.graph.topics')")
    ) == {"topics/archive": True}
    more = page.locator(".home-graph-more")
    expect(more).to_contain_text("51")
    expect(more).to_have_attribute(
        "aria-label", "Show 50 more in Archive (51 remaining)"
    )

    more.click()
    expect(page.locator(".home-graph-node")).to_have_count(101)
    more.click()
    expect(page.locator(".home-graph-node")).to_have_count(102)
    expect(more).to_be_hidden()


def test_search_adds_matches_and_topic_ancestors(page: Page, base_url: str) -> None:
    _mock_graph_home(
        page,
        graph={
            **GRAPH,
            "nodes": [
                *GRAPH["nodes"],
                {"id": "topics/personal", "title": "Personal", "type": "topic"},
            ],
        },
        search_results=[
            {"path": "notes/beta", "title": "Beta note", "type": "note", "parent_path": "topics/projects"}
        ],
    )
    _open_graph(page, base_url)

    page.locator(".home-omnibox input").fill("beta")
    expect(_node(page, "notes/beta")).to_be_visible(timeout=3000)
    expect(_node(page, "topics/projects")).to_be_visible()
    expect(_node(page, "topics/work")).to_be_visible()
    expect(_node(page, "notes/beta")).to_have_class(re.compile(r"\bis-match\b"))
    expect(_node(page, "topics/work")).not_to_have_class(re.compile(r"\bis-dimmed\b"))
    personal = _node(page, "topics/personal")
    expect(personal).to_have_class(re.compile(r"\bis-dimmed\b"))

    personal_button = personal.locator(".home-graph-node-main")
    personal_button.focus()
    expect(personal_button).to_be_focused()
    styles = personal.evaluate(
        """
        element => {
          const button = element.querySelector('.home-graph-node-main');
          const nodeStyle = getComputedStyle(element);
          const buttonStyle = getComputedStyle(button);
          return {
            opacity: nodeStyle.opacity,
            buttonOpacity: buttonStyle.opacity,
            textOpacity: getComputedStyle(button.querySelector('.home-graph-node-title')).opacity,
            outlineStyle: buttonStyle.outlineStyle,
            outlineWidth: buttonStyle.outlineWidth,
          };
        }
        """
    )
    assert styles["opacity"] == "1"
    assert styles["buttonOpacity"] == "1"
    assert styles["textOpacity"] == "1"
    assert styles["outlineStyle"] == "solid"
    assert float(styles["outlineWidth"].removesuffix("px")) >= 2

    personal_button.click()
    selected_shadow = personal_button.evaluate(
        "element => getComputedStyle(element).boxShadow"
    )
    assert selected_shadow != "none"


def test_search_offers_open_for_a_result_outside_the_graph(
    page: Page, base_url: str
) -> None:
    _mock_graph_home(
        page,
        search_results=[
            {"path": "notes/outside", "title": "Outside note", "type": "note", "parent_path": "topics/work"},
            {"path": "topics/outside", "title": "Outside topic", "type": "topic"},
        ],
    )
    _open_graph(page, base_url)

    page.locator(".home-omnibox input").fill("outside")
    action = page.get_by_role("link", name="Open Outside note")
    expect(action).to_be_visible(timeout=3000)
    expect(action).to_have_attribute("href", "/pages/knowledge.html?note=notes%2Foutside")
    expect(page.get_by_role("link", name="Open Outside topic")).to_have_attribute(
        "href", "/pages/knowledge.html?topic=topics%2Foutside"
    )
    limit_note = page.locator(".home-graph-search-limit")
    expect(limit_note).to_be_visible()
    expect(limit_note).to_contain_text("5,000")


def test_selection_shows_relations_and_escape_clears_it_before_search(
    page: Page, base_url: str
) -> None:
    calls = _mock_graph_home(
        page,
        search_results=[
            {"path": "notes/alpha", "title": "Alpha note", "type": "note", "parent_path": "topics/work"}
        ],
    )
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    page.locator(".home-omnibox input").fill("alpha")
    expect(_node(page, "notes/alpha")).to_be_visible(timeout=3000)

    _node(page, "notes/alpha").locator(".home-graph-node-main").click()
    expect(_node(page, "notes/alpha")).to_have_class(re.compile(r"\bis-selected\b"))
    expect(page.locator(".home-graph-relations")).to_contain_text("Beta note")
    expect(page.locator(".home-graph-edge.is-relation")).to_have_count(1)

    page.keyboard.press("Escape")
    expect(_node(page, "notes/alpha")).not_to_have_class(re.compile(r"\bis-selected\b"))
    expect(page.locator(".home-omnibox input")).to_have_value("alpha")
    assert calls["positions"] == []


def test_second_click_and_enter_open_the_existing_knowledge_page(
    page: Page, base_url: str
) -> None:
    _mock_graph_home(page)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    button = _node(page, "notes/alpha").locator(".home-graph-node-main")

    button.click()
    button.click()
    page.wait_for_url("**/pages/knowledge.html?note=notes%2Falpha")

    page.go_back(wait_until="networkidle")
    _expand(page, "topics/work")
    button = _node(page, "notes/alpha").locator(".home-graph-node-main")
    button.focus()
    button.press("Enter")
    page.wait_for_url("**/pages/knowledge.html?note=notes%2Falpha")


def test_graph_uses_stored_positions(page: Page, base_url: str) -> None:
    _mock_graph_home(page)
    _open_graph(page, base_url)
    _expand(page, "topics/work")

    expect(_node(page, "topics/work")).to_have_attribute("data-x", "320")
    expect(_node(page, "topics/work")).to_have_attribute("data-y", "220")
    expect(_node(page, "notes/alpha")).to_have_attribute("data-x", "560")
    expect(_node(page, "notes/alpha")).to_have_attribute("data-y", "260")


def test_free_drag_saves_position(page: Page, base_url: str) -> None:
    calls = _mock_graph_home(page)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    alpha = _node(page, "notes/alpha")
    box = alpha.bounding_box()
    assert box is not None

    _drag(page, alpha, {"x": box["x"] + 120, "y": box["y"] + 90})
    expect(page.locator(".home-graph-notice")).to_contain_text("Position saved")
    assert len(calls["positions"]) == 1
    assert set(calls["positions"][0]) == {"x", "y"}


def test_failed_position_save_restores_the_prior_position(
    page: Page, base_url: str
) -> None:
    calls = _mock_graph_home(page, position_status=500)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    alpha = _node(page, "notes/alpha")
    box = alpha.bounding_box()
    assert box is not None

    _drag(page, alpha, {"x": box["x"] + 120, "y": box["y"] + 90})
    expect(page.locator(".home-graph-notice")).to_contain_text("could not be saved")
    expect(alpha).to_have_attribute("data-x", "560")
    expect(alpha).to_have_attribute("data-y", "260")
    assert len(calls["positions"]) == 1


def test_topic_drop_saves_both_parent_and_dropped_position(
    page: Page, base_url: str
) -> None:
    calls = _mock_graph_home(page)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    alpha = _node(page, "notes/alpha")
    target = _node(page, "topics/projects").bounding_box()
    assert target is not None

    _drag(
        page,
        alpha,
        {
            "x": target["x"] + target["width"] / 2,
            "y": target["y"] + target["height"] / 2,
        },
    )
    expect(page.locator(".home-graph-notice")).to_be_visible()
    dropped = {
        "x": float(alpha.get_attribute("data-x")),
        "y": float(alpha.get_attribute("data-y")),
    }

    assert calls["parents"] == [{"parent_path": "topics/projects"}]
    assert calls["positions"] == [dropped]
    assert calls["writes"] == [
        {
            "kind": "parent",
            "path": "/api/knowledge/notes/notes/alpha",
            "body": {"parent_path": "topics/projects"},
        },
        {
            "kind": "position",
            "path": "/api/knowledge/graph/positions/notes/alpha",
            "body": dropped,
        },
    ]


def test_topic_drop_position_failure_restores_parent_and_position(
    page: Page, base_url: str
) -> None:
    calls = _mock_graph_home(page, position_status=500)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    alpha = _node(page, "notes/alpha")
    target = _node(page, "topics/projects").bounding_box()
    assert target is not None

    _drag(
        page,
        alpha,
        {
            "x": target["x"] + target["width"] / 2,
            "y": target["y"] + target["height"] / 2,
        },
    )

    expect(page.locator(".home-graph-notice.is-error")).to_be_visible(timeout=2000)
    expect(alpha).to_have_attribute("data-x", "560")
    expect(alpha).to_have_attribute("data-y", "260")
    expect(page.get_by_role("button", name="Undo move")).to_be_hidden()
    assert calls["parents"] == [
        {"parent_path": "topics/projects"},
        {"parent_path": "topics/work"},
    ]
    assert len(calls["positions"]) == 1


def test_topic_drop_changes_parent_and_undo_restores_it(
    page: Page, base_url: str
) -> None:
    calls = _mock_graph_home(page)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    alpha = _node(page, "notes/alpha")
    projects = _node(page, "topics/projects")
    target = projects.bounding_box()
    assert target is not None

    _drag(
        page,
        alpha,
        {"x": target["x"] + target["width"] / 2, "y": target["y"] + target["height"] / 2},
    )
    expect(page.get_by_role("button", name="Undo move")).to_be_visible()
    expect(alpha).to_be_visible()
    dropped_position = (
        alpha.get_attribute("data-x"),
        alpha.get_attribute("data-y"),
    )
    assert calls["parents"] == [{"parent_path": "topics/projects"}]

    page.get_by_role("button", name="Undo move").click()
    expect(page.locator(".home-graph-notice")).to_contain_text("Move undone")
    expect(alpha).to_have_attribute("data-x", dropped_position[0])
    expect(alpha).to_have_attribute("data-y", dropped_position[1])
    assert calls["parents"] == [
        {"parent_path": "topics/projects"},
        {"parent_path": "topics/work"},
    ]


def test_unrelated_graph_notice_clears_the_old_undo(
    page: Page, base_url: str
) -> None:
    _mock_graph_home(page)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    alpha = _node(page, "notes/alpha")
    target = _node(page, "topics/projects").bounding_box()
    assert target is not None

    _drag(
        page,
        alpha,
        {
            "x": target["x"] + target["width"] / 2,
            "y": target["y"] + target["height"] / 2,
        },
    )
    undo = page.get_by_role("button", name="Undo move")
    expect(undo).to_be_visible()

    alpha.locator(".home-graph-node-main").focus()
    alpha.locator(".home-graph-node-main").press("Alt+ArrowRight")

    expect(page.locator(".home-graph-notice")).to_contain_text("Position saved")
    expect(undo).to_be_hidden()


def test_root_note_move_undo_uses_its_stored_system_parent(
    page: Page, base_url: str
) -> None:
    root_graph = {
        "nodes": [
            {"id": "topics/work", "title": "Work", "type": "topic", "status": "active", "parent_path": ""},
            {"id": "notes/root", "title": "Root note", "type": "note", "parent_path": "notes"},
        ],
        "edges": [],
        "positions": {
            "topics/work": {"x": 320, "y": 220},
            "notes/root": {"x": 560, "y": 260},
        },
        "stats": {"notes": 2, "links": 0},
    }
    calls = _mock_graph_home(
        page,
        graph=root_graph,
        search_results=[
            {"path": "notes/root", "title": "Root note", "type": "note", "parent_path": "notes"}
        ],
    )
    _open_graph(page, base_url)
    page.locator(".home-omnibox input").fill("root")
    root_note = _node(page, "notes/root")
    expect(root_note).to_be_visible(timeout=3000)
    work_box = _node(page, "topics/work").bounding_box()
    assert work_box is not None

    _drag(
        page,
        root_note,
        {
            "x": work_box["x"] + work_box["width"] / 2,
            "y": work_box["y"] + work_box["height"] / 2,
        },
    )
    expect(page.get_by_role("button", name="Undo move")).to_be_visible()
    assert calls["parents"] == [{"parent_path": "topics/work"}]

    page.get_by_role("button", name="Undo move").click()
    expect(page.locator(".home-graph-notice")).to_contain_text("Move undone")
    assert calls["parents"] == [
        {"parent_path": "topics/work"},
        {"parent_path": "notes"},
    ]


def test_root_topic_move_undo_uses_null_parent(
    page: Page, base_url: str
) -> None:
    graph = {
        "nodes": [
            {"id": "topics/source", "title": "Source", "type": "topic", "status": "active", "parent_path": None},
            {"id": "topics/target", "title": "Target", "type": "topic", "status": "active", "parent_path": None},
        ],
        "edges": [],
        "positions": {
            "topics/source": {"x": 320, "y": 220},
            "topics/target": {"x": 650, "y": 220},
        },
        "stats": {"notes": 2, "links": 0},
    }
    calls = _mock_graph_home(page, graph=graph)
    _open_graph(page, base_url)
    target_box = _node(page, "topics/target").bounding_box()
    assert target_box is not None

    _drag(
        page,
        _node(page, "topics/source"),
        {
            "x": target_box["x"] + target_box["width"] / 2,
            "y": target_box["y"] + target_box["height"] / 2,
        },
    )
    expect(page.get_by_role("button", name="Undo move")).to_be_visible()
    page.get_by_role("button", name="Undo move").click()

    expect(page.locator(".home-graph-notice")).to_contain_text("Move undone")
    assert calls["parents"] == [
        {"parent_path": "topics/target"},
        {"parent_path": None},
    ]


def test_inactive_topic_is_not_a_valid_drop_or_keyboard_target(
    page: Page, base_url: str
) -> None:
    graph = {
        **GRAPH,
        "nodes": [
            *GRAPH["nodes"],
            {"id": "topics/archive", "title": "Archive", "type": "topic", "status": "archived", "parent_path": None},
        ],
    }
    calls = _mock_graph_home(page, graph=graph)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    alpha = _node(page, "notes/alpha")
    archive = _node(page, "topics/archive")
    source_box = alpha.bounding_box()
    target_box = archive.bounding_box()
    assert source_box is not None and target_box is not None

    page.mouse.move(source_box["x"] + source_box["width"] / 2, source_box["y"] + source_box["height"] / 2)
    page.mouse.down()
    page.mouse.move(target_box["x"] + target_box["width"] / 2, target_box["y"] + target_box["height"] / 2, steps=12)
    expect(archive).not_to_have_class(re.compile(r"\bis-drop-target\b"))
    expect(archive).to_have_class(re.compile(r"\bis-invalid-target\b"))
    page.mouse.up()

    expect(page.locator(".home-graph-notice")).to_contain_text("cannot move")
    assert calls["parents"] == []

    alpha.locator(".home-graph-node-main").click()
    target_select = page.get_by_label("Move to topic")
    expect(target_select.locator("option", has_text="Archive")).to_have_count(0)


def test_alt_arrow_moves_and_saves_a_selected_node_position(
    page: Page, base_url: str
) -> None:
    calls = _mock_graph_home(page)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    alpha = _node(page, "notes/alpha")
    button = alpha.locator(".home-graph-node-main")
    button.focus()

    button.press("Alt+ArrowRight")

    expect(alpha).to_have_attribute("data-x", "584")
    expect(alpha).to_have_attribute("data-y", "260")
    assert calls["positions"] == [{"x": 584, "y": 260}]


def test_keyboard_topic_picker_moves_and_undoes_the_selected_node(
    page: Page, base_url: str
) -> None:
    calls = _mock_graph_home(page)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    _node(page, "notes/alpha").locator(".home-graph-node-main").click()
    target_select = page.get_by_label("Move to topic")
    expect(target_select).to_be_visible()
    target_select.focus()
    target_select.press("ArrowDown")
    target_select.press("Tab")
    move_button = page.get_by_role("button", name="Move selected node")
    expect(move_button).to_be_focused()
    move_button.press("Enter")

    expect(page.get_by_role("button", name="Undo move")).to_be_visible()
    assert calls["parents"] == [{"parent_path": "topics/projects"}]
    page.get_by_role("button", name="Undo move").click()
    assert calls["parents"] == [
        {"parent_path": "topics/projects"},
        {"parent_path": "topics/work"},
    ]


def test_pending_parent_move_blocks_duplicate_controls_and_keeps_original_parent(
    page: Page, base_url: str
) -> None:
    calls = _mock_graph_home(page, defer_parent_response=True)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    _expand(page, "topics/projects")
    alpha = _node(page, "notes/alpha")
    alpha.locator(".home-graph-node-main").click()
    target_select = page.get_by_label("Move to topic")
    target_select.select_option("topics/projects")
    move_button = page.get_by_role("button", name="Move selected node")

    move_button.click()
    move_button.evaluate("element => element.click()")
    expect(move_button).to_be_disabled()
    expect(target_select).to_be_disabled()
    undo_button = page.locator(".home-graph-notice button")
    expect(undo_button).to_be_disabled()

    work_box = _node(page, "topics/work").bounding_box()
    assert work_box is not None
    _drag(
        page,
        alpha,
        {
            "x": work_box["x"] + work_box["width"] / 2,
            "y": work_box["y"] + work_box["height"] / 2,
        },
    )
    assert calls["parents"] == [{"parent_path": "topics/projects"}]

    _json(calls["pending_parents"][0], {"ok": True})
    expect(undo_button).to_be_visible()
    expect(undo_button).to_be_enabled()
    undo_button.click()
    expect(undo_button).to_be_disabled()
    undo_button.evaluate("element => element.click()")
    assert calls["parents"] == [
        {"parent_path": "topics/projects"},
        {"parent_path": "topics/work"},
    ]
    _json(calls["pending_parents"][1], {"ok": True})
    expect(page.locator(".home-graph-notice")).to_contain_text("Move undone")


def test_failed_pending_parent_move_unlocks_the_controls(
    page: Page, base_url: str
) -> None:
    calls = _mock_graph_home(page, defer_parent_response=True)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    _node(page, "notes/alpha").locator(".home-graph-node-main").click()
    target_select = page.get_by_label("Move to topic")
    target_select.select_option("topics/projects")
    move_button = page.get_by_role("button", name="Move selected node")

    move_button.click()
    expect(move_button).to_be_disabled()
    _json(calls["pending_parents"][0], {"error": "move_rejected"}, status=422)

    expect(page.locator(".home-graph-notice")).to_contain_text("could not be moved")
    expect(target_select).to_be_enabled()
    expect(move_button).to_be_enabled()
    move_button.click()
    assert calls["parents"] == [
        {"parent_path": "topics/projects"},
        {"parent_path": "topics/projects"},
    ]
    _json(calls["pending_parents"][1], {"ok": True})


def test_failed_topic_drop_restores_the_parent_and_position(
    page: Page, base_url: str
) -> None:
    calls = _mock_graph_home(page, parent_status=500)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    alpha = _node(page, "notes/alpha")
    projects = _node(page, "topics/projects")
    target = projects.bounding_box()
    assert target is not None

    _drag(
        page,
        alpha,
        {"x": target["x"] + target["width"] / 2, "y": target["y"] + target["height"] / 2},
    )
    expect(page.locator(".home-graph-notice")).to_contain_text("could not be moved")
    expect(alpha).to_have_attribute("data-x", "560")
    expect(alpha).to_have_attribute("data-y", "260")
    expect(page.get_by_role("button", name="Undo move")).to_be_hidden()
    assert calls["parents"] == [{"parent_path": "topics/projects"}]


def test_descendant_topic_drop_is_rejected_in_the_browser(
    page: Page, base_url: str
) -> None:
    calls = _mock_graph_home(page)
    _open_graph(page, base_url)
    _expand(page, "topics/work")
    work = _node(page, "topics/work")
    projects = _node(page, "topics/projects")
    target = projects.bounding_box()
    assert target is not None

    _drag(
        page,
        work,
        {"x": target["x"] + target["width"] / 2, "y": target["y"] + target["height"] / 2},
    )
    expect(page.locator(".home-graph-notice")).to_contain_text("cannot move")
    assert calls["parents"] == []


def test_self_topic_drop_is_rejected_in_the_browser(
    page: Page, base_url: str
) -> None:
    calls = _mock_graph_home(page)
    _open_graph(page, base_url)
    work = _node(page, "topics/work")
    box = work.bounding_box()
    assert box is not None
    center_x = box["x"] + box["width"] / 2
    center_y = box["y"] + box["height"] / 2

    page.mouse.move(center_x, center_y)
    page.mouse.down()
    page.mouse.move(center_x + 90, center_y + 70, steps=6)
    page.mouse.move(center_x + 8, center_y + 6, steps=6)
    page.mouse.up()

    expect(page.locator(".home-graph-notice")).to_contain_text("cannot move")
    assert calls["parents"] == []
    assert calls["positions"] == []


def test_pan_zoom_fit_and_keyboard_do_not_save_node_positions(
    page: Page, base_url: str
) -> None:
    calls = _mock_graph_home(page)
    _open_graph(page, base_url)
    canvas = page.locator(".home-graph-canvas")
    initial = page.locator(".home-graph-stage").get_attribute("style")

    box = canvas.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + 30, box["y"] + 80)
    before_wheel = page.locator(".home-graph-stage").get_attribute("style")
    page.mouse.wheel(0, -240)
    assert page.locator(".home-graph-stage").get_attribute("style") != before_wheel
    page.mouse.down()
    page.mouse.move(box["x"] + 100, box["y"] + 130, steps=8)
    page.mouse.up()
    assert page.locator(".home-graph-stage").get_attribute("style") != initial

    page.get_by_role("button", name="Zoom in").click()
    page.get_by_role("button", name="Zoom out").click()
    page.get_by_role("button", name="Fit graph").click()
    canvas.focus()
    canvas.press("ArrowRight")
    canvas.press("+")
    stored_viewport = json.loads(
        page.evaluate("localStorage.getItem('mycelos.home.graph.viewport')")
    )
    assert set(stored_viewport) == {"x", "y", "zoom"}
    assert calls["positions"] == []


def test_large_graph_does_not_create_all_node_buttons(
    page: Page, base_url: str
) -> None:
    nodes = [
        {"id": f"topics/large-{index}", "title": f"Large topic {index}", "type": "topic"}
        for index in range(5000)
    ]
    _mock_graph_home(
        page,
        graph={"nodes": nodes, "edges": [], "positions": {}, "stats": {"notes": 5000, "links": 0}},
    )
    _open_graph(page, base_url)

    expect(page.locator(".home-graph-node-main")).to_have_count(50)
    expect(page.locator(".home-graph-more")).to_contain_text("4950")
    assert page.locator(".home-main *").count() < 1000
    expect(page.locator(".home-tree-row")).to_have_count(0)


def test_graph_honors_reduced_motion(page: Page, base_url: str) -> None:
    page.emulate_media(reduced_motion="reduce")
    _mock_graph_home(page)
    _open_graph(page, base_url)

    transition = page.locator(".home-graph-stage").evaluate(
        "element => getComputedStyle(element).transitionDuration"
    )
    assert transition == "0s"
