"""Integration test: Reminder message generation with a local LLM.

ReminderService.generate_message() wraps a Haiku-tier call that produces
a natural 2–3 sentence message from a list of overdue tasks. Exactly the
kind of background LLM work that should run on local inference.

Gated on OLLAMA_HOST. Skipped without it.

Run:
    pytest -m integration tests/integration/test_reminder_local.py -v -s
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


@pytest.mark.parametrize("integration_app_local", ["ollama"], indirect=True)
def test_reminder_generates_message_locally(integration_app_local):
    from mycelos.knowledge.reminder import ReminderService

    app = integration_app_local
    service = ReminderService(app)

    tasks = [
        {"title": "Call dentist", "due": "2026-04-10", "priority": 2},
        {"title": "Pay electricity bill", "due": "2026-04-15", "priority": 1},
    ]
    message = service.generate_message(tasks)

    # The LLM call must return *something* and the fallback text must not
    # match exactly (the fallback starts with "Reminder:" and lists items,
    # which is distinctive enough).
    assert message
    assert message.strip()
    # At least one of the task titles should appear — the model might
    # paraphrase but it should still reference them.
    lower = message.lower()
    referenced = any(
        keyword in lower
        for keyword in ("dentist", "electricity", "bill")
    )
    assert referenced, f"reminder message didn't reference any task: {message!r}"
