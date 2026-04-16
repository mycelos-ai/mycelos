"""Tests for confirmable commands — LLM suggests, user confirms."""

from __future__ import annotations

import pytest

from mycelos.chat.confirmable import extract_suggested_commands, format_confirmable


def test_extract_single_command():
    text = "Gib Zugriff frei mit: `/mount add ~/Downloads --rw`"
    cmds = extract_suggested_commands(text)
    assert cmds == ["/mount add ~/Downloads --rw"]


def test_extract_multiple_commands():
    text = (
        "Fuehre folgende Befehle aus:\n"
        "1. `/mount add ~/Documents --read`\n"
        "2. `/connector add github`\n"
    )
    cmds = extract_suggested_commands(text)
    assert len(cmds) == 2
    assert "/mount add ~/Documents --read" in cmds
    assert "/connector add github" in cmds


def test_extract_no_commands():
    text = "Hier gibt es keine Befehle, nur normalen Text."
    assert extract_suggested_commands(text) == []


def test_extract_ignores_non_slash():
    text = "Run `pip install mycelos` to install."
    assert extract_suggested_commands(text) == []


def test_extract_agent_grant():
    text = "Gib ihm die Berechtigung: `/agent news-agent grant search.web`"
    cmds = extract_suggested_commands(text)
    assert cmds == ["/agent news-agent grant search.web"]


def test_extract_schedule():
    text = 'Plane es mit: `/schedule add news-summary --cron "0 8 * * *"`'
    cmds = extract_suggested_commands(text)
    assert len(cmds) == 1
    assert "/schedule add" in cmds[0]


def test_format_single():
    result = format_confirmable(["/mount add ~/Downloads --rw"])
    assert "Suggested command" in result
    assert "/mount add" in result


def test_format_multiple():
    result = format_confirmable(["/mount add ~/a --read", "/connector add github"])
    assert "(1)" in result
    assert "(2)" in result


def test_format_empty():
    assert format_confirmable([]) == ""
