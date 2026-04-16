"""Tests for the agent Test Runner -- executes pytest in isolated environment."""

from __future__ import annotations

import pytest

from mycelos.agents.test_runner import TestResult, _parse_pytest_summary, run_agent_tests


# --- Simple passing tests ---

SIMPLE_CODE = """\
class MyAgent:
    def execute(self, input):
        return type('R', (), {'success': True, 'result': 'hello', 'error': ''})()
"""

SIMPLE_TESTS = """\
from agent_code import MyAgent

def test_agent_executes():
    agent = MyAgent()
    result = agent.execute(None)
    assert result.success

def test_agent_returns_result():
    agent = MyAgent()
    result = agent.execute(None)
    assert result.result == 'hello'
"""


def test_passing_tests():
    result = run_agent_tests(SIMPLE_CODE, SIMPLE_TESTS)
    assert result.passed
    assert result.tests_run >= 2
    assert result.tests_failed == 0
    assert result.duration_ms > 0


def test_passing_tests_output():
    result = run_agent_tests(SIMPLE_CODE, SIMPLE_TESTS)
    assert "passed" in result.output


# --- Failing tests ---

FAILING_TESTS = """\
from agent_code import MyAgent

def test_fails():
    agent = MyAgent()
    result = agent.execute(None)
    assert result.result == 'wrong_value'
"""


def test_failing_tests():
    result = run_agent_tests(SIMPLE_CODE, FAILING_TESTS)
    assert not result.passed
    assert result.tests_failed >= 1
    assert result.error or "FAILED" in result.output


# --- Import error ---

BAD_TESTS = """\
from agent_code import NonExistentClass

def test_import():
    pass
"""


def test_import_error():
    result = run_agent_tests(SIMPLE_CODE, BAD_TESTS)
    assert not result.passed


# --- Syntax error in code ---

SYNTAX_ERROR_CODE = """\
class MyAgent:
    def execute(self
        return None
"""


def test_syntax_error():
    result = run_agent_tests(SYNTAX_ERROR_CODE, SIMPLE_TESTS)
    assert not result.passed


# --- Timeout ---

SLOW_TESTS = """\
import time

def test_slow():
    time.sleep(10)
    assert True
"""


def test_timeout():
    result = run_agent_tests(SIMPLE_CODE, SLOW_TESTS, timeout=2)
    assert not result.passed
    assert "timed out" in result.error.lower()


# --- TestResult dataclass ---

def test_result_dataclass():
    r = TestResult(
        passed=True, output="ok", error="", duration_ms=100, tests_run=3, tests_failed=0
    )
    assert r.passed
    assert r.tests_run == 3


def test_result_frozen():
    r = TestResult(passed=True, output="", error="", duration_ms=0, tests_run=0, tests_failed=0)
    with pytest.raises(AttributeError):
        r.passed = False  # type: ignore[misc]


# --- Parse summary ---

def test_parse_summary_passed():
    assert _parse_pytest_summary("3 passed in 0.05s") == (3, 0)


def test_parse_summary_mixed():
    assert _parse_pytest_summary("1 failed, 2 passed in 0.10s") == (3, 1)


def test_parse_summary_empty():
    assert _parse_pytest_summary("no tests ran") == (0, 0)


# --- Extra files ---

CODE_WITH_IMPORT = """\
from helpers import helper_func

class MyAgent:
    def execute(self, input):
        return type('R', (), {'success': True, 'result': helper_func(), 'error': ''})()
"""

TESTS_WITH_HELPER = """\
from agent_code import MyAgent

def test_with_helper():
    agent = MyAgent()
    result = agent.execute(None)
    assert result.result == 42
"""


def test_extra_files():
    result = run_agent_tests(
        CODE_WITH_IMPORT,
        TESTS_WITH_HELPER,
        extra_files={"helpers.py": "def helper_func(): return 42"},
    )
    assert result.passed
