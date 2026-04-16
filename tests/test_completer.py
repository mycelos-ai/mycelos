"""Tests for slash-command autocomplete."""

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from mycelos.cli.completer import SLASH_COMMANDS, SlashCommandCompleter


def _complete(text: str) -> list[tuple[str, str]]:
    """Helper: get completions as (text, meta_text) tuples."""
    completer = SlashCommandCompleter()
    doc = Document(text, len(text))
    event = CompleteEvent()
    return [(c.text, c.display_meta_text) for c in completer.get_completions(doc, event)]


class TestSlashCommandCompletion:
    def test_slash_shows_all_commands(self):
        results = _complete("/")
        texts = [r[0] for r in results]
        assert "/help" in texts
        assert "/demo" in texts
        assert "/memory" in texts
        assert len(texts) == len(SLASH_COMMANDS)

    def test_partial_command_filters(self):
        results = _complete("/de")
        texts = [r[0] for r in results]
        assert texts == ["/demo"]

    def test_partial_command_multiple_matches(self):
        results = _complete("/co")
        texts = [r[0] for r in results]
        assert "/config" in texts
        assert "/connector" in texts
        assert "/cost" in texts

    def test_command_shows_description(self):
        results = _complete("/de")
        assert results[0][1] == "Feature demonstrations"

    def test_no_completion_without_slash(self):
        results = _complete("hello")
        assert results == []

    def test_empty_input(self):
        results = _complete("")
        assert results == []


class TestSubcommandCompletion:
    def test_demo_subcommands(self):
        results = _complete("/demo ")
        texts = [r[0] for r in results]
        assert "widget" in texts

    def test_memory_subcommands(self):
        results = _complete("/memory ")
        texts = [r[0] for r in results]
        assert "list" in texts
        assert "search" in texts
        assert "delete" in texts
        assert "clear" in texts

    def test_partial_subcommand_filters(self):
        results = _complete("/memory l")
        texts = [r[0] for r in results]
        assert texts == ["list"]

    def test_subcommand_shows_description(self):
        results = _complete("/demo w")
        assert results[0][0] == "widget"
        assert results[0][1] == "Show all widget types"

    def test_no_subcommands_for_help(self):
        results = _complete("/help ")
        assert results == []

    def test_unknown_command_no_subcommands(self):
        results = _complete("/unknown ")
        assert results == []


class TestRegistryCompleteness:
    def test_all_slash_commands_have_descriptions(self):
        for cmd, info in SLASH_COMMANDS.items():
            assert cmd.startswith("/"), f"{cmd} must start with /"
            assert info["description"], f"{cmd} missing description"
            assert isinstance(info["subs"], dict), f"{cmd} subs must be dict"
