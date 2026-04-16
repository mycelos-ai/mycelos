"""Unit tests for mycelos.knowledge.parse_note — DE+EN deterministic note parser."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mycelos.knowledge.parse_note import parse_note_text

FIXTURE = Path(__file__).parent / "fixtures" / "parse-note-vectors.json"
VECTORS = json.loads(FIXTURE.read_text())
REFERENCE_NOW = datetime.fromisoformat(VECTORS["reference_now"].replace("Z", "+00:00"))


@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=lambda v: v["name"])
def test_parse_note_vectors(vector: dict) -> None:
    result = parse_note_text(vector["input"], now=REFERENCE_NOW)
    expected = vector["expected"]
    assert result["type"] == expected["type"], f"type mismatch for {vector['name']}"
    assert result["tags"] == expected["tags"], f"tags mismatch for {vector['name']}"
    assert result["wikilinks"] == expected["wikilinks"], f"wikilinks mismatch for {vector['name']}"
    assert result["reminder"] == expected["reminder"], f"reminder mismatch for {vector['name']}"
    assert result["due"] == expected["due"], f"due mismatch for {vector['name']}"


def test_parse_note_empty_string() -> None:
    result = parse_note_text("", now=REFERENCE_NOW)
    assert result == {"type": "note", "due": None, "tags": [], "wikilinks": [], "reminder": False}


def test_parse_note_preserves_raw_text_in_body_semantics() -> None:
    """Wikilinks are noted but the body-level text is not rewritten by the parser."""
    result = parse_note_text("Call [[contacts/lisa]] tomorrow 2pm", now=REFERENCE_NOW)
    assert result["wikilinks"] == ["contacts/lisa"]
    assert result["due"] == "2026-04-09T14:00:00Z"
    assert result["type"] == "task"
