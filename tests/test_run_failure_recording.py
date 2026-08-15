"""A run that ends for any reason leaves a row that says so.

Package 3, Task 2. Three holes let a run end without a truthful record:

1. An exception inside ``WorkflowAgent.execute`` propagated out and the row
   stayed ``running`` forever — ``fail()`` had exactly one call site.
2. The orphan sweep stamped "gateway restarted" over rows whose real cause
   was a crash. A wrong cause sends the reader to the wrong place.
3. A scheduled task's failure was a log line only; ``scheduled_tasks`` has no
   outcome column, so the run row must be the durable trace.

Plus the fourth: ``run_manager.start()`` failing left the run with no row.

The stored ``error`` is user-facing text — a cause, never a traceback, a file
path or the content that failed to parse (Constitution Rule 1).
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App
from mycelos.scheduler.jobs import check_scheduled_workflows, sweep_orphaned_workflow_runs
from mycelos.workflows.agent import WorkflowAgent


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-run-failures"
        a = App(Path(tmp))
        a.initialize()
        yield a


def _mock_llm_response(content: str = "Done.", tool_calls=None) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.tool_calls = tool_calls
    resp.total_tokens = 50
    resp.cost = 0.0023
    return resp


def _register(app: App, wf_id: str, plan: str = "Do the thing.") -> dict:
    app.workflow_registry.register(
        wf_id, wf_id,
        steps=[{"id": "s1"}],
        plan=plan,
        allowed_tools=["search_web"],
    )
    return app.workflow_registry.get(wf_id)


def _make_due(app: App, task_id: str) -> None:
    app.storage.execute(
        "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), task_id),
    )


# A message shaped like the worst case: a traceback fragment, an absolute file
# path, and note content the user wrote. None of it may reach the column.
NOTE_CONTENT = "Meeting notes: salary negotiation with Anna, target 92000 EUR"
LEAKY_MESSAGE = (
    f'Failed to parse note at /Users/stefan/data/knowledge/notes/2026-08-14.md: '
    f'"{NOTE_CONTENT}"'
)


def _assert_no_personal_data(error_text: str) -> None:
    """The stored cause must name what failed, never what the data contained."""
    assert error_text, "a failed run must state a cause"
    assert "Traceback" not in error_text
    assert 'File "' not in error_text
    assert "/Users/" not in error_text
    assert ".md" not in error_text
    assert "Anna" not in error_text
    assert "92000" not in error_text
    assert NOTE_CONTENT not in error_text


# --- The cause formatter -----------------------------------------------------


@pytest.mark.parametrize(
    "exc, must_not_contain",
    [
        (ValueError(LEAKY_MESSAGE), ["Anna", "92000", "/Users", ".md"]),
        (OSError("[Errno 2] No such file: '/srv/secrets/key.pem'"), ["secrets", ".pem"]),
        (RuntimeError('Traceback (most recent call last):\n  File "/srv/x.py", line 3'),
         ["Traceback", "/srv", 'File "']),
        (ValueError("mail to nothegger@vorfina.de bounced"), ["@vorfina.de"]),
        (RuntimeError("token=sk-ant-abcdefghij0123456789ABCDEFGHIJ rejected"),
         ["sk-ant-abcdefghij"]),
        (ValueError("IBAN DE89370400440532013000 rejected"), ["DE89370400440532013000"]),
        (ValueError("amount 92000 exceeded"), ["92000"]),
        (ValueError("fetch https://mail.example.com/inbox/42 failed"), ["example.com"]),
    ],
)
def test_describe_exception_strips_content(exc, must_not_contain) -> None:
    """The cause keeps the exception type and drops content-bearing spans."""
    from mycelos.workflows.run_cause import describe_exception

    cause = describe_exception(exc)
    assert cause.startswith(type(exc).__name__)
    for fragment in must_not_contain:
        assert fragment not in cause, f"{fragment!r} leaked into {cause!r}"


@pytest.mark.parametrize(
    "message, leaked",
    [
        # An IBAN is written in groups of four everywhere a human sees one:
        # a bank export, a CSV, a note, every German banking UI. Matching
        # only the solid token catches the form nobody writes.
        ("Buchung IBAN DE89370400440532013000 abgelehnt", "DE89370400440532013000"),
        ("Buchung IBAN DE89 3704 0044 0532 0130 00 abgelehnt", "3704"),
        ("Buchung IBAN DE89-3704-0044-0532-0130-00 abgelehnt", "3704"),
        ("IBAN DE89 3704-0044 0532 0130.00 gemischt", "3704"),
        ("AT61 1904 3002 3457 3201 rejected", "1904"),
        ("transfer to NL91ABNA0417164300 failed", "ABNA"),
        # The same defect class, other identifier families.
        ("Karte 4111 1111 1111 1111 abgelehnt", "4111"),
        ("Steuernummer 123/456/78901 ungueltig", "78901"),
        ("USt-IdNr DE 123 456 789 ungueltig", "456"),
        ("Versicherungsnummer 12 345678 A 901 ungueltig", "345678"),
        ("Telefon 089 12345678 nicht erreichbar", "12345678"),
    ],
)
def test_describe_exception_strips_grouped_identifiers(message, leaked) -> None:
    """A structured identifier leaks in its human-readable form, or not at all.

    The first case is the solid token the original test pinned. The rest are
    the separator styles that actually occur — each one leaked verbatim
    before this was fixed.
    """
    from mycelos.workflows.run_cause import describe_exception

    cause = describe_exception(ValueError(message))
    assert leaked not in cause, f"{leaked!r} leaked into {cause!r}"


@pytest.mark.parametrize(
    "message",
    [
        # Balanced, the easy case.
        'parse failed at "Gehalt Anna Mueller 9200 EUR"',
        "parse failed at 'Gehalt Anna Mueller 9200 EUR'",
        "parse failed at `Gehalt Anna Mueller 9200 EUR`",
        # Unbalanced: truncated upstream, or a payload that runs to the end.
        # We cannot know where the payload stops, so the tail must go.
        'parse failed at "Gehalt Anna Mueller 9200 EUR im Meeting',
        "parse failed at 'Gehalt Anna Mueller 9200 EUR im Meeting",
        "parse failed at `Gehalt Anna Mueller 9200 EUR im Meeting",
        # Nested, either way round.
        'nested "outer Anna Mueller inner" span',
        "nested 'outer \"Anna Mueller\" inner' span",
        # German and French typographic quotes: any message built from German
        # text uses these, and they were not in the pattern set at all.
        "note „Gehalt Anna Mueller 9200“ ungueltig",
        "note “Gehalt Anna Mueller 9200” ungueltig",
        "note «Gehalt Anna Mueller 9200» ungueltig",
        "note ‚Gehalt Anna Mueller 9200‘ ungueltig",
    ],
)
def test_describe_exception_strips_quoted_spans(message) -> None:
    """A quoted span never survives, closed or not, in any quote style."""
    from mycelos.workflows.run_cause import describe_exception

    cause = describe_exception(ValueError(message))
    assert "Anna" not in cause, f"content leaked into {cause!r}"
    assert "Mueller" not in cause
    assert "9200" not in cause


def test_quoted_span_redaction_does_not_eat_prose() -> None:
    """A possessive apostrophe is prose, not a quote.

    Treating every apostrophe as an opening delimiter destroyed the cause and
    leaked the word before it: "Anna's note about Stefan's salary" became
    "Anna<value>s salary". Only an apostrophe at a word boundary opens a span.
    """
    from mycelos.workflows.run_cause import describe_exception

    cause = describe_exception(ValueError("the host's certificate has expired"))
    assert cause == "ValueError: the host's certificate has expired"


def test_describe_exception_keeps_a_useful_cause() -> None:
    """Redaction must not reduce every cause to the bare type."""
    from mycelos.workflows.run_cause import describe_exception

    cause = describe_exception(RuntimeError("connection to the mail host was refused"))
    assert cause == "RuntimeError: connection to the mail host was refused"


def test_describe_exception_caps_length() -> None:
    """A long message is a payload, not a cause — it is truncated."""
    from mycelos.workflows.run_cause import MAX_CAUSE_LENGTH, describe_exception

    cause = describe_exception(ValueError("word " * 200))
    assert len(cause) <= MAX_CAUSE_LENGTH + len("ValueError: ") + 2


def test_describe_exception_without_message() -> None:
    """An exception with no message still yields its type."""
    from mycelos.workflows.run_cause import describe_exception

    assert describe_exception(RuntimeError()) == "RuntimeError"


# --- Hole 1: exceptions inside execute() ------------------------------------


def test_llm_exception_records_failed_run(app: App) -> None:
    """An exception from the LLM call leaves status='failed', not 'running'."""
    wf = _register(app, "llm-boom-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-llm-boom")

    with patch.object(app.llm, "complete", side_effect=RuntimeError("provider unreachable")):
        with pytest.raises(RuntimeError):
            agent.execute()

    run = app.workflow_run_manager.get("run-llm-boom")
    assert run is not None, "the run must leave a row"
    assert run["status"] == "failed"
    assert run["error"]
    assert "RuntimeError" in run["error"]


def test_tool_exception_records_failed_run(app: App) -> None:
    """An exception raised out of a tool call also records the failure."""
    wf = _register(app, "tool-boom-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-tool-boom")

    mock_resp = _mock_llm_response(tool_calls=[{
        "id": "tc1", "function": {"name": "search_web", "arguments": "{}"}
    }])

    # _execute_tool swallows tool errors by design; the hole is an exception
    # escaping the surrounding loop (audit, progress callback, conversation).
    with patch.object(app.llm, "complete", return_value=mock_resp), \
            patch.object(agent, "_execute_tool", side_effect=RuntimeError("tool crashed")):
        with pytest.raises(RuntimeError):
            agent.execute()

    run = app.workflow_run_manager.get("run-tool-boom")
    assert run is not None
    assert run["status"] == "failed"
    assert "RuntimeError" in run["error"]


def test_exception_still_propagates_to_caller(app: App) -> None:
    """Recording the failure must not swallow the exception.

    jobs.py audits it and chat surfaces it — a swallowed exception turns a
    visible failure back into silence.
    """
    wf = _register(app, "propagate-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-propagate")

    with patch.object(app.llm, "complete", side_effect=ValueError("bad model id")):
        with pytest.raises(ValueError, match="bad model id"):
            agent.execute()


def test_recorded_error_carries_no_personal_data(app: App) -> None:
    """An exception whose message carries note content must not leak it."""
    wf = _register(app, "leaky-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-leaky")

    with patch.object(app.llm, "complete", side_effect=ValueError(LEAKY_MESSAGE)):
        with pytest.raises(ValueError):
            agent.execute()

    run = app.workflow_run_manager.get("run-leaky")
    assert run["status"] == "failed"
    _assert_no_personal_data(run["error"])
    # It must still say what kind of failure it was.
    assert "ValueError" in run["error"]


def test_recording_failure_does_not_hide_the_original_exception(app: App) -> None:
    """If recording itself fails, the caller still sees the original error."""
    wf = _register(app, "double-fault-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-double-fault")

    with patch.object(app.llm, "complete", side_effect=RuntimeError("original")), \
            patch.object(app.workflow_run_manager, "fail",
                         side_effect=RuntimeError("recording broke")):
        with pytest.raises(RuntimeError, match="original"):
            agent.execute()


# --- SF-1: an interrupt is an ending too ------------------------------------


@pytest.mark.parametrize(
    "exc_class", [KeyboardInterrupt, SystemExit, GeneratorExit],
)
def test_base_exception_records_failed_run(app: App, exc_class) -> None:
    """Ctrl-C ends the run, so the row must say the run ended.

    `except Exception` does not catch these. Each one left the row 'running'
    with error=None — the exact state the invariant forbids — and only the
    orphan sweep, which runs at gateway start, would ever relabel it.
    """
    wf = _register(app, f"interrupt-wf-{exc_class.__name__}")
    run_id = f"run-interrupt-{exc_class.__name__}"
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id=run_id)

    with patch.object(app.llm, "complete", side_effect=exc_class()):
        with pytest.raises(exc_class):
            agent.execute()

    run = app.workflow_run_manager.get(run_id)
    assert run is not None, "an interrupted run must leave a row"
    assert run["status"] == "failed", "an interrupt ends the run"
    assert exc_class.__name__ in run["error"], "the cause must be honest"


def test_keyboard_interrupt_still_interrupts(app: App) -> None:
    """Recording must not turn a Ctrl-C into a swallowed exception."""
    wf = _register(app, "interrupt-propagate-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-interrupt-propagate")

    with patch.object(app.llm, "complete", side_effect=KeyboardInterrupt()):
        with pytest.raises(KeyboardInterrupt):
            agent.execute()


# --- SF-3: the recording path is guarded as a whole -------------------------


def test_sanitizer_failure_still_records_the_run(app: App) -> None:
    """If the sanitizer raises, the row still ends 'failed' with the type.

    `describe_exception` used to be called outside the guard, so an error in
    the sanitizer left the row 'running'. The exception type always renders.
    """
    wf = _register(app, "sanitizer-boom-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-sanitizer-boom")

    with patch.object(app.llm, "complete", side_effect=ValueError("original")), \
            patch("mycelos.workflows.agent.describe_exception",
                  side_effect=RuntimeError("sanitizer broke")):
        with pytest.raises(ValueError, match="original"):
            agent.execute()

    run = app.workflow_run_manager.get("run-sanitizer-boom")
    assert run is not None
    assert run["status"] == "failed", "a broken sanitizer must not leave the row running"
    assert run["error"] == "ValueError", "the type is the fallback cause"


def test_recorder_failure_is_escalated_and_audited(app: App) -> None:
    """A run that ends with no row is loud, not a warning nobody greps.

    The start of a run is fatal when it cannot be recorded. The end cannot
    raise — that would replace the real cause with a storage error — so it
    escalates instead: logger.error plus an audit event.
    """
    wf = _register(app, "escalate-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-escalate")

    with patch.object(app.llm, "complete", side_effect=RuntimeError("original")), \
            patch.object(app.workflow_run_manager, "fail",
                         side_effect=RuntimeError("storage down")):
        with pytest.raises(RuntimeError, match="original"):
            agent.execute()

    events = app.audit.query(event_type="workflow.run_unrecorded")
    assert len(events) == 1, "an unrecorded ending must be visible somewhere"
    details = events[0]["details"]
    if isinstance(details, str):
        import json as _json
        details = _json.loads(details)
    assert details["run_id"] == "run-escalate"


def test_max_rounds_path_unchanged(app: App) -> None:
    """The one path that already recorded must keep recording as before."""
    wf = _register(app, "max-rounds-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-max-rounds", max_rounds=1)

    mock_resp = _mock_llm_response(tool_calls=[{
        "id": "tc1", "function": {"name": "search_web", "arguments": "{}"}
    }])
    with patch.object(app.llm, "complete", return_value=mock_resp), \
            patch.object(agent, "_execute_tool", return_value="{}"):
        result = agent.execute()

    assert result.status == "failed"
    assert "Max rounds" in result.error
    run = app.workflow_run_manager.get("run-max-rounds")
    assert run["status"] == "failed"
    assert "Max rounds (1) exceeded" == run["error"]


def test_completed_run_is_untouched_by_failure_recording(app: App) -> None:
    """The success path must not be re-marked by the new wrapper."""
    wf = _register(app, "happy-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-happy")

    with patch.object(app.llm, "complete", return_value=_mock_llm_response("Hello!")):
        result = agent.execute()

    assert result.status == "completed"
    run = app.workflow_run_manager.get("run-happy")
    assert run["status"] == "completed"
    assert not run["error"]


# --- Hole 4: run_manager.start() failing ------------------------------------


def test_unregistered_workflow_still_records_a_run(app: App) -> None:
    """An ad-hoc workflow_def has no `workflows` row — the run is still recorded.

    Making start() fatal would otherwise break every caller that builds a
    workflow definition in code. The run gets a null workflow_id and keeps
    its identity in routine_key.
    """
    adhoc = {"id": "never-registered", "plan": "Do it.", "allowed_tools": []}
    agent = WorkflowAgent(app=app, workflow_def=adhoc, run_id="run-adhoc")

    with patch.object(app.llm, "complete", return_value=_mock_llm_response("Done.")):
        result = agent.execute()

    assert result.status == "completed"
    run = app.workflow_run_manager.get("run-adhoc")
    assert run is not None, "an unregistered workflow must still leave a row"
    assert run["workflow_id"] is None
    assert run["routine_key"] == "never-registered"
    assert run["status"] == "completed"


def test_start_failure_aborts_the_run(app: App) -> None:
    """If the run row cannot be written, the run must not execute unrecorded.

    A run nobody can account for is exactly what this package exists to
    prevent, and every later surface (inbox, history) reads rows only.
    """
    wf = _register(app, "no-row-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-no-row")

    complete = MagicMock(return_value=_mock_llm_response("Hello!"))
    with patch.object(app.workflow_run_manager, "start",
                      side_effect=RuntimeError("disk full")), \
            patch.object(app.llm, "complete", complete):
        with pytest.raises(RuntimeError):
            agent.execute()

    assert complete.call_count == 0, "no LLM call may happen without a run row"
    assert app.workflow_run_manager.get("run-no-row") is None


# --- Hole 2: the orphan sweep -----------------------------------------------


def test_orphan_sweep_does_not_claim_a_restart(app: App) -> None:
    """A row found 'running' means "we cannot tell", not "gateway restarted"."""
    _register(app, "orphan-wf")
    app.workflow_run_manager.start(workflow_id="orphan-wf", run_id="run-orphan")

    count = sweep_orphaned_workflow_runs(app)
    assert count == 1

    run = app.workflow_run_manager.get("run-orphan")
    assert run["status"] == "failed"
    error = run["error"]
    assert "restart" not in error.lower(), "the sweep cannot know a restart happened"
    assert "gateway" not in error.lower()
    # It must still say something a human can act on.
    assert "unknown" in error.lower() or "did not finish" in error.lower()


def test_orphan_sweep_leaves_recorded_failures_alone(app: App) -> None:
    """A run that already recorded its real cause keeps it."""
    _register(app, "recorded-wf")
    app.workflow_run_manager.start(workflow_id="recorded-wf", run_id="run-recorded")
    app.workflow_run_manager.fail("run-recorded", error="RuntimeError: provider unreachable")

    sweep_orphaned_workflow_runs(app)

    run = app.workflow_run_manager.get("run-recorded")
    assert run["error"] == "RuntimeError: provider unreachable"


# --- Hole 3: scheduled tasks -------------------------------------------------


def test_scheduled_workflow_failure_leaves_a_run_row(app: App) -> None:
    """A scheduled workflow that fails leaves a durable trace, not a log line."""
    _register(app, "sched-fail-wf")
    task_id = app.schedule_manager.add("sched-fail-wf", "*/5 * * * *")
    _make_due(app, task_id)

    with patch.object(app.llm, "complete", side_effect=RuntimeError("provider unreachable")):
        executed = check_scheduled_workflows(app)

    assert task_id in executed, "the task must still be marked to avoid a retry loop"
    runs = app.workflow_run_manager.list_runs(workflow_id="sched-fail-wf")
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    _assert_no_personal_data(runs[0]["error"])


def test_scheduled_workflow_failure_without_agent_row(app: App) -> None:
    """Even when the agent never wrote a row, the failure is recorded."""
    _register(app, "sched-norow-wf")
    task_id = app.schedule_manager.add("sched-norow-wf", "*/5 * * * *")
    _make_due(app, task_id)

    with patch("mycelos.workflows.agent.WorkflowAgent") as MockAgent:
        MockAgent.return_value.execute.side_effect = RuntimeError("boom")
        executed = check_scheduled_workflows(app)

    assert task_id in executed
    runs = app.workflow_run_manager.list_runs(workflow_id="sched-norow-wf")
    assert len(runs) == 1, "the failure must leave exactly one row"
    assert runs[0]["status"] == "failed"
    assert runs[0]["kind"] == "scheduled_task"
    assert runs[0]["error"]


def test_scheduled_workflow_non_completed_status_records_failure(app: App) -> None:
    """A non-completed result status is a failure with a durable trace."""
    _register(app, "sched-status-wf")
    task_id = app.schedule_manager.add("sched-status-wf", "*/5 * * * *")
    _make_due(app, task_id)

    from mycelos.workflows.agent import WorkflowAgentResult

    with patch("mycelos.workflows.agent.WorkflowAgent") as MockAgent:
        MockAgent.return_value.execute.return_value = WorkflowAgentResult(
            status="failed", error="Max rounds (20) exceeded",
        )
        check_scheduled_workflows(app)

    runs = app.workflow_run_manager.list_runs(workflow_id="sched-status-wf")
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert "Max rounds" in runs[0]["error"]


def test_scheduled_workflow_without_plan_records_failure(app: App) -> None:
    """A workflow with no plan no longer vanishes silently."""
    app.workflow_registry.register(
        "noplan-wf", "No Plan WF", steps=[{"id": "s1"}], allowed_tools=[],
    )
    app.storage.execute("UPDATE workflows SET plan = NULL WHERE id = ?", ("noplan-wf",))
    task_id = app.schedule_manager.add("noplan-wf", "*/5 * * * *")
    _make_due(app, task_id)

    check_scheduled_workflows(app)

    runs = app.workflow_run_manager.list_runs(workflow_id="noplan-wf")
    assert len(runs) == 1, "a skipped-for-no-plan workflow must leave a row"
    assert runs[0]["status"] == "failed"
    assert "plan" in runs[0]["error"].lower()
    _assert_no_personal_data(runs[0]["error"])


def test_scheduled_needs_clarification_records_failure(app: App) -> None:
    """A headless run that stops to ask a question has ended.

    Nobody is there to answer a cron run, so a question is a dead end, not a
    pause. It left no row at all, which is a run that ended with nothing to
    say so.
    """
    _register(app, "sched-ask-wf")
    task_id = app.schedule_manager.add("sched-ask-wf", "*/5 * * * *")
    _make_due(app, task_id)

    from mycelos.workflows.agent import WorkflowAgentResult

    with patch("mycelos.workflows.agent.WorkflowAgent") as MockAgent:
        MockAgent.return_value.execute.return_value = WorkflowAgentResult(
            status="needs_clarification",
            error="Which note about Anna Mueller did you mean?",
        )
        check_scheduled_workflows(app)

    runs = app.workflow_run_manager.list_runs(workflow_id="sched-ask-wf")
    assert len(runs) == 1, "a headless question must still leave a row"
    assert runs[0]["status"] == "failed"
    assert runs[0]["error"], "the row must state why the run ended"
    # The question is LLM text built from the run's data — it is not a cause.
    _assert_no_personal_data(runs[0]["error"])


def test_scheduled_unknown_status_records_failure(app: App) -> None:
    """An ending this code does not recognise is still an ending."""
    _register(app, "sched-odd-wf")
    task_id = app.schedule_manager.add("sched-odd-wf", "*/5 * * * *")
    _make_due(app, task_id)

    from mycelos.workflows.agent import WorkflowAgentResult

    with patch("mycelos.workflows.agent.WorkflowAgent") as MockAgent:
        MockAgent.return_value.execute.return_value = WorkflowAgentResult(
            status="something_new", error=None,
        )
        check_scheduled_workflows(app)

    runs = app.workflow_run_manager.list_runs(workflow_id="sched-odd-wf")
    assert len(runs) == 1, "an unrecognised status must not vanish"
    assert runs[0]["status"] == "failed"
    assert runs[0]["error"]


def test_scheduled_workflow_success_records_no_extra_failure(app: App) -> None:
    """A completed scheduled run must not gain a spurious failure row."""
    _register(app, "sched-ok-wf")
    task_id = app.schedule_manager.add("sched-ok-wf", "*/5 * * * *")
    _make_due(app, task_id)

    with patch.object(app.llm, "complete", return_value=_mock_llm_response("All done.")):
        check_scheduled_workflows(app)

    runs = app.workflow_run_manager.list_runs(workflow_id="sched-ok-wf")
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
