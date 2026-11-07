# Request Action Tool — LLM-Initiated Privileged Actions with User Confirmation

**Date:** 2026-03-24
**Status:** PLAN (next session)

---

## Problem

Currently when the LLM needs a privileged action (mount, connector setup, package install),
it tells the user to type a slash command manually. This is bad UX:

```
LLM: "You can set up GitHub with: /connector setup github"
User: has to copy/paste or type the command
```

## Solution

A `request_action` tool that lets the LLM propose actions for user confirmation:

```
LLM: calls request_action("mount add ~/Documents --rw", "Need read/write access to your Documents folder")
System: shows confirmation widget to user
User: clicks "Yes" / types "ja"
System: executes the slash command, returns result to LLM
LLM: "Done! I now have access to your Documents folder."
```

---

## Tool Definition

```python
{
    "name": "request_action",
    "description": (
        "Request a privileged system action that needs user confirmation. "
        "Use this instead of telling the user to type slash commands. "
        "The user will see a confirmation prompt and can approve or reject."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": (
                    "The action to execute. Can be a slash command (without /) "
                    "or a system action. Examples: "
                    "'connector setup github', 'mount add ~/Documents --rw', "
                    "'schedule add daily-news --cron 0 7 * * *', "
                    "'pip install reportlab'"
                ),
            },
            "reason": {
                "type": "string",
                "description": "Why this action is needed — shown to the user for context",
            },
        },
        "required": ["action", "reason"],
    },
}
```

---

## Execution Flow

```
1. LLM calls request_action(action, reason)
   |
2. ChatService receives tool call
   |
3. Security check: is this action allowed to be proposed?
   |  - Allowlist of action types (connector, mount, schedule, pip)
   |  - Block dangerous actions (config rollback, agent delete, etc.)
   |
4. Return confirmation_required to LLM with action details
   |
5. LLM presents to user: "I need to [reason]. Execute: [action]?"
   |
6. User confirms (next message: "ja", "yes", etc.)
   |
7. ChatService detects pending action confirmation
   |
8. Execute the slash command via handle_slash_command()
   |
9. Return result to conversation
   |
10. LLM sees result, continues
```

---

## Channel-Specific Rendering

### Terminal (CLI)
```
Mycelos> I need read/write access to your Documents folder.

  ┌─ Requested Action ─────────────────────────┐
  │ /mount add ~/Documents --rw                 │
  │                                             │
  │ Reason: Need access to read invoice PDFs    │
  │                                             │
  │ [Execute]  [Cancel]                         │
  └─────────────────────────────────────────────┘

You> ja
System> Mount added: /Users/stefan/Documents (read_write)
```

### Telegram
```
Mycelos: I need access to your Documents folder.

Action: /mount add ~/Documents --rw
Reason: Need to read invoice PDFs

Execute? (yes/no)
```

### Web Frontend (uses existing confirm widget)
```json
{
    "type": "widget",
    "data": {
        "widget": {
            "type": "confirm",
            "prompt": "Execute: /mount add ~/Documents --rw\n\nReason: Need access to read invoice PDFs",
            "danger": false
        }
    }
}
```

---

## Action Types

### Allowed (can be proposed by LLM)
| Action | Example | Confirm Required |
|--------|---------|-----------------|
| connector setup | `connector setup github` | Yes |
| connector add | `connector add brave-search` | Yes |
| mount add | `mount add ~/Documents --rw` | Yes |
| schedule add | `schedule add news --cron "0 7 * * *"` | Yes |
| pip install | `pip install reportlab` | Yes (sandbox only) |
| memory set | `memory set tone casual` | No (auto-execute) |

### Blocked (LLM cannot propose)
| Action | Reason |
|--------|--------|
| config rollback | Destructive, user-initiated only |
| mount revoke | Destructive |
| agent delete | Destructive |
| connector remove | Destructive |
| schedule delete | Destructive |
| memory clear | Destructive |
| raw SQL | Security risk |

---

## Implementation Plan

### Step 1: request_action tool in ChatService
- Add to CHAT_AGENT_TOOLS
- Dispatch in _execute_tool_inner
- Action allowlist validation
- Return confirmation_required with action details
- Store pending action in session state

### Step 2: Confirmation detection
- When user confirms (next message after request_action)
- Detect "ja"/"yes" pattern (reuse is_plan_confirmation)
- Execute via handle_slash_command
- Return result to conversation

### Step 3: pip install support (for Creator)
- Special action type: "pip install <package>"
- Executes in sandbox/virtualenv, not global
- Creator pipeline can use request_action for dependencies

### Step 4: Widget rendering
- Terminal: Rich panel with Execute/Cancel
- Telegram: text with yes/no prompt
- Web: existing confirm widget type

### Step 5: System prompt update
- Tell LLM to use request_action instead of suggesting slash commands
- "Instead of telling the user to type /connector setup github,
   call request_action('connector setup github', 'Need GitHub access')"

---

## Security Considerations

1. **Action allowlist** — only approved action types can be proposed
2. **User always confirms** — no auto-execution of privileged actions
3. **Audit trail** — every request_action logged with action, reason, user decision
4. **Rate limit** — max 3 pending actions per session
5. **No chaining** — LLM cannot request_action to approve its own request_action
6. **Destructive actions blocked** — delete, rollback, clear cannot be proposed

---

## Integration with Existing Systems

### Replaces
- "Confirmable Commands" pattern (regex-based detection of backtick commands)
- Manual slash command suggestions in system prompt

### Works With
- PolicyEngine (request_action checks policy before proposing)
- Audit Logger (all actions logged)
- HandoffEnvelope (Creator can request pip install via this mechanism)
- Widget system (confirm widget in web frontend)

---

## Cost: Zero LLM overhead
The request_action tool is a system tool — no additional LLM calls needed.
The confirmation is handled by pattern detection, not LLM classification.
