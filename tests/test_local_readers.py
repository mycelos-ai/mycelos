import json
from pathlib import Path

import pytest

from mycelos.knowledge.local_readers import read_units


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x) + "\n" for x in records))


def test_project_groups_readmes_and_specs_but_excludes_code(tmp_path):
    project = tmp_path / "sharewise"
    (project / ".git").mkdir(parents=True)
    (project / "docs").mkdir()
    (project / "README.md").write_text("Portfolio application")
    (project / "docs" / "import.md").write_text("CSV portfolio import")
    (project / "app.rb").write_text("not a knowledge item")
    (project / ".git" / "secret.md").write_text("not public content")
    units = list(read_units("folder", tmp_path))
    assert len(units) == 1
    assert units[0].title == "sharewise"
    assert "CSV portfolio import" in units[0].content
    assert "not a knowledge item" not in units[0].content
    assert "not public content" not in units[0].content


def test_codex_uses_messages_once_and_keeps_session_identity(tmp_path):
    path = tmp_path / "rollout.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": "s1", "cwd": "/projects/docustore"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "Question"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Question"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Use OCR"}]}},
        {"type": "response_item", "payload": {"type": "reasoning", "text": "internal"}},
    ]
    write_jsonl(path, records)
    first = list(read_units("codex", tmp_path))[0]
    assert first.external_id == "s1"
    assert first.content.count("Question") == 1
    assert "Use OCR" in first.content
    assert "internal" not in first.content
    assert first.project == "docustore"
    with path.open("a") as f:
        f.write('{"type":')
    # A writer's incomplete last line must not erase a completed conversation.
    assert list(read_units("codex", tmp_path))[0].content == first.content


def test_claude_keeps_tool_results_and_excludes_internal_thinking(tmp_path):
    write_jsonl(tmp_path / "s.jsonl", [
        {"type": "user", "sessionId": "s2", "cwd": "/projects/vorfina", "uuid": "u", "message": {"role": "user", "content": "Fix the import"}},
        {"type": "assistant", "sessionId": "s2", "uuid": "a", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "The test passes"}]}},
        {"type": "user", "sessionId": "s2", "uuid": "r", "message": {"role": "user", "content": [{"type": "tool_result", "content": "3 passed"}]}},
    ])
    unit = list(read_units("claude", tmp_path))[0]
    assert unit.external_id == "s2"
    assert "3 passed" in unit.content
    assert "hidden" not in unit.content
    assert unit.project == "vorfina"


def test_symlink_cannot_import_outside_selected_folder(tmp_path):
    outside = tmp_path / "private.md"
    outside.write_text("secret")
    root = tmp_path / "source"
    root.mkdir()
    (root / "linked.md").symlink_to(outside)
    assert list(read_units("folder", root)) == []


def test_corrupt_complete_transcript_line_is_reported(tmp_path):
    (tmp_path / "bad.jsonl").write_text('not json\n')
    with pytest.raises(ValueError, match="bad.jsonl"):
        list(read_units("claude", tmp_path))


def test_claude_branch_uses_current_parent_chain(tmp_path):
    write_jsonl(tmp_path / "branch.jsonl", [
        {"type": "user", "sessionId": "s", "uuid": "root", "parentUuid": None, "message": {"content": "Question"}},
        {"type": "assistant", "sessionId": "s", "uuid": "old", "parentUuid": "root", "message": {"content": "Abandoned branch"}},
        {"type": "assistant", "sessionId": "s", "uuid": "new", "parentUuid": "root", "message": {"content": "Current answer"}},
    ])
    text = list(read_units("claude", tmp_path))[0].content
    assert "Question" in text
    assert "Current answer" in text
    assert "Abandoned branch" not in text


def test_codex_tool_evidence_includes_command_and_call_id(tmp_path):
    write_jsonl(tmp_path / "s.jsonl", [
        {"type": "session_meta", "payload": {"id": "s"}},
        {"type": "response_item", "payload": {"type": "function_call", "call_id": "call-1", "name": "shell", "arguments": '{"cmd":"pytest tests/test_import.py"}'}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-1", "output": "3 passed"}},
    ])
    text = list(read_units("codex", tmp_path))[0].content
    assert "pytest tests/test_import.py" in text
    assert "3 passed" in text
    assert text.count("call-1") == 2
