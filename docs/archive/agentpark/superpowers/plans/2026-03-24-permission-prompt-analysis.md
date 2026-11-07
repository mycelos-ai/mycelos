# Permission Prompt Analysis — Two Approaches

**Date:** 2026-03-24
**Status:** ANALYSIS (decision needed)

---

## The Two Approaches

### Approach A: LLM requests (current — request_action)

```
LLM: "I need access to ~/Downloads"
LLM: calls request_action("mount add ~/Downloads --rw", "Need to list files")
System: shows confirm widget
User: confirms
System: executes slash command
LLM: continues
```

**Problems:**
- LLM is in the loop — it decides WHEN to ask
- LLM can phrase the request manipulatively
- Double-prompting: LLM explains + system shows widget
- LLM sees the action details (security concern)
- The action text comes from LLM output (could be injection)

### Approach B: System intercepts (Claude Code style)

```
LLM: calls filesystem_list("~/Downloads")
Security Gate: "no mount for ~/Downloads"
System: shows permission prompt — "Grant read access to ~/Downloads? [Y/n/Always]"
User: Y
System: creates mount, re-executes filesystem_list
LLM: gets the result (never saw the permission prompt)
```

**Advantages:**
- LLM never sees the permission flow
- System controls what is shown to user
- No manipulation possible — the prompt comes from the security layer
- Cleaner UX — one prompt, not LLM + system double-talk
- "Always Allow" option reduces future prompts
- Same pattern works for ALL permission types

---

## How It Maps To Our Architecture

### What We Already Have

```
PolicyEngine.evaluate(user_id, agent_id, resource) → always/confirm/never
```

The `confirm` decision is exactly this — "ask the user before executing".
Currently we return `confirmation_required` to the LLM.
Instead, we should PAUSE, ask the user, and then continue.

### The Permission Types

| Trigger | When | What System Shows |
|---------|------|-------------------|
| Mount missing | filesystem_read/write/list on unmounted path | "Grant access to ~/Downloads? [Y/n/Always]" |
| Policy=confirm | Any tool with confirm policy | "Allow [tool_name] for this session? [Y/n/Always]" |
| Package needed | Creator needs pip install | "Install reportlab? [Y/n]" |
| Connector missing | Tool needs unconfigured connector | "Set up GitHub connector? [Y/n]" |

### The "Always Allow" Option

Like Claude Code, we can save the decision:
- **Y** — allow this one time
- **A (Always)** — create a permanent policy (always allow)
- **N** — deny this time
- **Never** — create a permanent policy (never allow)

This maps directly to our PolicyEngine:
- Y → execute once, no policy change
- A → `policy_engine.set_policy(user_id, None, resource, "always")`
- N → deny, no policy change
- Never → `policy_engine.set_policy(user_id, None, resource, "never")`

---

## Implementation Design

### In ChatService._execute_tool

```python
def _execute_tool(self, tool_name, args, user_id):
    decision = self._app.policy_engine.evaluate(user_id, None, tool_name)

    if decision == "never":
        return {"error": "Blocked by policy."}

    if decision == "confirm":
        # PAUSE — ask user via permission prompt
        # This is a SYSTEM prompt, not an LLM prompt
        raise PermissionRequired(tool_name, args, reason="Policy requires confirmation")

    # decision == "always" → execute
    result = self._execute_tool_inner(tool_name, args)
    return result
```

### For Mount-Specific Permissions

```python
def _filesystem_read(self, path):
    if not mounts.check_access(path, "read"):
        # Instead of returning error, raise permission request
        raise PermissionRequired(
            action="mount add",
            target=path,
            access="read",
            reason=f"Need read access to {path}",
        )
    # ... normal execution
```

### In handle_message (the tool loop)

```python
try:
    result = self._execute_tool(tool_name, args)
except PermissionRequired as perm:
    # STOP the tool loop
    # Emit permission prompt event
    events.append(PermissionPromptEvent(
        tool=tool_name,
        action=perm.action,
        reason=perm.reason,
        options=["allow_once", "always_allow", "deny", "never_allow"],
    ))
    # Store pending state for when user responds
    self._pending_permission = {
        "tool_name": tool_name,
        "args": args,
        "permission": perm,
        "conversation_state": conversation.copy(),
    }
    return events  # Stop here, wait for user
```

### When User Responds

```python
# Next message from user
if self._pending_permission:
    if user_says_yes:
        # Execute the granted action (e.g., create mount)
        self._grant_permission(pending)
        # Re-execute the original tool call
        result = self._execute_tool(pending["tool_name"], pending["args"])
        # Continue the LLM conversation with the result
```

---

## CLI Rendering

```
You> Show me my Downloads folder

Mycelos>
  [filesystem_list running...]

  ╭─ Permission Required ──────────────────────╮
  │                                             │
  │  filesystem_list needs access to:           │
  │  ~/Downloads                                │
  │                                             │
  │  [Y] Allow once                             │
  │  [A] Always allow                           │
  │  [n] Deny                                   │
  │                                             │
  ╰─────────────────────────────────────────────╯

  > Y

  [filesystem_list done]

Mycelos> Here are the files in your Downloads folder:
  ...
```

### Key UX Points:
- The prompt comes from the SYSTEM, not from Mycelos
- Mycelos is "paused" — it doesn't talk during the permission prompt
- The tool name and target are shown clearly
- "Always allow" saves the user from future prompts
- After approval, the original request continues seamlessly

---

## Web UI Rendering

```json
{
    "type": "permission_prompt",
    "tool": "filesystem_list",
    "target": "~/Downloads",
    "reason": "Need read access to list directory contents",
    "options": [
        {"key": "allow_once", "label": "Allow Once"},
        {"key": "always_allow", "label": "Always Allow"},
        {"key": "deny", "label": "Deny"}
    ]
}
```

Rendered as a card with buttons — similar to the action_confirm widget
but clearly marked as a SYSTEM prompt, not an AI message.

---

## What This Replaces

1. **request_action tool** — no longer needed. The LLM just tries to use tools, the system handles permissions.
2. **confirmation_required in _execute_tool** — replaced by PermissionRequired exception
3. **"suggest /mount add" in system prompt** — not needed. System auto-prompts.
4. **Confirmable commands (backtick detection)** — not needed. System handles all.

---

## Recommendation

**Go with Approach B (System intercepts).** It is:
- More secure (LLM not in the permission loop)
- Better UX (one prompt, not LLM + system)
- More consistent (same pattern for all permission types)
- Aligned with Claude Code's proven pattern
- Simpler for the LLM (just call tools, system handles rest)

**Implementation order:**
1. PermissionRequired exception + handler in tool loop
2. CLI rendering (Rich panel with Y/A/n options)
3. Web UI rendering (permission_prompt widget)
4. Mount auto-grant on permission
5. "Always Allow" → PolicyEngine integration
6. Remove request_action tool and related code

---

## Impact on request_action

The `request_action` tool becomes unnecessary for most cases.
However, it could still be useful for:
- Creator requesting pip install (no tool trigger for this)
- Agent requesting connector setup (proactive, not reactive)

Decision: Keep request_action as fallback for proactive requests,
but the PRIMARY permission flow should be system-intercepted.
