"""POST /api/inbox/runs/{routine_key}/retry — the failed_run entry's exit.

A ``failed_run`` entry is synthesized from run rows, so it has no row to
update and no dismissal to persist. Its only real exit is running the
routine again, and the entry disappears when a run row says the routine
succeeded. That makes one property load-bearing above all others:

**The retry must write a run row.** ``POST /api/knowledge/ingest/{source}``
calls the ingest function directly and records nothing, so a retry built on
that route would leave a *successful* retry's entry standing forever — the
sticky entry Package 2's final review had to remove from ``unclassifiable``.
``test_a_successful_retry_clears_the_entry`` is the pin for that.

The second theme is the boundary. ``routine_key`` arrives from a URL path,
which the recorder never assumed: it documents itself as safe because the
only writer was a hardcoded dict. The handler validates the key against
``INGEST_SOURCES`` before it touches storage, the dispatch table, or an
audit payload.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mycelos.knowledge.inbox_model import InboxModel
from mycelos.scheduler.run_recorder import CAUSES, RunRecorder


@pytest.fixture
def api_client():
    """Mirrors the fixture in tests/test_inbox_api.py."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-retry-run-api"

        from mycelos.app import App
        from mycelos.setup import web_init
        from mycelos.gateway.server import create_app

        app_obj = App(data_dir)
        app_obj.initialize()
        web_init(app_obj, api_key="sk-ant-api03-FAKETESTKEYFORRETRY")

        fastapi_app = create_app(
            data_dir, no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True
        )
        client = TestClient(fastapi_app)
        yield client, fastapi_app.state.mycelos


# ---- helpers ------------------------------------------------------------


def _failed_sync(app, source: str, cause: str = CAUSES["source_failed"]) -> str:
    """One failed source_sync run row, written the way the scheduler writes it."""
    recorder = RunRecorder(app.storage)
    run_id = recorder.start("source_sync", source)
    recorder.fail(run_id, cause)
    return run_id


def _failed_run_entries(client) -> list[dict]:
    return [
        entry
        for entry in client.get("/api/inbox").json()["entries"]
        if entry["kind"] == "failed_run"
    ]


def _run_rows(app, source: str) -> list[dict]:
    return app.storage.fetchall(
        "SELECT id, kind, routine_key, status, error, artifacts "
        "FROM workflow_runs WHERE kind='source_sync' AND routine_key=? "
        "ORDER BY created_at ASC, id ASC",
        (source,),
    )


def _stub_source(monkeypatch, name: str, fn) -> None:
    """Replace one entry of the real dispatch table for the test's duration."""
    from mycelos.knowledge import connector_ingest

    monkeypatch.setitem(connector_ingest.INGEST_SOURCES, name, fn)


# ---- SF-1: a successful retry clears the entry --------------------------


def test_a_successful_retry_clears_the_entry(api_client, monkeypatch) -> None:
    """The pin for the whole endpoint.

    The entry is derived from run rows, so nothing but a *completed* run row
    can clear it. A retry that dispatched the ingest without recording — the
    shape ``POST /api/knowledge/ingest/{source}`` has — would return 200,
    import the data, and leave the entry and the badge standing for good.
    """
    client, app_obj = api_client
    _failed_sync(app_obj, "yt-summary")
    assert len(_failed_run_entries(client)) == 1
    assert client.get("/api/inbox/count").json()["count"] == 1

    _stub_source(monkeypatch, "yt-summary",
                 lambda app, **kw: {"created": 2, "updated": 1})

    resp = client.post("/api/inbox/runs/yt-summary/retry")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    assert _failed_run_entries(client) == []
    assert client.get("/api/inbox/count").json()["count"] == 0


def test_a_retry_writes_a_run_row(api_client, monkeypatch) -> None:
    """Prove the row exists rather than inferring it from the cleared entry."""
    client, app_obj = api_client
    _failed_sync(app_obj, "gmail")
    before = len(_run_rows(app_obj, "gmail"))

    _stub_source(monkeypatch, "gmail", lambda app, **kw: {"created": 4})
    client.post("/api/inbox/runs/gmail/retry")

    rows = _run_rows(app_obj, "gmail")
    assert len(rows) == before + 1
    latest = rows[-1]
    assert latest["kind"] == "source_sync"
    assert latest["routine_key"] == "gmail"
    assert latest["status"] == "completed"


def test_the_run_row_carries_counts_only(api_client, monkeypatch) -> None:
    """Constitution Rule 1 reaches the retry path through the recorder."""
    client, app_obj = api_client
    _failed_sync(app_obj, "gmail")

    _stub_source(monkeypatch, "gmail", lambda app, **kw: {
        "created": 1,
        "subject": "Rechnung Mueller GmbH",
        "sender": "buchhaltung@example.com",
    })
    client.post("/api/inbox/runs/gmail/retry")

    artifacts = _run_rows(app_obj, "gmail")[-1]["artifacts"]
    assert "created" in artifacts
    assert "Mueller" not in artifacts
    assert "example.com" not in artifacts
    assert "subject" not in artifacts


# ---- fail closed --------------------------------------------------------


def test_a_failed_retry_does_not_clear_the_entry(api_client, monkeypatch) -> None:
    """Constitution Rule 3. A retry that fails again is not a resolution."""
    client, app_obj = api_client
    _failed_sync(app_obj, "yt-summary")

    def _raises(app, **kw):
        raise ConnectionError("connector socket closed")

    _stub_source(monkeypatch, "yt-summary", _raises)
    resp = client.post("/api/inbox/runs/yt-summary/retry")

    assert resp.status_code == 502
    assert resp.json()["ok"] is False

    entries = _failed_run_entries(client)
    assert len(entries) == 1
    # The new attempt counts: two open failures since the last success.
    assert entries[0]["source"]["failure_count"] == 2
    assert client.get("/api/inbox/count").json()["count"] == 1


def test_a_connector_that_returns_an_error_is_not_a_success(
    api_client, monkeypatch
) -> None:
    """The connector answered and said no. That is a failed run, not a 200.

    ``auto_ingest_check`` treats this branch as a failure; the retry must
    agree, or the same outcome would clear the entry here and keep it there.
    """
    client, app_obj = api_client
    _failed_sync(app_obj, "gmail")

    _stub_source(monkeypatch, "gmail",
                 lambda app, **kw: {"error": "token expired for user@example.com"})
    resp = client.post("/api/inbox/runs/gmail/retry")

    assert resp.status_code == 502
    assert resp.json()["ok"] is False
    assert _run_rows(app_obj, "gmail")[-1]["status"] == "failed"
    assert len(_failed_run_entries(client)) == 1


def test_a_failed_retry_stores_a_fixed_cause_not_the_exception_text(
    api_client, monkeypatch
) -> None:
    """The exception message is built from the data that failed."""
    client, app_obj = api_client
    _failed_sync(app_obj, "gmail")

    def _raises(app, **kw):
        raise ValueError("could not parse note 'Kontoauszug Sparkasse 1234567890'")

    _stub_source(monkeypatch, "gmail", _raises)
    client.post("/api/inbox/runs/gmail/retry")

    stored = _run_rows(app_obj, "gmail")[-1]["error"]
    assert stored == CAUSES["response_unreadable"]
    assert "Kontoauszug" not in stored
    assert "1234567890" not in stored


def test_the_error_response_carries_no_connector_text(
    api_client, monkeypatch
) -> None:
    """The body is a rendering surface too — same rule as the column."""
    client, app_obj = api_client
    _failed_sync(app_obj, "gmail")

    def _raises(app, **kw):
        raise RuntimeError("mailbox 'Rechnungen 2026' rejected user@example.com")

    _stub_source(monkeypatch, "gmail", _raises)
    body = client.post("/api/inbox/runs/gmail/retry").text

    assert "Rechnungen" not in body
    assert "example.com" not in body


def test_a_retry_whose_row_cannot_be_closed_is_not_reported_as_success(
    api_client, monkeypatch
) -> None:
    """SF-4. The sync ran; the row did not close. That is not ``ok: true``.

    This handler's rule is that the run row is the deliverable, because a
    *completed* row is the only thing that clears the entry. The rule was
    applied at ``start`` (500 when the row cannot be opened) but abandoned at
    ``finish``, so a swallowed storage error answered 200 while the row sat
    ``running`` and the entry stayed put.
    """
    client, app_obj = api_client
    _failed_sync(app_obj, "gmail")

    _stub_source(monkeypatch, "gmail", lambda app, **kw: {"created": 3})
    monkeypatch.setattr(
        RunRecorder, "finish",
        lambda self, run_id, counts=None: (_ for _ in ()).throw(
            RuntimeError("disk full")
        ),
    )

    resp = client.post("/api/inbox/runs/gmail/retry")

    assert resp.status_code == 500
    assert resp.json()["ok"] is False
    # The row is genuinely stuck, which is why the response must say so.
    assert _run_rows(app_obj, "gmail")[-1]["status"] == "running"


def test_a_retry_whose_row_cannot_be_closed_keeps_the_entry(
    api_client, monkeypatch
) -> None:
    """The inbox already failed closed here. The response now agrees with it.

    Kept separate from the response assertion: the entry surviving is the
    Rule 3 property, and it must not regress if the status code ever changes.
    """
    client, app_obj = api_client
    _failed_sync(app_obj, "gmail")

    _stub_source(monkeypatch, "gmail", lambda app, **kw: {"created": 3})
    monkeypatch.setattr(
        RunRecorder, "finish",
        lambda self, run_id, counts=None: (_ for _ in ()).throw(
            RuntimeError("disk full")
        ),
    )
    client.post("/api/inbox/runs/gmail/retry")

    assert len(_failed_run_entries(client)) == 1
    assert client.get("/api/inbox/count").json()["count"] == 1


def test_the_row_close_failure_response_carries_no_connector_text(
    api_client, monkeypatch
) -> None:
    """The new 500 body is a rendering surface, same rule as the column."""
    client, app_obj = api_client
    _failed_sync(app_obj, "gmail")

    _stub_source(monkeypatch, "gmail", lambda app, **kw: {
        "created": 1,
        "subject": "Rechnung Mueller GmbH",
        "sender": "buchhaltung@example.com",
    })
    monkeypatch.setattr(
        RunRecorder, "finish",
        lambda self, run_id, counts=None: (_ for _ in ()).throw(
            RuntimeError("could not write /Users/stefan/data/mycelos.db")
        ),
    )

    body = client.post("/api/inbox/runs/gmail/retry").text
    assert "Mueller" not in body
    assert "example.com" not in body
    assert "/Users/" not in body


# ---- the boundary: routine_key comes from a URL path --------------------


def test_an_unknown_routine_key_is_404(api_client) -> None:
    client, _ = api_client
    resp = client.post("/api/inbox/runs/not-a-source/retry")
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


def test_a_workflow_routine_key_is_rejected(api_client) -> None:
    """A workflow run has its own path. It is not retryable here.

    It is not in ``INGEST_SOURCES``, so the allowlist rejects it for the
    same reason it rejects a typo — which is the point of an allowlist: a
    key is retryable because it is a known ingest source, never because it
    failed to look dangerous.
    """
    client, app_obj = api_client
    app_obj.workflow_run_manager.start(
        workflow_id=None, run_id="adhoc-retry-1", routine_key="daily-report",
    )
    app_obj.workflow_run_manager.fail("adhoc-retry-1", error="The workflow run failed.")

    resp = client.post("/api/inbox/runs/daily-report/retry")
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


def test_a_briefing_routine_key_is_rejected(api_client) -> None:
    """The briefing produces no entry and offers no retry route."""
    client, _ = api_client
    assert client.post("/api/inbox/runs/briefing/retry").status_code == 404


@pytest.mark.parametrize("hostile", [
    "../../etc/passwd",
    "..%2F..%2Fetc%2Fpasswd",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "gmail/../../secrets",
    "/etc/passwd",
    "..\\..\\windows\\system32",
    "gmail' OR '1'='1",
    "gmail'; DROP TABLE workflow_runs; --",
    "gmail\" UNION SELECT error FROM workflow_runs --",
    "gmail\x00.txt",
    "gmail\n\rInjected: header",
    "g" * 4096,
])
def test_a_hostile_routine_key_never_reaches_dispatch(
    api_client, monkeypatch, hostile: str
) -> None:
    """The allowlist runs before storage, dispatch and the audit payload.

    ``routine_key`` was unvalidated in the recorder, correctly, because its
    only writer was a hardcoded dict. A URL path changed that assumption, so
    the check belongs at this boundary — validate once, where the untrusted
    value enters.

    The dispatch sentinel is the assertion that matters, not the status
    code. Two layers reject these and they answer differently: the route
    is ``{routine_key}``, not ``{routine_key:path}``, so anything carrying
    a slash never matches the route at all and Starlette answers 405
    before any handler runs. What survives routing reaches the allowlist
    and gets a 404. Rejecting with a tidy 404 while still having called
    the ingest function would be a pass on status code alone, so the
    sentinel is what this test is really for.
    """
    client, app_obj = api_client
    dispatched: list[str] = []

    def _sentinel(app, **kw):
        dispatched.append("called")
        return {"created": 0}

    for name in ("gmail", "yt-summary"):
        _stub_source(monkeypatch, name, _sentinel)

    try:
        resp = client.post(f"/api/inbox/runs/{hostile}/retry")
    except Exception:
        # A NUL byte or a bare CR in a request target is rejected by the
        # transport before routing. Rejected earlier still is rejected.
        assert dispatched == []
        return

    assert resp.status_code in (400, 404, 405), resp.status_code
    assert dispatched == []
    # Nothing was written, under any key.
    assert app_obj.storage.fetchall(
        "SELECT id FROM workflow_runs WHERE kind='source_sync'"
    ) == []
    # The table is still there — the SQL metacharacter cases.
    assert app_obj.storage.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
    )


def test_the_workflow_runs_table_survives_sql_metacharacters(api_client) -> None:
    """A parameterised query is not the only defence, but prove it holds."""
    client, app_obj = api_client
    _failed_sync(app_obj, "gmail")

    client.post("/api/inbox/runs/gmail'; DROP TABLE workflow_runs; --/retry")

    assert len(_run_rows(app_obj, "gmail")) == 1
    assert len(_failed_run_entries(client)) == 1


# ---- a valid source with no failed run ----------------------------------


def test_a_valid_source_with_no_failed_run_still_retries(
    api_client, monkeypatch
) -> None:
    """Decision: retry runs the sync; it does not require a failure first.

    The alternative — 404 unless an open failure exists — was rejected. The
    endpoint's contract is "run this routine now", and the entry disappears
    as a consequence of the run succeeding, not as the endpoint's own
    effect. Requiring a failure would also make the retry racy: two clicks
    on one entry would give a 200 and then a 404 for the same correct
    action, and the second would read as an error to the user.

    It is also the honest reading of idempotence. Package 2 settled the same
    question for ``confirm``: the post-state is what the caller asked for,
    so it is a 200.
    """
    client, app_obj = api_client
    assert _failed_run_entries(client) == []

    _stub_source(monkeypatch, "gmail", lambda app, **kw: {"created": 0})
    resp = client.post("/api/inbox/runs/gmail/retry")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    rows = _run_rows(app_obj, "gmail")
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    # Running a healthy routine does not create an entry.
    assert _failed_run_entries(client) == []


# ---- audit --------------------------------------------------------------


def test_the_retry_is_audited_with_the_key_and_counts_only(
    api_client, monkeypatch
) -> None:
    """Constitution Rule 1: an audit payload carries keys and numbers."""
    client, app_obj = api_client
    _failed_sync(app_obj, "gmail")

    _stub_source(monkeypatch, "gmail", lambda app, **kw: {
        "created": 2,
        "subject": "Rechnung Mueller GmbH",
        "sender": "buchhaltung@example.com",
    })
    client.post("/api/inbox/runs/gmail/retry")

    rows = app_obj.storage.fetchall(
        "SELECT event_type, details FROM audit_events WHERE event_type=?",
        ("knowledge.run_retried",),
    )
    assert len(rows) == 1
    details = rows[0]["details"]
    assert "gmail" in details
    assert "created" in details
    assert "Mueller" not in details
    assert "example.com" not in details
    assert "subject" not in details


def test_a_failed_retry_is_audited_as_a_failure(api_client, monkeypatch) -> None:
    """A retry that failed must not leave an audit trail claiming success."""
    client, app_obj = api_client
    _failed_sync(app_obj, "gmail")

    def _raises(app, **kw):
        raise ConnectionError("socket closed for user@example.com")

    _stub_source(monkeypatch, "gmail", _raises)
    client.post("/api/inbox/runs/gmail/retry")

    rows = app_obj.storage.fetchall(
        "SELECT details FROM audit_events WHERE event_type=?",
        ("knowledge.run_retried",),
    )
    assert len(rows) == 1
    assert '"ok": false' in rows[0]["details"] or '"ok":false' in rows[0]["details"]
    assert "example.com" not in rows[0]["details"]


def test_a_rejected_key_is_not_audited(api_client) -> None:
    """Nothing happened, so nothing is recorded — under any key."""
    client, app_obj = api_client
    client.post("/api/inbox/runs/..%2F..%2Fetc%2Fpasswd/retry")
    client.post("/api/inbox/runs/not-a-source/retry")

    assert app_obj.storage.fetchall(
        "SELECT id FROM audit_events WHERE event_type=?",
        ("knowledge.run_retried",),
    ) == []
