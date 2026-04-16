"""Confirmable Commands — LLM suggests, user confirms with Y/N.

When the LLM suggests a slash command (e.g., /mount add ~/Downloads --rw),
the system detects it and offers the user a Y/N confirmation instead of
making them retype the entire command.

This keeps the security guarantee: commands bypass the LLM.
The LLM only SUGGESTS, the system EXECUTES after user confirmation.
"""

from __future__ import annotations

import re
from typing import Any


# Pattern to detect slash commands in LLM output
_KNOWN_COMMANDS = r"mount|connector|agent|schedule|config|memory|workflow|model|credential|restart"

# In backticks: `/connector add email`
_BACKTICK_PATTERN = re.compile(rf"`(/(?:{_KNOWN_COMMANDS})\s[^`]+)`")

# Standalone on a line: /connector add email (no backticks, at start of line or after whitespace)
_STANDALONE_PATTERN = re.compile(rf"(?:^|\s)(/(?:{_KNOWN_COMMANDS})\s\S[^\n]*)", re.MULTILINE)


def extract_suggested_commands(text: str) -> list[str]:
    """Extract slash commands suggested by the LLM in its response.

    Looks for commands in backtick blocks like `/connector add email`
    or standalone on a line.

    Returns:
        List of unique command strings (without backticks), max 5.
    """
    found = set()
    for m in _BACKTICK_PATTERN.findall(text):
        found.add(m.strip())
    for m in _STANDALONE_PATTERN.findall(text):
        found.add(m.strip())
    return list(found)[:5]


def format_confirmable(commands: list[str]) -> str:
    """Format suggested commands as a confirmable prompt.

    Returns a string the terminal can display with Y/N options.
    """
    if not commands:
        return ""

    if len(commands) == 1:
        return f"\n[bold yellow]Suggested command:[/bold yellow] `{commands[0]}`\n"
    else:
        lines = ["\n[bold yellow]Suggested commands:[/bold yellow]"]
        for i, cmd in enumerate(commands, 1):
            lines.append(f"  ({i}) `{cmd}`")
        return "\n".join(lines) + "\n"
