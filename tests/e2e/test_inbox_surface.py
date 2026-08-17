"""E2E contract for the unified Inbox surface."""

from __future__ import annotations

import json
from urllib.parse import unquote, urlparse

from playwright.sync_api import Page, Route, expect


ENTRIES = [
    {
        "id": "suggestion:17",
        "kind": "merge",
        "class": "consequence",
        "title": "Possible duplicate",
        "why": "This merge needs a decision.",
        "actions": [{"id": "accept", "label": "Accept"}],
        "source": {"path": "notes/possible-duplicate"},
    },
    {
        "id": "reminder:notes/muller",
        "kind": "reminder",
        "class": "obligation",
        "title": "Müller filing",
        "why": "This reminder is due.",
        "actions": [
            {"id": "done", "label": "Done"},
            {"id": "snooze", "label": "Snooze"},
        ],
        "source": {"path": "notes/muller"},
    },
    {
        "id": "run:gmail",
        "kind": "failed_run",
        "class": "consequence",
        "title": "Gmail sync failed",
        "why": "The last sync did not finish.",
        "actions": [{"id": "retry", "label": "Try again"}],
        "source": {"routine_key": "gmail"},
    },
]


def _json(route: Route, payload: object) -> None:
    route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(payload),
    )


def test_inbox_lists_every_entry_and_runs_a_supported_action(
    page: Page, base_url: str
) -> None:
    page.route("**/api/inbox/count", lambda route: _json(route, {"count": 3}))
    page.route("**/api/inbox", lambda route: _json(route, {"entries": ENTRIES}))
    page.route(
        "**/api/organizer/suggestions/17/accept",
        lambda route: _json(route, {"ok": True}),
    )
    page.route(
        "**/api/knowledge/notes/**/remind",
        lambda route: _json(route, {"status": "reminder_set"}),
    )

    page.goto(f"{base_url}/pages/inbox.html", wait_until="networkidle")

    entries = page.locator(".review-entry")
    expect(entries).to_have_count(3)
    for entry in ENTRIES:
        expect(page.get_by_text(entry["title"], exact=True)).to_be_visible()

    reminder = entries.filter(has_text="Müller filing")
    expect(reminder.get_by_role("link", name="Open note")).to_have_attribute(
        "href", "/pages/knowledge.html?note=notes%2Fmuller"
    )

    with page.expect_request(
        lambda request: request.method == "POST"
        and urlparse(request.url).path
        == "/api/organizer/suggestions/17/accept"
    ):
        entries.filter(has_text="Possible duplicate").get_by_role(
            "button", name="Accept", exact=True
        ).click()

    with page.expect_request(
        lambda request: request.method == "POST"
        and unquote(urlparse(request.url).path)
        == "/api/knowledge/notes/notes/muller/remind"
    ) as request_info:
        reminder.get_by_role(
            "button", name="Snooze until tomorrow", exact=True
        ).click()

    snooze_body = request_info.value.post_data_json
    assert list(snooze_body) == ["when"]
    assert len(snooze_body["when"]) == 10
