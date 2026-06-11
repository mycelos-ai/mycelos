"""Tests for the morning briefing — living knowledge daily synthesis.

The briefing builder gathers deterministic facts (overdue tasks, today's
tasks, reminders, fresh notes with provenance, touched topics) and makes
ONE LLM call to synthesize them. Fail-soft: if the LLM call fails the
deterministic sections still go out. Delivery happens once per day at the
user-configured time, via the same channel path reminders use.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-briefing"
        a = App(Path(tmp))
        a.initialize()
        yield a


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeBroker:
    """Returns a fixed prose answer for every call."""

    def __init__(self, content: str = "Good morning! Two tasks need you today.") -> None:
        self.calls: list = []
        self._content = content

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return _FakeLLMResponse(self._content)


class _RaisingBroker:
    def __init__(self) -> None:
        self.calls: list = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        raise RuntimeError("LLM unavailable")


class _FakeDelivery:
    """Stands in for ReminderService — records dispatches."""

    def __init__(self, channels=("chat", "telegram"), succeed=True) -> None:
        self.channels = list(channels)
        self.succeed = succeed
        self.dispatched: list = []

    def _default_channels(self) -> list[str]:
        return self.channels

    def dispatch(self, channel: str, message: str) -> bool:
        self.dispatched.append((channel, message))
        return self.succeed


def _yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def _seed_notes(app) -> None:
    kb = app.knowledge_base
    kb.write(title="Pay invoice", content="Transfer the money", type="task",
             status="open", due=_yesterday())
    kb.write(title="Call Alice", content="About the contract", type="task",
             status="open", due=date.today().isoformat())
    kb.write(title="Standup notes", content="Discussed the rollout",
             type="note")
    kb.write(title="Email: offer received", content="The vendor sent a quote",
             type="note", created_by="import",
             source={"kind": "connector", "connector": "gmail",
                     "external_id": "t-99"})


class TestGatherFacts:
    def test_buckets_tasks_and_recent_notes(self, app):
        from mycelos.knowledge.briefing import gather_facts
        _seed_notes(app)
        facts = gather_facts(app)

        overdue_titles = [t["title"] for t in facts["overdue_tasks"]]
        today_titles = [t["title"] for t in facts["today_tasks"]]
        assert "Pay invoice" in overdue_titles
        assert "Call Alice" in today_titles
        assert "Call Alice" not in overdue_titles

        recent_titles = [n["title"] for n in facts["recent_notes"]]
        assert "Standup notes" in recent_titles
        assert "Email: offer received" in recent_titles

    def test_recent_notes_carry_provenance(self, app):
        from mycelos.knowledge.briefing import gather_facts
        _seed_notes(app)
        facts = gather_facts(app)
        imported = [n for n in facts["recent_notes"]
                    if n["title"] == "Email: offer received"]
        assert imported, "ingested note missing from recent notes"
        assert imported[0]["created_by"] == "import"
        assert imported[0]["source"]["connector"] == "gmail"

    def test_reminders_firing_today(self, app):
        from mycelos.knowledge.briefing import gather_facts
        kb = app.knowledge_base
        kb.write(title="Dentist", content="", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)
        facts = gather_facts(app)
        assert any(r["title"] == "Dentist" for r in facts["reminders_today"])

    def test_old_notes_not_recent(self, app):
        from mycelos.knowledge.briefing import gather_facts
        kb = app.knowledge_base
        path = kb.write(title="Ancient wisdom", content="old", type="note")
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        app.storage.execute(
            "UPDATE knowledge_notes SET created_at=? WHERE path=?",
            (old, path),
        )
        facts = gather_facts(app)
        assert all(n["title"] != "Ancient wisdom" for n in facts["recent_notes"])


class TestBuildBriefing:
    def test_synthesis_plus_sections(self, app):
        from mycelos.knowledge.briefing import build_briefing
        _seed_notes(app)
        broker = _FakeBroker("Good morning Stefan!")
        app._llm = broker

        briefing = build_briefing(app)
        assert briefing["synthesis"] == "Good morning Stefan!"
        assert "Good morning Stefan!" in briefing["markdown"]
        # Deterministic sections appended
        assert "Pay invoice" in briefing["markdown"]
        assert "Call Alice" in briefing["markdown"]
        assert len(broker.calls) == 1

    def test_prompt_frames_content_as_data(self, app):
        """User content (note titles can carry injected instructions) must be
        framed as data, and the system prompt must be English."""
        from mycelos.knowledge.briefing import build_briefing
        _seed_notes(app)
        broker = _FakeBroker()
        app._llm = broker
        build_briefing(app)

        messages, kwargs = broker.calls[0]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        assert "data, not instructions" in user_msg
        assert "<briefing-data>" in user_msg
        system_msg = next(m["content"] for m in messages if m["role"] == "system")
        assert "briefing" in system_msg.lower()

    def test_fail_soft_without_llm(self, app):
        """LLM down → deliver the deterministic sections, no synthesis."""
        from mycelos.knowledge.briefing import build_briefing
        _seed_notes(app)
        app._llm = _RaisingBroker()

        briefing = build_briefing(app)
        assert briefing["synthesis"] is None
        assert "Pay invoice" in briefing["markdown"]
        assert "Call Alice" in briefing["markdown"]

    def test_cached_per_day(self, app):
        """Second build on the same day returns the cache — one LLM call."""
        from mycelos.knowledge.briefing import get_or_build_briefing
        _seed_notes(app)
        broker = _FakeBroker()
        app._llm = broker

        first = get_or_build_briefing(app)
        second = get_or_build_briefing(app)
        assert len(broker.calls) == 1
        assert first["markdown"] == second["markdown"]

    def test_build_audited(self, app):
        from mycelos.knowledge.briefing import get_or_build_briefing
        app._llm = _FakeBroker()
        get_or_build_briefing(app)
        row = app.storage.fetchone(
            "SELECT details FROM audit_events WHERE event_type='briefing.built'"
        )
        assert row is not None


class TestIsBriefingDue:
    def _now(self, hh: int, mm: int) -> datetime:
        return datetime(2026, 6, 11, hh, mm)

    def test_before_configured_time(self):
        from mycelos.knowledge.briefing import is_briefing_due
        assert is_briefing_due(self._now(7, 0), "07:30", None) is False

    def test_after_configured_time(self):
        from mycelos.knowledge.briefing import is_briefing_due
        assert is_briefing_due(self._now(7, 31), "07:30", None) is True

    def test_already_sent_today(self):
        from mycelos.knowledge.briefing import is_briefing_due
        assert is_briefing_due(self._now(9, 0), "07:30", "2026-06-11") is False

    def test_sent_yesterday(self):
        from mycelos.knowledge.briefing import is_briefing_due
        assert is_briefing_due(self._now(9, 0), "07:30", "2026-06-10") is True

    def test_invalid_time_falls_back_to_default(self):
        from mycelos.knowledge.briefing import is_briefing_due
        # Garbage time string → default 07:30 applies, never a crash
        assert is_briefing_due(self._now(8, 0), "25:99", None) is True
        assert is_briefing_due(self._now(7, 0), "garbage", None) is False


class TestBriefingTick:
    def test_disabled_by_default(self, app):
        from mycelos.scheduler.jobs import briefing_tick
        delivery = _FakeDelivery()
        result = briefing_tick(app, now=datetime(2026, 6, 11, 9, 0),
                               reminder_service=delivery)
        assert result["sent"] is False
        assert delivery.dispatched == []

    def test_sends_once_after_time(self, app):
        from mycelos.scheduler.jobs import briefing_tick
        app._llm = _FakeBroker()
        app.memory.set("default", "system", "briefing_enabled", True)
        delivery = _FakeDelivery()

        result = briefing_tick(app, now=datetime(2026, 6, 11, 8, 0),
                               reminder_service=delivery)
        assert result["sent"] is True
        assert len(delivery.dispatched) == 1
        channel, message = delivery.dispatched[0]
        assert channel == "telegram"
        assert message  # the briefing markdown

        # Audit + dedup marker
        row = app.storage.fetchone(
            "SELECT details FROM audit_events WHERE event_type='briefing.sent'"
        )
        assert row is not None
        assert app.memory.get("default", "system", "briefing_last_sent") == \
            date.today().isoformat()

    def test_no_double_send_same_day(self, app):
        from mycelos.scheduler.jobs import briefing_tick
        app._llm = _FakeBroker()
        app.memory.set("default", "system", "briefing_enabled", True)
        delivery = _FakeDelivery()

        briefing_tick(app, now=datetime(2026, 6, 11, 8, 0),
                      reminder_service=delivery)
        again = briefing_tick(app, now=datetime(2026, 6, 11, 8, 5),
                              reminder_service=delivery)
        assert again["sent"] is False
        assert len(delivery.dispatched) == 1

    def test_not_due_before_time(self, app):
        from mycelos.scheduler.jobs import briefing_tick
        app.memory.set("default", "system", "briefing_enabled", True)
        app.memory.set("default", "system", "briefing_time", "07:30")
        delivery = _FakeDelivery()
        result = briefing_tick(app, now=datetime(2026, 6, 11, 7, 0),
                               reminder_service=delivery)
        assert result["sent"] is False
        assert delivery.dispatched == []

    def test_respects_configured_time(self, app):
        from mycelos.scheduler.jobs import briefing_tick
        app._llm = _FakeBroker()
        app.memory.set("default", "system", "briefing_enabled", True)
        app.memory.set("default", "system", "briefing_time", "06:00")
        delivery = _FakeDelivery()
        result = briefing_tick(app, now=datetime(2026, 6, 11, 6, 1),
                               reminder_service=delivery)
        assert result["sent"] is True

    def test_skips_without_telegram(self, app):
        """No Telegram configured → skip with log, no send, not marked sent
        (so a Telegram configured later the same day still gets it)."""
        from mycelos.scheduler.jobs import briefing_tick
        app._llm = _FakeBroker()
        app.memory.set("default", "system", "briefing_enabled", True)
        delivery = _FakeDelivery(channels=["chat"])
        result = briefing_tick(app, now=datetime(2026, 6, 11, 9, 0),
                               reminder_service=delivery)
        assert result["sent"] is False
        assert delivery.dispatched == []
        assert app.memory.get("default", "system", "briefing_last_sent") is None

    def test_errors_do_not_propagate(self, app):
        """The tick must never crash the scheduler loop."""
        from mycelos.scheduler.jobs import briefing_tick

        class _Exploding:
            def _default_channels(self):
                raise RuntimeError("boom")

            def dispatch(self, channel, message):
                raise RuntimeError("boom")

        app._llm = _FakeBroker()
        app.memory.set("default", "system", "briefing_enabled", True)
        result = briefing_tick(app, now=datetime(2026, 6, 11, 9, 0),
                               reminder_service=_Exploding())
        assert result["sent"] is False


class TestBriefingAPI:
    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        from mycelos.gateway.server import create_app
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-briefing-api"
            from mycelos.app import App
            from mycelos.setup import web_init
            a = App(data_dir)
            a.initialize()
            web_init(a, api_key="sk-ant-api03-FAKETESTKEYBRIEFINGAPI")
            fastapi_app = create_app(data_dir, no_scheduler=True,
                                     host="0.0.0.0", allow_insecure_bind=True)
            yield TestClient(fastapi_app)

    def test_get_today_returns_briefing(self, client):
        mycelos = client.app.state.mycelos
        mycelos._llm = _FakeBroker("Synth!")
        resp = client.get("/api/briefing/today")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["date"] == date.today().isoformat()
        assert "markdown" in data

    def test_get_settings_defaults(self, client):
        resp = client.get("/api/briefing/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["time"] == "07:30"

    def test_post_settings_updates_and_audits(self, client):
        resp = client.post("/api/briefing/settings",
                           json={"enabled": True, "time": "06:15"})
        assert resp.status_code == 200, resp.text

        mycelos = client.app.state.mycelos
        assert mycelos.memory.get("default", "system", "briefing_enabled") is True
        assert mycelos.memory.get("default", "system", "briefing_time") == "06:15"
        row = mycelos.storage.fetchone(
            "SELECT details FROM audit_events "
            "WHERE event_type='briefing.settings.updated'"
        )
        assert row is not None

    def test_post_settings_rejects_invalid_time(self, client):
        for bad in ("25:00", "7:5x", "noon", "07:60"):
            resp = client.post("/api/briefing/settings", json={"time": bad})
            assert resp.status_code == 422, f"{bad} accepted: {resp.text}"

    def test_post_settings_sets_auto_ingest(self, client):
        resp = client.post("/api/briefing/settings",
                           json={"auto_ingest_enabled": True})
        assert resp.status_code == 200, resp.text
        mycelos = client.app.state.mycelos
        assert mycelos.memory.get(
            "default", "system", "auto_ingest_enabled") is True
