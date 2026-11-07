# Permission UI Improvements — Next Session

**Date:** 2026-03-24
**Status:** TODO

---

## 1. Path Validation Before Permission Prompt

Currently the system asks for permission even if the path doesn't exist.
Should validate first:

```
LLM: filesystem_list("/Users/stefan/Tante Maria")
System: Path doesn't exist. Check with find/glob.
  → If unique match found: use that path
  → If multiple matches: ask LLM "Which one?"
  → If no match: tell LLM "Path not found"
```

Only show permission prompt for paths that actually exist.

## 2. Claude Code Style CLI Permission UI

Current: text input Y/A/n
Better: interactive selection like Claude Code

```
╭─ Permission Required ──────────────────────╮
│                                             │
│  filesystem_list needs:                     │
│  /Users/stefan/Downloads                    │
│                                             │
│  > Allow (this session)                     │
│    Always Allow                             │
│    Deny                                     │
│                                             │
╰─────────────────────────────────────────────╯
```

Arrow keys to select, Enter to confirm.
Could use `rich.prompt.Prompt` with choices or `questionary` library.

## 3. Web UI Permission Widget

Current: generic confirm widget
Better: dedicated permission widget with:
- Clear "SYSTEM" badge (not AI)
- Path highlighted
- Radio buttons for Allow/Always/Deny
- "Always" explains what it means
