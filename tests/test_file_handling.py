"""Tests for File Handling — inbox, extraction, analysis, security."""

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.files.inbox import sanitize_filename, InboxManager


class TestSanitizeFilename:
    def test_normal_filename(self):
        assert sanitize_filename("invoice.pdf") == "invoice.pdf"

    def test_path_traversal_dots(self):
        result = sanitize_filename("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result

    def test_path_separators_stripped(self):
        result = sanitize_filename("path/to/file.pdf")
        assert result == "file.pdf"

    def test_backslash_stripped(self):
        result = sanitize_filename("path\\to\\file.pdf")
        assert "\\" not in result

    def test_null_bytes_removed(self):
        result = sanitize_filename("file\x00.pdf")
        assert "\x00" not in result

    def test_empty_becomes_unnamed(self):
        assert sanitize_filename("") == "unnamed_file"

    def test_dots_only_becomes_unnamed(self):
        assert sanitize_filename("..") == "unnamed_file"

    def test_special_chars_replaced(self):
        result = sanitize_filename("my file (1).pdf")
        assert "(" not in result
        assert ")" not in result

    def test_truncated_long_name(self):
        result = sanitize_filename("a" * 300 + ".pdf")
        assert len(result) <= 200


class TestInboxManager:
    def test_save_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = InboxManager(Path(tmp) / "inbox")
            path = mgr.save(b"hello world", "test.txt")
            assert path.exists()
            assert path.read_bytes() == b"hello world"

    def test_save_prevents_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = InboxManager(Path(tmp) / "inbox")
            path = mgr.save(b"data", "../../evil.txt")
            assert str(path.resolve()).startswith(str((Path(tmp) / "inbox").resolve()))

    def test_list_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = InboxManager(Path(tmp) / "inbox")
            mgr.save(b"a", "file1.txt")
            mgr.save(b"b", "file2.pdf")
            assert len(mgr.list_files()) == 2

    def test_duplicate_filename_suffixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = InboxManager(Path(tmp) / "inbox")
            p1 = mgr.save(b"first", "doc.pdf")
            p2 = mgr.save(b"second", "doc.pdf")
            assert p1 != p2
            assert p1.exists() and p2.exists()

    def test_rejects_oversized(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = InboxManager(Path(tmp) / "inbox", max_size_bytes=100)
            with pytest.raises(ValueError, match="too large"):
                mgr.save(b"x" * 200, "big.bin")

    def test_remove_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = InboxManager(Path(tmp) / "inbox")
            path = mgr.save(b"data", "delete_me.txt")
            assert mgr.remove(path)
            assert not path.exists()

    def test_remove_rejects_outside_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = InboxManager(Path(tmp) / "inbox")
            assert not mgr.remove(Path("/etc/passwd"))

    def test_get_path_partial_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = InboxManager(Path(tmp) / "inbox")
            mgr.save(b"data", "invoice-firma-x.pdf")
            found = mgr.get_path("firma-x")
            assert found is not None
            assert "firma-x" in found.name


class TestTextExtraction:
    def test_extract_text_file(self):
        from mycelos.files.extractor import extract_text
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("Hello world")
            f.flush()
            text, method = extract_text(Path(f.name))
        assert text == "Hello world"
        assert method == "text"
        os.unlink(f.name)

    def test_extract_markdown(self):
        from mycelos.files.extractor import extract_text
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f.write("# Title\nContent")
            f.flush()
            text, method = extract_text(Path(f.name))
        assert "Title" in text
        assert method == "text"
        os.unlink(f.name)

    def test_extract_csv(self):
        from mycelos.files.extractor import extract_text
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
            f.write("name,amount\nFirma X,1234\n")
            f.flush()
            text, method = extract_text(Path(f.name))
        assert "Firma X" in text
        assert method == "csv"
        os.unlink(f.name)

    def test_extract_json(self):
        from mycelos.files.extractor import extract_text
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write('{"key": "value"}')
            f.flush()
            text, method = extract_text(Path(f.name))
        assert "key" in text
        assert method == "text"
        os.unlink(f.name)

    def test_image_needs_vision(self):
        from mycelos.files.extractor import extract_text
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff\xe0")
            f.flush()
            text, method = extract_text(Path(f.name))
        assert method == "vision_needed"
        assert text == ""
        os.unlink(f.name)

    def test_unknown_format(self):
        from mycelos.files.extractor import extract_text
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"binary")
            f.flush()
            text, method = extract_text(Path(f.name))
        assert method == "unsupported"
        os.unlink(f.name)

    def test_png_needs_vision(self):
        from mycelos.files.extractor import extract_text
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG")
            f.flush()
            _, method = extract_text(Path(f.name))
        assert method == "vision_needed"
        os.unlink(f.name)


class TestAnalyzer:
    def test_build_prompt_wraps_content(self):
        from mycelos.files.analyzer import build_analysis_prompt
        prompt = build_analysis_prompt("Invoice content here", "invoice.pdf")
        assert "<document>" in prompt
        assert "</document>" in prompt
        assert "untrusted" in prompt.lower()
        assert "Invoice content here" in prompt

    def test_build_prompt_truncates(self):
        from mycelos.files.analyzer import build_analysis_prompt
        long_content = "x" * 5000
        prompt = build_analysis_prompt(long_content, "big.txt")
        assert len(prompt) < 5000  # truncated

    def test_parse_valid_json(self):
        from mycelos.files.analyzer import parse_analysis_response
        result = parse_analysis_response('{"type": "invoice", "summary": "Test", "entities": {}}')
        assert result["type"] == "invoice"

    def test_parse_json_in_markdown(self):
        from mycelos.files.analyzer import parse_analysis_response
        result = parse_analysis_response('```json\n{"type": "report", "summary": "Test"}\n```')
        assert result["type"] == "report"

    def test_parse_invalid_returns_default(self):
        from mycelos.files.analyzer import parse_analysis_response
        result = parse_analysis_response("not json at all")
        assert result["type"] == "other"

    def test_validate_good_result(self):
        from mycelos.files.analyzer import validate_analysis
        assert validate_analysis({"type": "invoice", "summary": "Test"})

    def test_validate_bad_result(self):
        from mycelos.files.analyzer import validate_analysis
        assert not validate_analysis({"random": "data"})
        assert not validate_analysis({})
        assert not validate_analysis("not a dict")

    def test_sanitize_template_var_normal(self):
        from mycelos.files.analyzer import sanitize_template_var
        assert sanitize_template_var("Firma X") == "Firma_X"

    def test_sanitize_template_var_traversal(self):
        from mycelos.files.analyzer import sanitize_template_var
        result = sanitize_template_var("../../evil")
        assert ".." not in result
        assert "/" not in result

    def test_sanitize_template_var_slashes(self):
        from mycelos.files.analyzer import sanitize_template_var
        assert "/" not in sanitize_template_var("path/to/evil")
        assert "\\" not in sanitize_template_var("path\\to\\evil")

    def test_expand_filing_rule(self):
        from mycelos.files.analyzer import expand_filing_rule
        rule = "~/Documents/Rechnungen/{year}-{month}/"
        result = expand_filing_rule(rule, {"type": "invoice", "entities": {}})
        assert "2026" in result
        assert "{year}" not in result

    def test_expand_filing_rule_with_company(self):
        from mycelos.files.analyzer import expand_filing_rule
        rule = "~/Documents/{company}/"
        result = expand_filing_rule(rule, {
            "type": "invoice",
            "entities": {"company": "Firma X"}
        })
        assert "Firma_X" in result
        assert "/" in result  # path separator from template, not from company


class TestFileTools:
    """Tests for file_analyze and file_manage tools in CHAT_AGENT_TOOLS."""

    def test_file_tools_in_tool_list(self):
        from mycelos.chat.service import CHAT_AGENT_TOOLS
        names = [t["function"]["name"] for t in CHAT_AGENT_TOOLS]
        assert "file_analyze" in names
        assert "file_manage" in names

    def test_file_analyze_has_required_path(self):
        from mycelos.chat.service import CHAT_AGENT_TOOLS
        tool = next(t for t in CHAT_AGENT_TOOLS if t["function"]["name"] == "file_analyze")
        assert "path" in tool["function"]["parameters"]["required"]

    def test_file_manage_has_action_and_source(self):
        from mycelos.chat.service import CHAT_AGENT_TOOLS
        tool = next(t for t in CHAT_AGENT_TOOLS if t["function"]["name"] == "file_manage")
        assert "action" in tool["function"]["parameters"]["required"]
        assert "source" in tool["function"]["parameters"]["required"]

    def test_file_manage_action_enum(self):
        from mycelos.chat.service import CHAT_AGENT_TOOLS
        tool = next(t for t in CHAT_AGENT_TOOLS if t["function"]["name"] == "file_manage")
        actions = tool["function"]["parameters"]["properties"]["action"]["enum"]
        assert set(actions) == {"move", "copy", "delete"}


class TestInboxSlashCommand:
    """Tests for /inbox slash command."""

    def test_inbox_list_empty(self):
        import tempfile
        from pathlib import Path
        from mycelos.app import App
        from mycelos.chat.slash_commands import handle_slash_command
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-inbox"
            app = App(Path(tmp))
            app.initialize()
            result = handle_slash_command(app, "/inbox")
            assert "empty" in result.lower()

    def test_inbox_list_with_files(self):
        import tempfile
        from pathlib import Path
        from mycelos.app import App
        from mycelos.chat.slash_commands import handle_slash_command
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-inbox2"
            app = App(Path(tmp))
            app.initialize()
            # Put a file in inbox
            inbox_dir = Path(tmp) / "inbox"
            inbox_dir.mkdir(parents=True, exist_ok=True)
            (inbox_dir / "2026-03-26_test.pdf").write_bytes(b"x" * 1024)
            result = handle_slash_command(app, "/inbox")
            assert "Inbox" in result
            assert "test.pdf" in result

    def test_inbox_clear(self):
        import tempfile
        from pathlib import Path
        from mycelos.app import App
        from mycelos.chat.slash_commands import handle_slash_command
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-inbox3"
            app = App(Path(tmp))
            app.initialize()
            inbox_dir = Path(tmp) / "inbox"
            inbox_dir.mkdir(parents=True, exist_ok=True)
            (inbox_dir / "2026-03-26_file1.txt").write_bytes(b"hello")
            (inbox_dir / "2026-03-26_file2.txt").write_bytes(b"world")
            result = handle_slash_command(app, "/inbox clear")
            assert "2" in result
            assert "Cleared" in result

    def test_inbox_unknown_subcommand(self):
        import tempfile
        from pathlib import Path
        from mycelos.app import App
        from mycelos.chat.slash_commands import handle_slash_command
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-inbox4"
            app = App(Path(tmp))
            app.initialize()
            result = handle_slash_command(app, "/inbox unknown")
            assert "Usage" in result

    def test_inbox_in_help(self):
        import tempfile
        from pathlib import Path
        from mycelos.app import App
        from mycelos.chat.slash_commands import handle_slash_command
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-inbox5"
            app = App(Path(tmp))
            app.initialize()
            result = handle_slash_command(app, "/help")
            assert "/inbox" in result

    def test_inbox_in_completer(self):
        from mycelos.cli.completer import SLASH_COMMANDS
        assert "/inbox" in SLASH_COMMANDS
        assert "clear" in SLASH_COMMANDS["/inbox"]["subs"]
