# tests/test_chat_confirmation.py
"""Tests for plan confirmation detection in the orchestrator."""

from __future__ import annotations

import pytest

from mycelos.orchestrator import ChatOrchestrator, Intent, RouteResult, is_plan_confirmation


def test_confirmation_ja():
    assert is_plan_confirmation("Ja") is True

def test_confirmation_yes():
    assert is_plan_confirmation("Yes") is True

def test_confirmation_mach_das():
    assert is_plan_confirmation("Mach das") is True

def test_confirmation_ja_bitte():
    assert is_plan_confirmation("Ja bitte") is True

def test_confirmation_do_it():
    assert is_plan_confirmation("Do it") is True

def test_confirmation_ausfuehren():
    assert is_plan_confirmation("Ausfuehren") is True

def test_not_confirmation_question():
    assert is_plan_confirmation("Was kostet das?") is False

def test_not_confirmation_long_text():
    assert is_plan_confirmation("Ich moechte lieber etwas anderes machen, lass uns nochmal ueberlegen") is False

def test_not_confirmation_nein():
    assert is_plan_confirmation("Nein") is False

def test_not_confirmation_empty():
    assert is_plan_confirmation("") is False

def test_not_confirmation_conditional_yes():
    """'Ja, aber aendere Schritt 2' is NOT a simple confirmation."""
    assert is_plan_confirmation("Ja, aber aendere Schritt 2") is False

def test_not_confirmation_abbrechen():
    assert is_plan_confirmation("Abbrechen") is False
