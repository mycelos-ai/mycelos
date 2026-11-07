# Chat Completion Plan — Session Resume + Real Routing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the chat fully functional — sessions resume with history, routing uses different prompts per intent, task requests execute workflows, system commands work.

**Architecture:** Four focused fixes to the existing chat_cmd.py and orchestrator. No new files needed — just wiring what we already have.

**Tech Stack:** Python 3.12+, existing codebase

---

## What's broken and how to fix it

### Fix 1: Session Resume loads conversation history

Currently `--continue` finds the latest session but doesn't load messages into `conversation[]`. The LLM starts fresh every time.

**Change in `chat_cmd.py`:** After finding the session, load messages and rebuild conversation:

```python
if continue_session:
    session_id = app.session_store.get_latest_session()
    if session_id:
        # Load previous messages into conversation
        previous = app.session_store.load_messages(session_id)
        for msg in previous:
            conversation.append({"role": msg["role"], "content": msg["content"]})
        console.print(f"[dim]Resumed session ({len(previous)} messages loaded)[/dim]")
```

### Fix 2: Different System Prompts per Intent

Currently every intent gets the Creator-Agent system prompt. Fix: use the PromptAssembler to load different prompts based on the route result.

For CONVERSATION → Creator-Agent prompt (as now)
For TASK_REQUEST → inject the plan into the conversation so the LLM explains it
For CREATE_AGENT → inject the proposal into the conversation
For SYSTEM_COMMAND → handle directly without LLM call

**Change in REPL loop:** Before the LLM call, adjust the conversation based on intent:

```python
if route_result and route_result.intent == Intent.TASK_REQUEST and route_result.plan:
    # Add plan context so LLM can explain it naturally
    plan_summary = json.dumps(route_result.plan, indent=2)
    conversation.append({"role": "system", "content":
        f"The PlannerAgent has created this plan for the user's request. "
        f"Explain it naturally and ask if they want to proceed:\n{plan_summary}"
    })

elif route_result and route_result.intent == Intent.CREATE_AGENT and route_result.proposal:
    proposal = route_result.proposal
    conversation.append({"role": "system", "content":
        f"The CreatorAgent has generated an agent proposal. "
        f"Agent: {proposal.get('agent_id')}, Type: {proposal.get('agent_type')}. "
        f"Audit: {'approved' if proposal.get('audit_result', {}).get('approved') else 'REJECTED'}. "
        f"Present this to the user and ask for confirmation."
    })

elif route_result and route_result.intent == Intent.SYSTEM_COMMAND:
    # Handle directly — no LLM call needed for simple commands
    system_response = _handle_system_command(app, user_input)
    console.print(f"\n[bold magenta]System>[/bold magenta]")
    console.print(system_response)
    conversation.append({"role": "assistant", "content": system_response})
    app.session_store.append_message(session_id, role="assistant", content=system_response)
    continue  # skip LLM call
```

### Fix 3: System Command Handler

Direct execution for system queries without LLM call:

```python
def _handle_system_command(app: App, user_input: str) -> str:
    """Handle system commands directly without LLM."""
    lower = user_input.lower()

    if "config" in lower or "generation" in lower:
        gens = app.config.list_generations(limit=5)
        lines = ["**Config Generations:**\n"]
        for g in gens:
            active = " ← active" if g.is_active else ""
            lines.append(f"- Gen {g.id}: {g.description or 'no description'}{active}")
        return "\n".join(lines)

    elif "agent" in lower:
        agents = app.agent_registry.list_agents()
        if not agents:
            return "No agents registered yet."
        lines = ["**Registered Agents:**\n"]
        for a in agents:
            lines.append(f"- {a['name']} ({a['agent_type']}) — {a['status']}, reputation: {a['reputation']}")
        return "\n".join(lines)

    elif "connector" in lower:
        services = app.credentials.list_services()
        if not services:
            return "No connectors configured. Run: `mycelos connector setup`"
        lines = ["**Configured Connectors:**\n"]
        for s in services:
            lines.append(f"- {s}")
        return "\n".join(lines)

    elif "task" in lower:
        tasks = app.task_manager.list_tasks(limit=10)
        if not tasks:
            return "No tasks yet."
        lines = ["**Recent Tasks:**\n"]
        for t in tasks:
            lines.append(f"- {t['goal'][:50]} — {t['status']} ({t['created_at'][:16]})")
        return "\n".join(lines)

    elif "session" in lower:
        sessions = app.session_store.list_sessions()
        if not sessions:
            return "No sessions yet."
        lines = [f"**Sessions ({len(sessions)}):**\n"]
        for s in sessions[:5]:
            lines.append(f"- {s.get('session_id', '?')[:8]}... ({s.get('message_count', 0)} messages)")
        return "\n".join(lines)

    else:
        return "Available commands: config, agents, connectors, tasks, sessions"
```

### Fix 4: Task Execution (when user confirms plan)

After showing the plan, the LLM asks "Soll ich das ausführen?". When the user confirms, we need to detect that and actually run the workflow. This is the most complex part — we park it for now and add a TODO. The plan is shown, but execution requires the user to have a matching workflow YAML.

---

## Tasks

### Task 1: Session Resume + System Commands + Plan Context

One task because these changes are all in `chat_cmd.py` and interdependent.

**Files:**
- Modify: `src/mycelos/cli/chat_cmd.py`

**Tests:**
- Create: `tests/test_chat_session_resume.py` (unit test with mocked SessionStore)

**Steps:**

- [ ] Write test for session resume loading messages
- [ ] Write test for system command handling
- [ ] Implement session resume (load messages into conversation[])
- [ ] Implement _handle_system_command()
- [ ] Add plan/proposal context injection before LLM call
- [ ] Run full test suite
- [ ] Commit
