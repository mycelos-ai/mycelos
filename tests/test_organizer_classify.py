from __future__ import annotations

from mycelos.knowledge.organizer import (
    Classification,
    SILENT_CONFIDENCE,
    decide_action,
)


def test_high_confidence_existing_topic_is_silent_move() -> None:
    r = Classification(topic_path="projects/mycelos", confidence=0.9,
                       related_note_paths=[], new_topic_name=None)
    assert decide_action(r, topic_exists=True) == "silent_move"


def test_high_confidence_but_unknown_topic_falls_back_to_suggest() -> None:
    r = Classification(topic_path="projects/mycelos", confidence=0.95,
                       related_note_paths=[], new_topic_name=None)
    assert decide_action(r, topic_exists=False) == "suggest_move"


def test_high_confidence_with_new_topic_name_is_suggest_new_topic() -> None:
    r = Classification(topic_path=None, confidence=0.92,
                       related_note_paths=[], new_topic_name="Coffee Stuff")
    assert decide_action(r, topic_exists=False) == "suggest_new_topic"


def test_low_confidence_is_suggest_move() -> None:
    r = Classification(topic_path="x", confidence=0.5,
                       related_note_paths=[], new_topic_name=None)
    assert decide_action(r, topic_exists=True) == "suggest_move"


def test_threshold_boundary() -> None:
    r = Classification(topic_path="x", confidence=SILENT_CONFIDENCE,
                       related_note_paths=[], new_topic_name=None)
    assert decide_action(r, topic_exists=True) == "silent_move"
