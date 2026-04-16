"""Slash Commands — secure system commands that bypass the LLM.

Everything starting with / goes directly to the system, never to the LLM.
Credentials, permissions, and sensitive operations are handled here.
"""

from __future__ import annotations

from typing import Any


def handle_slash_command(app: Any, command: str) -> str | list:
    """Parse and execute a slash command.

    Args:
        app: Mycelos App instance.
        command: The full command string (e.g., "/memory list").

    Returns:
        Markdown-formatted response text, or a list of ChatEvents
        (for commands that produce rich widget output).
    """
    parts = command.strip().split()
    if not parts:
        return "Unknown command. Type /help for available commands."

    cmd = parts[0].lower().lstrip("/")
    args = parts[1:]

    handlers = {
        "help": _handle_help,
        "memory": _handle_memory,
        "mount": _handle_mount,
        "sessions": _handle_sessions,
        "cost": _handle_cost,
        "config": _handle_config,
        "agent": _handle_agent,
        "connector": _handle_connector,
        "schedule": _handle_schedule,
        "workflow": _handle_workflow,
        "model": _handle_model,
        "reload": _handle_reload,
        "demo": _handle_demo,
        "bg": _handle_bg,
        "inbox": _handle_inbox,
        "restart": _handle_restart,
        "credential": _handle_credential,
        "run": _handle_run,
    }

    handler = handlers.get(cmd)
    if handler is None:
        return f"Unknown command: /{cmd}. Type /help for available commands."

    return handler(app, args)


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

def _handle_help(app: Any, args: list[str]) -> str:
    """Show available slash commands."""
    return """**Available Commands:**

**/memory** — Manage persistent memory
  `/memory list` — Show all stored entries
  `/memory search <query>` — Search entries
  `/memory delete <key>` — Delete an entry
  `/memory clear` — Clear all entries

**/cost** — Usage & cost tracking
  `/cost` — Today's usage summary
  `/cost week` — This week
  `/cost month` — This month
  `/cost all` — All time

**/sessions** — Session management
  `/sessions` — Show recent sessions
  `/sessions resume <id>` — Resume a previous session

**/mount** — Filesystem access
  `/mount list` — Show mounted directories
  `/mount add <path> --read|--write` — Grant directory access
  `/mount add <path> --read --agent <id>` — Grant for specific agent
  `/mount revoke <id>` — Revoke access

**/config** — System configuration
  `/config show` — Current state
  `/config rollback [gen-id]` — Rollback

**/agent** — Agent management
  `/agent list` — Show all agents
  `/agent <id> info` — Agent details
  `/agent <id> grant <capability>` — Grant permission
  `/agent <id> revoke <capability>` — Revoke permission

**/connector** — Connector management
  `/connector list` — Show connectors
  `/connector setup <name>` — Set up connector

**/schedule** — Cron jobs
  `/schedule list` — Show scheduled tasks

**/workflow** — Workflow management
  `/workflow list` — Show workflows
  `/workflow runs` — Show active/paused runs

**/bg** — Background tasks
  `/bg` — List background tasks
  `/bg cancel <id>` — Cancel a task
  `/bg approve <id>` — Approve a waiting task
  `/bg detail <id>` — Show task details

**/model** — LLM models
  `/model list` — Show configured models

**/reload** — Reload MCP connectors
  Re-discovers tools after adding/removing connectors. No full restart needed.

**/demo** — Feature demonstrations
  `/demo widget` — Show all widget types (Table, StatusCard, ProgressBar, etc.)

**/inbox** — File inbox
  `/inbox` — List files in inbox
  `/inbox clear` — Remove all inbox files
"""


# ---------------------------------------------------------------------------
# /memory
# ---------------------------------------------------------------------------

def _handle_memory(app: Any, args: list[str]) -> str:
    """Handle /memory commands."""
    if not args:
        return _handle_memory_summary(app)

    action = args[0].lower()

    if action == "summary":
        return _handle_memory_summary(app)
    elif action == "list":
        return _handle_memory_list(app)
    elif action == "search" and len(args) >= 2:
        query = " ".join(args[1:])
        return _handle_memory_search(app, query)
    elif action == "delete" and len(args) >= 2:
        return _handle_memory_delete(app, args[1])
    elif action == "clear":
        return _handle_memory_clear(app)
    elif action == "set" and len(args) >= 3:
        # /memory set name Stefan
        setting = args[1].lower()
        value = " ".join(args[2:])
        return _handle_memory_set(app, setting, value)
    else:
        return (
            "Usage:\n"
            "  /memory — Summary of what Mycelos knows about you\n"
            "  /memory list — Detailed list with numbers\n"
            "  /memory search <query> — Search entries\n"
            "  /memory delete <number> — Delete entry by number\n"
            "  /memory set name <name> — Set your name\n"
            "  /memory set tone <style> — Set response style\n"
            "  /memory clear — Clear all entries"
        )


def _handle_memory_summary(app: Any) -> str:
    """Human-readable summary of what Mycelos knows."""
    user_name = app.memory.get("default", "system", "user.name")
    entries = app.memory.search("default", "system", "user.")

    # Filter out internal keys
    user_entries = [
        e for e in entries
        if isinstance(e, dict)
        and e.get("key", "").startswith("user.")
        and not e.get("key", "").startswith("user.name")
        and not e.get("key", "").startswith("session.")
        and not e.get("key", "").startswith("memory.reviewed")
    ]

    if not user_name and not user_entries:
        return "Ich kenne dich noch nicht. Schreib mir einfach — ich lerne mit der Zeit!"

    parts = []

    if user_name:
        parts.append(f"**Name:** {user_name}")

    # Group by category and show human-readable
    categories = {
        "preference": ("So magst du es", []),
        "decision": ("Das hast du entschieden", []),
        "context": ("Woran du arbeitest", []),
        "fact": ("Das weiss ich ueber dich", []),
    }

    for e in user_entries:
        key = e.get("key", "")
        value = e.get("value", "")
        for cat, (_, items) in categories.items():
            if f".{cat}." in key:
                # Extract readable name from key: user.preference.output_format → output format
                short_key = key.split(".")[-1].replace("_", " ")
                items.append(f"  - {short_key}: {value}")
                break

    for cat, (label, items) in categories.items():
        if items:
            parts.append(f"\n**{label}:**\n" + "\n".join(items))

    if not parts:
        return "Ich kenne dich noch nicht. Schreib mir einfach — ich lerne mit der Zeit!"

    count = sum(len(items) for _, (_, items) in categories.items())
    parts.insert(0 if not user_name else 1, f"_{count} Eintraege gespeichert_")

    return "\n".join(parts)


def _handle_memory_list(app: Any) -> str:
    """Detailed list with numbers for easy deletion."""
    entries = app.memory.search("default", "system", "user.")
    user_entries = [
        e for e in entries
        if isinstance(e, dict)
        and e.get("key", "").startswith("user.")
        and not e.get("key", "").startswith("session.")
        and not e.get("key", "").startswith("memory.reviewed")
    ]

    if not user_entries:
        return "Memory is empty."

    lines = [f"**Memory** ({len(user_entries)} entries)\n"]
    for i, e in enumerate(user_entries, 1):
        key = e.get("key", "")
        value = e.get("value", "")
        short_key = key.split(".")[-1].replace("_", " ")
        cat = "?"
        for c in ("preference", "decision", "context", "fact", "name"):
            if f".{c}" in key:
                cat = c[:4]
                break
        lines.append(f"  {i}. [{cat}] {short_key}: {value}")

    lines.append(f"\nDelete with: /memory delete <number>")
    return "\n".join(lines)


def _handle_memory_search(app: Any, query: str) -> str:
    """Search memory entries."""
    results = app.memory.search("default", "system", query)
    if not results:
        return f"No memory entries matching '{query}'."

    lines = [f"**Search: '{query}'** ({len(results)} results)\n"]
    for i, e in enumerate(results, 1):
        key = e.get("key", "") if isinstance(e, dict) else ""
        value = e.get("value", "") if isinstance(e, dict) else ""
        short_key = key.split(".")[-1].replace("_", " ")
        lines.append(f"  {i}. {short_key}: {value}")

    return "\n".join(lines)


def _handle_memory_delete(app: Any, ref: str) -> str:
    """Delete by number (from /memory list) or by key."""
    # Try as number first
    try:
        num = int(ref)
        entries = app.memory.search("default", "system", "user.")
        user_entries = [
            e for e in entries
            if isinstance(e, dict)
            and e.get("key", "").startswith("user.")
            and not e.get("key", "").startswith("session.")
            and not e.get("key", "").startswith("memory.reviewed")
        ]
        if 1 <= num <= len(user_entries):
            key = user_entries[num - 1]["key"]
            app.memory.delete("default", "system", key)
            short = key.split(".")[-1].replace("_", " ")
            return f"Deleted: {short}"
        else:
            return f"Invalid number. Use /memory list to see entries (1-{len(user_entries)})."
    except ValueError:
        pass

    # Try as key
    existing = app.memory.get("default", "system", ref)
    if existing is not None:
        app.memory.delete("default", "system", ref)
        return f"Deleted: {ref}"
    return f"Entry not found: {ref}"


def _handle_memory_set(app: Any, setting: str, value: str) -> str:
    """Set user settings like name, tone, language."""
    if setting == "name":
        app.memory.set("default", "system", "user.name", value, created_by="user")
        app.audit.log("memory.set", details={"setting": setting, "value": value})
        return f"Name set to: {value}"
    elif setting == "tone":
        app.memory.set("default", "system", "user.preference.tone", value, created_by="user")
        app.audit.log("memory.set", details={"setting": setting, "value": value})
        return f"Response tone set to: {value}"
    elif setting == "language" or setting == "lang":
        app.memory.set("default", "system", "user.preference.language", value, created_by="user")
        app.audit.log("memory.set", details={"setting": setting, "value": value})
        return f"Language preference set to: {value}"
    else:
        return f"Unknown setting: {setting}. Available: name, tone, language"
    """Delete a memory entry."""
    deleted = app.memory.delete("default", "system", key)
    if deleted:
        return f"Deleted: `{key}`"
    return f"Entry `{key}` not found."


def _handle_memory_clear(app: Any) -> str:
    """Clear all memory entries."""
    entries = app.memory.search("default", "system", "")
    count = 0
    for e in entries:
        key = e.get("key", "") if isinstance(e, dict) else ""
        if key and key != "user.name":  # Keep user name
            app.memory.delete("default", "system", key)
            count += 1
    return f"Cleared {count} memory entries. (User name preserved.)"


# ---------------------------------------------------------------------------
# /config (delegates to existing context.py)
# ---------------------------------------------------------------------------

def _handle_config(app: Any, args: list[str]) -> str:
    from mycelos.chat.context import handle_system_command
    if not args:
        return handle_system_command(app, "show config")
    return handle_system_command(app, "config " + " ".join(args))


# ---------------------------------------------------------------------------
# /agent
# ---------------------------------------------------------------------------

def _handle_agent(app: Any, args: list[str]) -> str:
    if not args:
        return _agent_list(app)

    if args[0] == "list":
        return _agent_list(app)

    agent_id = args[0]
    agent = app.agent_registry.get(agent_id)
    if not agent:
        return f"Agent '{agent_id}' not found."

    if len(args) == 1 or args[1] == "info":
        caps = ", ".join(agent["capabilities"]) or "none"
        models = app.agent_registry.get_models(agent_id, "execution")
        model_str = ", ".join(models) if models else "system default"
        return (
            f"**Agent: {agent['name']}**\n"
            f"  ID: `{agent_id}`\n"
            f"  Type: {agent['agent_type']}\n"
            f"  Status: {agent['status']}\n"
            f"  Capabilities: {caps}\n"
            f"  Models: {model_str}\n"
            f"  Reputation: {agent['reputation']}\n"
            f"  Code: {'yes' if agent.get('code_hash') else 'no'}"
        )

    action = args[1].lower()

    if action == "grant" and len(args) >= 3:
        capability = args[2]
        current_caps = agent.get("capabilities", [])
        if capability in current_caps:
            return f"Agent '{agent_id}' already has '{capability}'."
        app.agent_registry.set_capabilities(agent_id, current_caps + [capability])
        app.policy_engine.set_policy("default", agent_id, capability, "always")
        app.config.apply_from_state(state_manager=app.state_manager, trigger="grant")
        return f"Granted `{capability}` to `{agent_id}`. New config generation created."

    if action == "revoke" and len(args) >= 3:
        capability = args[2]
        current_caps = agent.get("capabilities", [])
        if capability not in current_caps:
            return f"Agent '{agent_id}' doesn't have '{capability}'."
        app.agent_registry.set_capabilities(
            agent_id, [c for c in current_caps if c != capability]
        )
        app.config.apply_from_state(state_manager=app.state_manager, trigger="revoke")
        return f"Revoked `{capability}` from `{agent_id}`. New config generation created."

    return f"Unknown action: {action}. Try: info, grant, revoke"


def _agent_list(app: Any) -> str:
    agents = app.agent_registry.list_agents()
    if not agents:
        return "No agents registered."
    lines = ["**Agents:**\n"]
    for a in agents:
        caps = ", ".join(a["capabilities"]) if a["capabilities"] else "none"
        lines.append(f"  `{a['id']}` ({a['agent_type']}) — {a['status']} [{caps}]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /connector
# ---------------------------------------------------------------------------

def _handle_cost(app: Any, args: list[str]) -> str:
    """Handle /cost commands — usage and cost summary."""
    import datetime

    period = args[0].lower() if args else "today"
    now = datetime.datetime.now(datetime.timezone.utc)

    if period == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        label = "Today"
    elif period == "week":
        since = now - datetime.timedelta(days=7)
        label = "Last 7 days"
    elif period == "month":
        since = now - datetime.timedelta(days=30)
        label = "Last 30 days"
    elif period == "all":
        since = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        label = "All time"
    else:
        return "Usage: `/cost` | `/cost week` | `/cost month` | `/cost all`"

    since_str = since.isoformat()

    try:
        # Total summary
        total = app.storage.fetchone(
            """SELECT COUNT(*) as calls, COALESCE(SUM(total_tokens), 0) as tokens,
               COALESCE(SUM(cost), 0) as cost
               FROM llm_usage WHERE created_at >= ?""",
            (since_str,),
        )

        # Per-model breakdown
        by_model = app.storage.fetchall(
            """SELECT model, COUNT(*) as calls,
               COALESCE(SUM(input_tokens), 0) as input_tok,
               COALESCE(SUM(output_tokens), 0) as output_tok,
               COALESCE(SUM(total_tokens), 0) as tokens,
               COALESCE(SUM(cost), 0) as cost
               FROM llm_usage WHERE created_at >= ?
               GROUP BY model ORDER BY cost DESC""",
            (since_str,),
        )

        if not total or total["calls"] == 0:
            return f"**{label}:** No LLM usage recorded yet."

        parts = [f"**LLM Usage — {label}**\n"]
        parts.append(f"  Total calls: {total['calls']}")
        parts.append(f"  Total tokens: {total['tokens']:,}")
        parts.append(f"  Total cost: **${total['cost']:.4f}**")

        if by_model:
            parts.append(f"\n**By Model:**\n")
            for m in by_model:
                parts.append(
                    f"  `{m['model']}`: {m['calls']} calls, "
                    f"{m['tokens']:,} tokens, ${m['cost']:.4f}"
                )

        # Per-purpose breakdown
        by_purpose = app.storage.fetchall(
            """SELECT purpose, COUNT(*) as calls, COALESCE(SUM(cost), 0) as cost
               FROM llm_usage WHERE created_at >= ?
               GROUP BY purpose ORDER BY cost DESC""",
            (since_str,),
        )
        if by_purpose and len(by_purpose) > 1:
            parts.append(f"\n**By Purpose:**\n")
            for p in by_purpose:
                parts.append(f"  {p['purpose'] or 'unknown'}: {p['calls']} calls, ${p['cost']:.4f}")

        return "\n".join(parts)

    except Exception as e:
        return f"Cost tracking error: {e}"


def _handle_sessions(app: Any, args: list[str]) -> str:
    """Handle /sessions commands — list and resume."""
    if args and args[0] == "resume" and len(args) >= 2:
        # Resume is handled by the REPL, not here — just show instruction
        return (
            f"To resume session `{args[1]}`, restart chat with:\n"
            f"  `mycelos chat --continue`\n\n"
            f"Or in the current session, the context from that session "
            f"is not loadable mid-conversation. Start a new `mycelos chat` to resume."
        )

    sessions = app.session_store.list_sessions()
    if not sessions:
        return "No sessions found."

    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    lines = [f"**Recent Sessions** ({len(sessions)})\n"]

    for s in sessions[:10]:
        sid = s.get("session_id", "?")
        msg_count = s.get("message_count", 0)
        timestamp = s.get("timestamp", "")
        user_id = s.get("user_id", "?")

        # Calculate age
        age_str = ""
        try:
            session_time = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            age = now - session_time
            hours = int(age.total_seconds() / 3600)
            if hours < 1:
                mins = int(age.total_seconds() / 60)
                age_str = f"{mins}m ago"
            elif hours < 24:
                age_str = f"{hours}h ago"
            else:
                days = int(hours / 24)
                age_str = f"{days}d ago"
        except (ValueError, TypeError):
            age_str = "?"

        # Get last user message preview
        preview = ""
        try:
            messages = app.session_store.load_messages(sid)
            for m in reversed(messages):
                if m.get("role") == "user":
                    preview = m["content"][:60]
                    break
        except Exception:
            pass

        lines.append(
            f"  `{sid[:8]}` — {msg_count} msgs, {age_str}"
            + (f'\n    _"{preview}"_' if preview else "")
        )

    lines.append(f"\nResume latest: `mycelos chat --continue`")
    return "\n".join(lines)


def _handle_mount(app: Any, args: list[str]) -> str:
    """Handle /mount commands — add, list, revoke."""
    mounts = app.mount_registry

    if not args:
        return _mount_list(mounts)

    action = args[0].lower()

    if action == "list":
        return _mount_list(mounts)
    elif action == "add" and len(args) >= 2:
        return _mount_add(app, mounts, args[1:])
    elif action == "revoke" and len(args) >= 2:
        return _mount_revoke(app, mounts, args[1])
    else:
        return (
            "Usage:\n"
            "  `/mount list` — Show mounted directories\n"
            "  `/mount add <path> --read` — Grant read access\n"
            "  `/mount add <path> --write` — Grant write access\n"
            "  `/mount add <path> --read --agent <id>` — For specific agent\n"
            "  `/mount revoke <id>` — Revoke access"
        )


def _mount_list(mounts: Any) -> str:
    active = mounts.list_mounts()
    if not active:
        return ("No directories mounted. Agents cannot access any files.\n\n"
                "Grant access with: `/mount add ~/path --read`")

    lines = [f"**Mounted Directories** ({len(active)})\n"]
    for m in active:
        scope = ""
        if m.get("agent_id"):
            scope = f" (agent: {m['agent_id']})"
        elif m.get("workflow_id"):
            scope = f" (workflow: {m['workflow_id']})"
        purpose = f" — {m['purpose']}" if m.get("purpose") else ""
        lines.append(f"  `{m['id'][:8]}` {m['path']} [{m['access']}]{scope}{purpose}")
    return "\n".join(lines)


def _mount_add(app: Any, mounts: Any, args: list[str]) -> str:
    path = args[0]
    access = "read"
    agent_id = None
    workflow_id = None
    purpose = None

    # Parse flags
    i = 1
    while i < len(args):
        flag = args[i].lower()
        if flag == "--read":
            access = "read"
        elif flag == "--write":
            access = "write"
        elif flag == "--read-write" or flag == "--rw":
            access = "read_write"
        elif flag == "--agent" and i + 1 < len(args):
            i += 1
            agent_id = args[i]
        elif flag == "--workflow" and i + 1 < len(args):
            i += 1
            workflow_id = args[i]
        elif flag == "--purpose" and i + 1 < len(args):
            i += 1
            purpose = args[i]
        i += 1

    from pathlib import Path
    expanded = Path(path).expanduser().resolve()
    if not expanded.exists():
        return f"Path does not exist: `{expanded}`"
    if not expanded.is_dir():
        return f"Not a directory: `{expanded}`"

    try:
        mount_id = mounts.add(
            path=path, access=access, purpose=purpose,
            agent_id=agent_id, workflow_id=workflow_id,
        )

        scope = ""
        if agent_id:
            scope = f" for agent `{agent_id}`"
        elif workflow_id:
            scope = f" for workflow `{workflow_id}`"

        return (
            f"**Directory mounted:**\n"
            f"  Path: `{expanded}`\n"
            f"  Access: {access}{scope}\n"
            f"  ID: `{mount_id[:8]}`\n\n"
            f"Agents with `filesystem.{access}` capability can now access this directory."
        )
    except Exception as e:
        return f"Failed to mount: {e}"


def _mount_revoke(app: Any, mounts: Any, mount_id: str) -> str:
    # Try prefix match
    all_mounts = mounts.list_mounts()
    target = None
    for m in all_mounts:
        if m["id"].startswith(mount_id):
            target = m
            break
    if not target:
        return f"Mount `{mount_id}` not found."

    mounts.revoke(target["id"])
    return f"Mount revoked: `{target['path']}` ({target['access']})"


def _handle_connector(app: Any, args: list[str]) -> str:
    """Handle /connector commands — list, add, remove, test."""
    if not args:
        return _connector_list(app)

    action = args[0].lower()

    if action == "list":
        return _connector_list(app)
    elif action == "search" and len(args) >= 2:
        return _connector_search(" ".join(args[1:]))
    elif action == "add" and len(args) >= 2:
        return _connector_add_smart(app, args[1:])
    elif action == "remove" and len(args) >= 2:
        return _connector_remove(app, args[1])
    elif action == "test" and len(args) >= 2:
        return _connector_test(app, args[1])
    else:
        return (
            "Usage:\n"
            "  `/connector list` — Show available and active connectors\n"
            "  `/connector add <name> <command> [--secret <key>]` — Add MCP server\n"
            "  `/connector add <name> --secret <key>` — Add built-in connector with token\n"
            "  `/connector remove <name>` — Remove a connector\n"
            "  `/connector test <name>` — Test a connector\n\n"
            "Examples:\n"
            "  `/connector add playwright npx -y @playwright/mcp`\n"
            "  `/connector add context7 npx -y @upstash/context7-mcp --secret YOUR_KEY`\n"
            "  `/connector add telegram --secret 12345:ABCdef`"
        )


def _connector_search(query: str) -> str:
    """Search the MCP Registry for servers."""
    from mycelos.connectors.mcp_search import search_mcp_servers, format_search_results
    results = search_mcp_servers(query)
    return format_search_results(results)


def _connector_list(app: Any) -> str:
    """List available recipes + active connectors."""
    from mycelos.connectors.mcp_recipes import RECIPES, is_node_available

    # Get active connectors from DB
    active = {}
    try:
        for c in app.connector_registry.list_connectors():
            active[c["id"]] = c
    except Exception:
        pass

    lines = ["**Available Connectors:**\n"]

    if not is_node_available():
        lines.append("⚠️  Node.js (npx) not found — MCP connectors need it.\n"
                      "   Install: `brew install node` (macOS) or https://nodejs.org\n")

    for recipe_id, recipe in RECIPES.items():
        status = "**active**" if recipe_id in active else "available"
        creds = " (API key needed)" if recipe.credentials else ""
        lines.append(f"- `{recipe_id}` — {recipe.name}{creds} [{status}]")
        lines.append(f"  _{recipe.description}_")

    # Show non-recipe connectors (custom MCP, builtin)
    for cid, c in active.items():
        if cid not in RECIPES:
            lines.append(f"- `{cid}` — {c['name']} [**active**]")

    lines.append(f"\nSetup: `/connector add <name>`")
    return "\n".join(lines)


def _connector_add_smart(app: Any, args: list[str]) -> str:
    """Smart connector add — detects command vs token, handles --secret.

    Syntax:
      /connector add <name>                                    → recipe lookup
      /connector add <name> npx -y @pkg                        → custom MCP, no auth
      /connector add <name> npx -y @pkg --secret <key>         → custom MCP + auth
      /connector add <name> --secret <token>                   → built-in with token
    """
    name = args[0]
    rest = args[1:]

    # Parse --secret from anywhere in the args
    secret = None
    clean_args: list[str] = []
    i = 0
    while i < len(rest):
        if rest[i] == "--secret" and i + 1 < len(rest):
            secret = rest[i + 1]
            i += 2
        else:
            clean_args.append(rest[i])
            i += 1

    # No extra args → recipe-based setup
    if not clean_args and not secret:
        return _connector_add(app, name)

    # Only --secret, no command → built-in connector with token
    if not clean_args and secret:
        return _connector_add_with_key(app, name, secret)

    # Has command args → custom MCP server
    command = " ".join(clean_args)

    # Validate the command is a known MCP launcher
    first_word = clean_args[0].lower() if clean_args else ""
    mcp_launchers = ("npx", "docker", "python", "python3", "node", "uvx", "deno", "bun")
    if first_word not in mcp_launchers:
        # Could be a token (old syntax: /connector add telegram <token>)
        if not secret:
            return _connector_add_with_key(app, name, " ".join(clean_args))
        return f"Unknown launcher: `{first_word}`. Use: {', '.join(mcp_launchers)}"

    # Look up required env vars from MCP Registry
    from mycelos.connectors.mcp_search import lookup_env_vars
    # Extract package name from command (e.g., "npx -y @upstash/context7-mcp" → "@upstash/context7-mcp")
    package_name = ""
    for arg in clean_args:
        if arg.startswith("@") or (not arg.startswith("-") and "/" in arg):
            package_name = arg
            break
    env_var_specs = lookup_env_vars(package_name) if package_name else []
    secret_vars = [ev for ev in env_var_specs if ev.get("secret")]

    # Register the custom MCP connector
    result = _connector_add_custom(app, name, command)

    # _connector_add_custom now returns a list of ChatEvents
    # Extract text from first system-response event to check success
    result_text = ""
    if isinstance(result, list):
        for ev in result:
            if hasattr(ev, "data") and "content" in ev.data:
                result_text += ev.data["content"]
    else:
        result_text = result

    # Store secret if provided
    extra_text = ""
    if secret and "registered" in result_text:
        env_var_name = secret_vars[0]["name"] if secret_vars else f"{name.upper().replace('-', '_')}_API_KEY"
        try:
            app.credentials.store_credential(
                f"connector:{name}",
                {"api_key": secret, "env_var": env_var_name},
                description=f"API key for {name} MCP connector",
            )
            extra_text = f"\n\n**Secret stored** as `{env_var_name}` (encrypted)."
        except Exception as e:
            extra_text = f"\n\n**Warning:** Failed to store secret: {e}"
    elif not secret and secret_vars and "registered" in result_text:
        var_names = ", ".join(f"`{ev['name']}`" for ev in secret_vars)
        extra_text = (
            f"\n\n**Note:** This server requires {var_names}.\n"
            f"Add it with: `/connector add {name} {command} --secret YOUR_KEY`"
        )

    # Append extra text to the system-response event
    if extra_text and isinstance(result, list):
        from mycelos.chat.events import system_response_event as _sre
        result = [
            _sre(ev.data["content"] + extra_text) if ev.type == "system-response" else ev
            for ev in result
        ]
    elif extra_text and isinstance(result, str):
        result += extra_text

    return result


def _connector_add(app: Any, recipe_id: str) -> str:
    """Set up a connector from a predefined recipe.

    Returns instructions — credential input happens separately
    because slash commands are non-interactive in gateway mode.
    For terminal mode, the actual setup happens via connector_cmd.
    """
    from mycelos.connectors.mcp_recipes import get_recipe, is_node_available

    recipe = get_recipe(recipe_id)
    if recipe is None:
        available = ", ".join(
            r.id for r in __import__("mycelos.connectors.mcp_recipes", fromlist=["list_recipes"]).list_recipes()
        )
        from mycelos.chat.events import system_response_event, suggested_actions_event
        return [
            system_response_event(
                f"Unknown connector: `{recipe_id}`.\n\n"
                f"**Available built-in connectors:** {available}\n\n"
                f"Not what you need? Try:\n"
                f"- `/connector search {recipe_id}` — search the MCP registry for community servers\n"
                f"- Ask me in chat: *\"How do I connect {recipe_id}?\"* — I'll help you find the right approach"
            ),
            suggested_actions_event([
                {"label": f"Search MCP registry for {recipe_id}", "command": f"/connector search {recipe_id}"},
                {"label": f"Ask: How to connect {recipe_id}?", "command": f"How do I connect {recipe_id} to Mycelos?"},
            ]),
        ]

    if recipe.requires_node and not is_node_available():
        return (
            f"**{recipe.name}** needs Node.js (npx).\n\n"
            f"Install it first:\n"
            f"  macOS: `brew install node`\n"
            f"  Linux: `sudo apt install nodejs`\n"
            f"  Windows: https://nodejs.org/en/download\n\n"
            f"Then try again: `/connector add {recipe_id}`"
        )

    # Check if already configured
    existing = app.connector_registry.get(recipe_id)
    if existing and existing.get("status") == "active":
        return f"**{recipe.name}** is already active. Use `/connector test {recipe_id}` to verify."

    # Build setup instructions
    parts = [f"**Setting up: {recipe.name}**\n"]
    parts.append(f"{recipe.description}\n")

    if recipe.credentials:
        parts.append("**Credentials needed:**\n")
        for cred in recipe.credentials:
            parts.append(f"  - `{cred['env_var']}`: {cred['name']}")
            if cred.get("help"):
                parts.append(f"    {cred['help']}")

        parts.append(
            f"\n**Important:** Do NOT paste your token as a chat message — it would be visible to the AI.\n"
            f"Instead, use the command with the token appended:\n"
            f"  `/connector add {recipe_id} <your-token>`"
        )
        from mycelos.chat.events import system_response_event, suggested_actions_event
        return [
            system_response_event("\n".join(parts)),
            suggested_actions_event([
                {"label": f"Enter: /connector add {recipe_id} <token>", "command": f"/connector add {recipe_id} ", "prefill": True},
            ]),
        ]
    else:
        # No credentials needed — register immediately
        try:
            app.connector_registry.register(
                recipe_id, recipe.name, "mcp",
                recipe.capabilities_preview,
                description=recipe.description,
                setup_type="mcp",
            )
            for cap in recipe.capabilities_preview:
                app.policy_engine.set_policy("default", None, cap, "always")
            parts.append(f"✓ **{recipe.name} activated!** No API key needed.")
            parts.append(f"  Capabilities: {', '.join(recipe.capabilities_preview)}")
        except Exception as e:
            parts.append(f"**Setup failed:** {e}")

    return "\n".join(parts)


def _connector_add_with_key(app: Any, recipe_id: str, api_key: str) -> str:
    """Set up a connector with an inline credential (from chat).

    For simple connectors: api_key is a plain token string.
    For email: api_key is a JSON string with email, password, server config.
    """
    import json as _json

    from mycelos.connectors.mcp_recipes import get_recipe

    recipe = get_recipe(recipe_id)
    if recipe is None:
        return f"Unknown connector: `{recipe_id}`."

    api_key = api_key.strip()
    if not api_key:
        return f"Empty key. Usage: `/connector add {recipe_id} <your-token>`"

    # Email connector: parse JSON credential with multi-field config
    if recipe_id == "email" and api_key.startswith("{"):
        try:
            email_config = _json.loads(api_key)
            app.credentials.store_credential("email", {
                "api_key": _json.dumps(email_config),
                "env_var": "EMAIL_CONFIG",
                "provider": "email",
            })
        except _json.JSONDecodeError:
            return "Invalid email configuration. Please try again."
    elif recipe.credentials:
        # Simple token credential
        cred_info = recipe.credentials[0]
        service_name = recipe_id
        env_var = cred_info.get("env_var", f"{recipe_id.upper()}_TOKEN")
        app.credentials.store_credential(service_name, {
            "api_key": api_key,
            "env_var": env_var,
        })

    # Register connector
    try:
        app.connector_registry.register(
            recipe_id, recipe.name, "mcp",
            recipe.capabilities_preview,
            description=recipe.description,
            setup_type="mcp",
        )
        for cap in recipe.capabilities_preview:
            app.policy_engine.set_policy("default", None, cap, "always")
        app.audit.log("connector.setup", details={
            "connector": recipe_id, "has_credential": True,
        })
    except Exception as e:
        return f"Setup failed: {e}"

    # Special: Telegram — add channel config + auto-detect user
    if recipe_id == "telegram":
        # Try to auto-detect user from getUpdates
        allowed_users: list[int] = []
        bot_username = ""
        try:
            import httpx as _httpx
            # Verify token and get bot info
            me_resp = _httpx.get(f"https://api.telegram.org/bot{api_key}/getMe", timeout=10)
            me_data = me_resp.json()
            if me_data.get("ok"):
                bot_username = me_data["result"].get("username", "")

            # Clear old updates, then check for new ones
            _httpx.get(f"https://api.telegram.org/bot{api_key}/getUpdates",
                       params={"offset": -1, "limit": 1}, timeout=10)
        except Exception:
            pass

        try:
            app.storage.execute(
                """INSERT OR REPLACE INTO channels (id, channel_type, mode, status, config, allowed_users)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("telegram", "telegram", "polling", "active", "{}",
                 _json.dumps(allowed_users)),
            )
        except Exception:
            pass

        bot_ref = f"@{bot_username}" if bot_username else "your bot"
        from mycelos.chat.events import system_response_event, suggested_actions_event
        return [
            system_response_event(
                f"**Telegram Bot configured!** Token stored (encrypted).\n\n"
                f"**Important — activate your bot:**\n"
                f"1. Open Telegram and send `/start` to {bot_ref}\n"
                f"2. Then restart the gateway\n\n"
                f"The first message you send will automatically add you to the allowlist. "
                f"Without this, the bot won't respond to anyone (security: fail-closed)."
            ),
            suggested_actions_event([
                {"label": "Restart Gateway", "command": "/restart"},
            ]),
        ]

    from mycelos.chat.events import system_response_event, suggested_actions_event
    return [
        system_response_event(
            f"**{recipe.name} configured!**\n\n"
            f"  Credential stored (encrypted)\n"
            f"  Capabilities: {', '.join(recipe.capabilities_preview)}\n\n"
            f"Restart the gateway to activate:"
        ),
        suggested_actions_event([
            {"label": "Restart Gateway", "command": "/restart"},
        ]),
    ]


def _validate_mcp_command(command: str) -> str | None:
    """Validate a custom MCP command. Returns error message if invalid, None if OK."""
    import shlex

    if not command or not command.strip():
        return "Command cannot be empty."

    # Block shell metacharacters that enable injection
    shell_metacharacters = set("; | & $ ` ( ) { } < > !".split())
    for char in shell_metacharacters:
        if char in command:
            return f"Command contains forbidden shell metacharacter: `{char}`"

    # Parse the executable name
    try:
        parts = shlex.split(command)
    except ValueError as e:
        return f"Malformed command: {e}"

    if not parts:
        return "Command cannot be empty."

    import os
    executable = os.path.basename(parts[0])

    # Blocklist: dangerous executables that should never be the primary command
    blocked_executables = {"bash", "sh", "zsh", "fish", "csh", "ksh", "dash",
                           "rm", "curl", "wget", "nc", "ncat", "netcat", "dd",
                           "mkfs", "fdisk", "eval", "exec", "sudo", "su"}
    if executable in blocked_executables:
        return f"Blocked dangerous executable: `{executable}`"

    # Allowlist: known safe MCP server launchers
    allowed_executables = {"npx", "node", "python", "python3", "uvx", "docker",
                           "deno", "bun"}
    if executable not in allowed_executables:
        return (
            f"Unknown executable: `{executable}`. "
            f"Allowed: {', '.join(sorted(allowed_executables))}"
        )

    return None


def _connector_add_custom(app: Any, name: str, command: str) -> str:
    """Add a custom MCP server."""
    # Validate command before registration (F-01: prevent command injection)
    validation_error = _validate_mcp_command(command)
    if validation_error:
        return f"**Invalid command:** {validation_error}"

    try:
        app.connector_registry.register(
            name, name, "mcp", [],
            description=f"Custom MCP server: {command}",
            setup_type="mcp",
        )
        # Store the command in connector metadata and trigger config generation
        app.connector_registry.update_description(name, f"MCP: {command}")
        from mycelos.chat.events import system_response_event, suggested_actions_event
        return [
            system_response_event(
                f"**Custom connector `{name}` registered.**\n"
                f"  Command: `{command}`\n\n"
                f"Restart the gateway to activate:"
            ),
            suggested_actions_event([
                {"label": "Restart Gateway", "command": "/restart"},
            ]),
        ]
    except Exception as e:
        return f"Failed to add custom connector: {e}"


def _connector_remove(app: Any, connector_id: str) -> str:
    """Remove/deactivate a connector."""
    existing = app.connector_registry.get(connector_id)
    if not existing:
        return f"Connector `{connector_id}` not found."

    try:
        app.connector_registry.set_status(connector_id, "inactive")
        return f"Connector `{connector_id}` deactivated. Credentials preserved for reactivation."
    except Exception as e:
        return f"Failed to remove connector: {e}"


def _connector_test(app: Any, connector_id: str) -> str | list:
    """Test a connector's connection with a live check."""
    existing = app.connector_registry.get(connector_id)
    if not existing:
        return f"Connector `{connector_id}` not found."

    status = existing.get("status", "unknown")

    # Telegram: check if token exists and bot is reachable
    if connector_id == "telegram":
        return _test_telegram(app, connector_id)

    # MCP connectors: check if credential exists
    cred = None
    try:
        cred = app.credentials.get_credential(f"connector:{connector_id}")
        if not cred:
            cred = app.credentials.get_credential(connector_id)
    except Exception:
        pass

    if cred:
        return f"Connector `{connector_id}` is **{status}**. Credential stored."
    else:
        from mycelos.chat.events import system_response_event, suggested_actions_event
        return [
            system_response_event(
                f"Connector `{connector_id}` is **{status}** but has **no credential stored**.\n"
                f"It won't work without an API key."
            ),
            suggested_actions_event([
                {"label": f"Add credential", "command": f"/connector add {connector_id} ", "prefill": True},
            ]),
        ]


def _test_telegram(app: Any, connector_id: str) -> str | list:
    """Test Telegram bot: check token + call getMe API."""
    from mycelos.chat.events import system_response_event, suggested_actions_event

    # Check if token is stored
    cred = None
    try:
        cred = app.credentials.get_credential("telegram")
    except Exception:
        pass

    if not cred or not cred.get("api_key"):
        return [
            system_response_event(
                "**Telegram Bot is registered but has no token.**\n\n"
                "You need to:\n"
                "1. Create a bot via @BotFather in Telegram (send `/newbot`)\n"
                "2. Copy the token and paste it here"
            ),
            suggested_actions_event([
                {"label": "Paste token", "command": "/connector add telegram ", "prefill": True},
            ]),
        ]

    # Token exists — try to reach Telegram API
    token = cred["api_key"]
    try:
        import urllib.request
        import json as _json
        url = f"https://api.telegram.org/bot{token}/getMe"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
            if data.get("ok"):
                bot = data["result"]
                bot_name = bot.get("first_name", "Unknown")
                bot_username = bot.get("username", "?")
                return (
                    f"**Telegram Bot is working!**\n\n"
                    f"  Bot: {bot_name} (@{bot_username})\n"
                    f"  Token: stored (encrypted)\n\n"
                    f"Run `mycelos serve` to connect — then message your bot in Telegram."
                )
            else:
                return f"**Telegram API error:** {data.get('description', 'Unknown error')}"
    except Exception as e:
        error = str(e)
        if "401" in error or "Unauthorized" in error:
            return [
                system_response_event(
                    "**Telegram token is invalid or expired.**\n\n"
                    "Get a new token from @BotFather and update it:"
                ),
                suggested_actions_event([
                    {"label": "Update token", "command": "/connector add telegram ", "prefill": True},
                ]),
            ]
        return f"**Telegram connection test failed:** {e}"


# ---------------------------------------------------------------------------
# /schedule
# ---------------------------------------------------------------------------

def _handle_schedule(app: Any, args: list[str]) -> str:
    if not args or args[0] == "list":
        return _handle_schedule_list(app)
    elif args[0] == "add" and len(args) >= 2:
        return _handle_schedule_add(app, args[1:])
    elif args[0] == "delete" and len(args) >= 2:
        return _handle_schedule_delete(app, args[1])
    elif args[0] == "pause" and len(args) >= 2:
        return _handle_schedule_pause(app, args[1])
    elif args[0] == "resume" and len(args) >= 2:
        return _handle_schedule_resume(app, args[1])
    else:
        return (
            "Usage:\n"
            "  /schedule — List scheduled tasks\n"
            "  /schedule add <workflow> --cron \"0 7 * * *\" — Add schedule\n"
            "  /schedule delete <id> — Delete schedule\n"
            "  /schedule pause <id> — Pause schedule\n"
            "  /schedule resume <id> — Resume schedule"
        )


def _handle_schedule_list(app: Any) -> str:
    tasks = app.schedule_manager.list_tasks()
    if not tasks:
        return "No scheduled tasks."
    lines = ["**Scheduled Tasks:**\n"]
    for i, st in enumerate(tasks, 1):
        lines.append(
            f"  {i}. `{st['id'][:8]}` **{st['workflow_id']}** — {st['schedule']} "
            f"({st['status']}, {st.get('run_count', 0)} runs)"
        )
    return "\n".join(lines)


def _handle_schedule_add(app: Any, args: list[str]) -> str:
    """Add a scheduled task: /schedule add <workflow_id> --cron "0 7 * * *" """
    workflow_id = args[0]
    cron_expr = None

    # Parse --cron argument
    for i, arg in enumerate(args):
        if arg == "--cron" and i + 1 < len(args):
            cron_expr = args[i + 1].strip('"').strip("'")
            break

    if not cron_expr:
        # Try if the second arg is the cron directly
        if len(args) >= 2:
            cron_expr = " ".join(args[1:]).strip('"').strip("'")
        else:
            return "Missing cron expression. Example: /schedule add news-summary --cron \"0 7 * * *\""

    # Validate workflow exists
    try:
        workflows = app.workflow_registry.list_workflows(status="active")
        wf_ids = [w["id"] for w in workflows]
        if workflow_id not in wf_ids:
            return f"Workflow '{workflow_id}' not found. Available: {', '.join(wf_ids)}"
    except Exception:
        pass

    # Validate cron expression
    try:
        from mycelos.scheduler.schedule_manager import parse_next_run
        next_run = parse_next_run(cron_expr)
    except Exception as e:
        return f"Invalid cron expression: {e}"

    # Add the schedule
    task_id = app.schedule_manager.add(
        workflow_id=workflow_id,
        schedule=cron_expr,
    )

    app.audit.log("schedule.created", details={"workflow_id": workflow_id, "cron": cron_expr, "task_id": task_id})

    return (
        f"**Scheduled!**\n"
        f"  Workflow: {workflow_id}\n"
        f"  Cron: {cron_expr}\n"
        f"  Next run: {next_run.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"  ID: {task_id[:8]}"
    )


def _handle_schedule_delete(app: Any, ref: str) -> str:
    tasks = app.schedule_manager.list_tasks()
    # Try as number
    try:
        num = int(ref)
        if 1 <= num <= len(tasks):
            task_id = tasks[num - 1]["id"]
            app.schedule_manager.delete(task_id)
            app.audit.log("schedule.deleted", details={"task_id": task_id})
            return f"Deleted schedule {task_id[:8]}."
    except ValueError:
        pass
    # Try as ID prefix
    for st in tasks:
        if st["id"].startswith(ref):
            app.schedule_manager.delete(st["id"])
            app.audit.log("schedule.deleted", details={"task_id": st["id"]})
            return f"Deleted schedule {st['id'][:8]}."
    return f"Schedule not found: {ref}"


def _handle_schedule_pause(app: Any, ref: str) -> str:
    tasks = app.schedule_manager.list_tasks()
    for st in tasks:
        if st["id"].startswith(ref) or st["workflow_id"] == ref:
            app.schedule_manager.update_status(st["id"], "paused")
            return f"Paused schedule {st['id'][:8]} ({st['workflow_id']})."
    return f"Schedule not found: {ref}"


def _handle_schedule_resume(app: Any, ref: str) -> str:
    tasks = app.schedule_manager.list_tasks()
    for st in tasks:
        if st["id"].startswith(ref) or st["workflow_id"] == ref:
            app.schedule_manager.update_status(st["id"], "active")
            return f"Resumed schedule {st['id'][:8]} ({st['workflow_id']})."
    return f"Schedule not found: {ref}"


# ---------------------------------------------------------------------------
# /workflow
# ---------------------------------------------------------------------------

def _handle_workflow(app: Any, args: list[str]) -> str:
    if args and args[0] == "delete" and len(args) >= 2:
        wf_id = args[1]
        try:
            app.storage.execute("DELETE FROM scheduled_tasks WHERE workflow_id = ?", (wf_id,))
            app.storage.execute("DELETE FROM workflows WHERE id = ?", (wf_id,))
            app.audit.log("workflow.deleted", details={"workflow_id": wf_id})
            app.config.apply_from_state(
                state_manager=app.state_manager,
                description=f"Workflow deleted: {wf_id}",
                trigger="workflow_delete",
            )
            return f"Deleted workflow `{wf_id}` and its schedules."
        except Exception as e:
            return f"Failed to delete: {e}"

    if args and args[0] == "show" and len(args) >= 2:
        wf_id = args[1]
        workflows = app.workflow_registry.list_workflows()
        for w in workflows:
            if w["id"] == wf_id:
                import json
                steps = w.get("steps", [])
                lines = [f"**Workflow: {w['id']}**", f"  {w.get('description', '')}",  f"  Version: {w['version']}", ""]
                for i, s in enumerate(steps, 1):
                    if isinstance(s, dict):
                        lines.append(f"  {i}. [{s.get('agent', '?')}] {s.get('action', '?')}")
                    else:
                        lines.append(f"  {i}. {s}")
                return "\n".join(lines)
        return f"Workflow `{wf_id}` not found."

    if args and args[0] == "runs":
        runs = app.workflow_run_manager.list_runs()
        if not runs:
            return "No workflow runs."
        lines = ["**Workflow Runs:**\n"]
        for r in runs[:10]:
            lines.append(
                f"  `{r['id'][:8]}` {r['workflow_id']} — {r['status']} "
                f"(step: {r.get('current_step', '?')}, cost: ${r.get('cost', 0):.4f})"
            )
        return "\n".join(lines)

    workflows = app.workflow_registry.list_workflows()
    if not workflows:
        return "No workflows defined."
    lines = ["**Workflows:**\n"]
    for w in workflows:
        lines.append(
            f"  `{w['id']}` — {w.get('description', '')} "
            f"(v{w['version']}, {len(w.get('steps', []))} steps)"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /model
# ---------------------------------------------------------------------------

def _handle_model(app: Any, args: list[str]) -> str:
    from mycelos.chat.context import handle_system_command
    return handle_system_command(app, "model list")


def _handle_reload(app: Any, args: list[str]) -> str:
    """Reload MCP connectors — re-discovers tools without full restart."""
    from mycelos.gateway.server import _start_mcp_connectors

    # Disconnect existing
    try:
        app.mcp_manager.disconnect_all()
    except Exception:
        pass

    # Restart
    _start_mcp_connectors(app, debug=False)

    connected = app.mcp_manager.list_connected() if app._mcp_manager else []
    tool_count = app.mcp_manager.tool_count if app._mcp_manager else 0

    app.audit.log("gateway.reloaded", details={
        "mcp_connectors": connected,
        "mcp_tools": tool_count,
    })

    if connected:
        lines = [f"**Reloaded** — {len(connected)} MCP server(s):"]
        for cid in connected:
            lines.append(f"- {cid}")
        lines.append(f"\n{tool_count} tools available.")
        return "\n".join(lines)
    else:
        return "**Reloaded** — no MCP connectors to start.\nAdd one with: /connector add <name>"


# ---------------------------------------------------------------------------
# /bg — Background tasks
# ---------------------------------------------------------------------------

STATUS_EMOJI = {
    "pending": "⏳",
    "running": "🔄",
    "completed": "✅",
    "failed": "❌",
    "cancelled": "🚫",
    "waiting_for_user": "⏸️",
    "cost_exceeded": "💰",
}


def _handle_bg(app: Any, args: list[str]) -> str:
    """Background task management."""
    sub = args[0] if args else "list"

    if sub == "list":
        return _bg_list(app)
    elif sub == "cancel" and len(args) > 1:
        return _bg_cancel(app, args[1])
    elif sub == "approve" and len(args) > 1:
        return _bg_approve(app, args[1])
    elif sub == "detail" and len(args) > 1:
        return _bg_detail(app, args[1])
    else:
        return (
            "**Usage:**\n"
            "  `/bg` — List background tasks\n"
            "  `/bg cancel <id>` — Cancel a task\n"
            "  `/bg approve <id>` — Approve a waiting task\n"
            "  `/bg detail <id>` — Show task details"
        )


def _bg_list(app: Any) -> str:
    """List recent background tasks (last 20) with status, type, step progress, cost."""
    tasks = app.task_runner.get_tasks_for_user("default")
    if not tasks:
        return "No background tasks."

    lines = ["**Background Tasks:**\n"]
    for task in tasks[:20]:
        emoji = STATUS_EMOJI.get(task["status"], "❓")
        task_id = task["id"][:8]
        task_type = task["task_type"]
        step = task.get("current_step") or "-"
        cost = task.get("cost") or 0
        lines.append(f"{emoji} `{task_id}` {task_type} — step: {step}, cost: ${cost:.4f}")

    return "\n".join(lines)


def _bg_cancel(app: Any, task_id_prefix: str) -> str:
    """Cancel a background task by ID prefix."""
    task = _find_task(app, task_id_prefix)
    if not task:
        return f"No task found matching `{task_id_prefix}`."
    if app.task_runner.cancel(task["id"]):
        return f"Task `{task['id'][:8]}` cancelled."
    return f"Could not cancel task `{task_id_prefix}`."


def _bg_approve(app: Any, task_id_prefix: str) -> str:
    """Approve a waiting background task."""
    task = _find_task(app, task_id_prefix)
    if not task:
        return f"No task found matching `{task_id_prefix}`."
    if app.task_runner.approve_waiting(task["id"]):
        return f"Task `{task['id'][:8]}` approved, resuming."
    return f"Could not approve task `{task_id_prefix}`."


def _bg_detail(app: Any, task_id_prefix: str) -> str:
    """Show detailed info for a background task."""
    task = _find_task(app, task_id_prefix)
    if not task:
        return f"No task found matching `{task_id_prefix}`."

    import json

    lines = [f"**Task {task['id'][:8]}**\n"]
    lines.append(f"Type: {task['task_type']}")
    lines.append(f"Status: {task['status']}")
    lines.append(f"Cost: ${(task.get('cost') or 0):.4f}")
    if task.get("cost_limit"):
        lines.append(f"Cost limit: ${task['cost_limit']:.2f}")
    lines.append(f"Created: {task['created_at']}")
    if task.get("started_at"):
        lines.append(f"Started: {task['started_at']}")
    if task.get("completed_at"):
        lines.append(f"Completed: {task['completed_at']}")
    if task.get("error"):
        lines.append(f"Error: {task['error']}")
    if task.get("result"):
        try:
            result = json.loads(task["result"])
            summary = result.get("summary", str(result))
            lines.append(f"Result: {summary}")
        except (json.JSONDecodeError, TypeError):
            lines.append(f"Result: {task['result']}")

    return "\n".join(lines)


def _find_task(app: Any, task_id_prefix: str) -> dict | None:
    """Find a task by ID prefix match."""
    tasks = app.task_runner.get_tasks_for_user("default")
    for task in tasks:
        if task["id"].startswith(task_id_prefix):
            return task
    return None


# ---------------------------------------------------------------------------
# /demo
# ---------------------------------------------------------------------------

def _handle_demo(app: Any, args: list[str]) -> str | list:
    """Feature demonstrations."""
    if not args:
        return "**Usage:** `/demo widget` — Show all widget types"

    sub = args[0].lower()
    if sub == "widget":
        return _demo_widgets()

    return f"Unknown demo: `{sub}`. Available: `widget`"


def _demo_widgets() -> list:
    """Demonstrate all widget types via ChatEvents."""
    from mycelos.chat.events import widget_event
    from mycelos.widgets import (
        Choice, ChoiceBox, CodeBlock, Compose, Confirm,
        ImageBlock, ProgressBar, StatusCard, Table, TextBlock,
    )

    widget = Compose(children=[
        TextBlock(text="Widget Demo — All 9 Types", weight="bold"),

        TextBlock(text="1. Table"),
        Table(
            headers=["Agent", "Status", "Tasks", "Cost"],
            rows=[
                ["creator", "active", "12", "$0.34"],
                ["planner", "active", "8", "$0.21"],
                ["auditor", "idle", "3", "$0.05"],
            ],
        ),

        TextBlock(text="2. StatusCard"),
        StatusCard(
            title="System Health",
            facts={"Agents": "3 active", "Memory": "42 entries", "Uptime": "2h 15m"},
            style="success",
        ),

        TextBlock(text="3. ProgressBar"),
        ProgressBar(label="Workflow: backup-daily", current=7, total=10),

        TextBlock(text="4. CodeBlock"),
        CodeBlock(
            code='from mycelos.widgets import Table, StatusCard\n\nwidget = Table(headers=["A"], rows=[["1"]])',
            language="python",
        ),

        TextBlock(text="5. ChoiceBox"),
        ChoiceBox(
            prompt="How should I proceed?",
            options=[
                Choice(id="retry", label="Retry the failed step"),
                Choice(id="skip", label="Skip and continue"),
                Choice(id="abort", label="Abort workflow"),
            ],
        ),

        TextBlock(text="6. Confirm"),
        Confirm(prompt="Register agent 'web-scraper'?", danger=True),

        TextBlock(text="7. ImageBlock"),
        ImageBlock(url="https://example.com/arch.png", alt="Architecture diagram", caption="Mycelos System Overview"),
    ])

    return [widget_event(widget)]


# ---------------------------------------------------------------------------
# /inbox
# ---------------------------------------------------------------------------

def _handle_inbox(app: Any, args: list[str]) -> str:
    """Handle /inbox — list, clear inbox items."""
    from mycelos.files.inbox import InboxManager
    inbox = InboxManager(app.data_dir / "inbox")

    sub = args[0] if args else "list"

    if sub == "list":
        files = inbox.list_files()
        if not files:
            return "Inbox is empty."
        lines = ["**Inbox:**\n"]
        for f in files:
            size = f.stat().st_size
            size_str = f"{size / 1024:.0f}KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f}MB"
            lines.append(f"  `{f.name}` ({size_str})")
        return "\n".join(lines)

    elif sub == "clear":
        files = inbox.list_files()
        for f in files:
            inbox.remove(f)
        return f"Cleared {len(files)} file(s) from inbox."

    else:
        return "Usage: `/inbox` — list files | `/inbox clear` — clear all"


def _handle_run(app: Any, args: list[str]) -> str | list:
    """/run <workflow_id> [key=value ...] — execute a workflow immediately."""
    if not args:
        # List available workflows
        workflows = app.workflow_registry.list_workflows()
        if not workflows:
            return "No workflows registered. Create one first."
        lines = ["**Available workflows:**\n"]
        for wf in workflows:
            inputs = wf.get("inputs", [])
            if isinstance(inputs, str):
                try:
                    inputs = __import__("json").loads(inputs)
                except (ValueError, TypeError):
                    inputs = []
            input_hint = ""
            if inputs:
                params = " ".join(f"{i['name']}=..." for i in inputs if isinstance(i, dict) and i.get("required"))
                if params:
                    input_hint = f" {params}"
            lines.append(f"  `/run {wf['id']}{input_hint}` — {wf.get('description', wf.get('name', wf['id']))}")
        return "\n".join(lines)

    workflow_id = args[0]
    workflow = app.workflow_registry.get(workflow_id)
    if not workflow:
        return f"Workflow `{workflow_id}` not found. Use `/run` to see available workflows."

    # Parse key=value pairs from remaining args
    inputs = _parse_run_inputs(args[1:])

    # Execute via the run_workflow tool
    from mycelos.tools.workflow import execute_run_workflow
    context = {
        "app": app,
        "user_id": "default",
        "session_id": "",
        "agent_id": "mycelos",
    }
    result = execute_run_workflow({"workflow_id": workflow_id, "inputs": inputs}, context)

    if isinstance(result, dict) and result.get("error"):
        return f"**Run failed:** {result['error']}"

    status = result.get("status", "unknown")
    result_text = result.get("result", "")
    cost = result.get("cost", 0)

    parts = [f"**Workflow:** {workflow.get('name', workflow_id)}"]
    if status == "success":
        if result_text:
            parts.append(f"\n{result_text}")
        else:
            parts.append("Completed successfully.")
    elif status == "needs_clarification":
        parts.append(f"\n**Question:** {result.get('clarification', '')}")
    else:
        parts.append(f"**Status:** {status}")
        if result.get("error"):
            parts.append(f"**Error:** {result['error']}")

    if cost and cost > 0:
        parts.append(f"\n_Cost: ${cost:.4f}_")

    from mycelos.chat.events import system_response_event
    return [system_response_event("\n".join(parts))]


def _parse_run_inputs(args: list[str]) -> dict:
    """Parse key=value pairs from /run arguments.

    Supports: key=value, key="value with spaces"
    Remaining non-key=value args are joined as 'query'.
    """
    inputs: dict[str, str] = {}
    free_text: list[str] = []

    # Join all args to handle quoted values properly
    raw = " ".join(args)
    i = 0
    while i < len(raw):
        # Skip whitespace
        if raw[i] == " ":
            i += 1
            continue

        # Look for key=value pattern
        eq_pos = raw.find("=", i)
        next_space = raw.find(" ", i)
        if eq_pos != -1 and (next_space == -1 or eq_pos < next_space):
            key = raw[i:eq_pos]
            i = eq_pos + 1
            if i < len(raw) and raw[i] == '"':
                # Quoted value
                end_quote = raw.find('"', i + 1)
                if end_quote == -1:
                    inputs[key] = raw[i + 1:]
                    break
                inputs[key] = raw[i + 1:end_quote]
                i = end_quote + 1
            else:
                # Unquoted value — until next space
                end = raw.find(" ", i)
                if end == -1:
                    inputs[key] = raw[i:]
                    break
                inputs[key] = raw[i:end]
                i = end
        else:
            # Not a key=value, collect as free text
            if next_space == -1:
                free_text.append(raw[i:])
                break
            free_text.append(raw[i:next_space])
            i = next_space + 1

    # If there's free text and no explicit query, use it as query
    if free_text and "query" not in inputs and "topic" not in inputs:
        inputs["query"] = " ".join(free_text)

    return inputs


def _handle_restart(app: Any, args: list[str]) -> list:
    """Handle /restart — trigger Gateway restart via restart.txt."""
    from mycelos.chat.events import system_response_event, ChatEvent
    restart_dir = app.data_dir / "tmp"
    restart_dir.mkdir(parents=True, exist_ok=True)
    restart_file = restart_dir / "restart.txt"
    restart_file.write_text("restart requested")
    app.audit.log("gateway.restart_requested")
    return [
        system_response_event("Restarting Gateway..."),
        ChatEvent(type="restart", data={"delay": 5}),
    ]


def _handle_credential(app: Any, args: list[str]) -> str:
    """Handle /credential — list, store, delete credentials from chat."""
    sub = args[0] if args else "list"

    if sub == "list":
        try:
            services = app.credentials.list_services()
        except Exception:
            return "Credential store not available (master key missing?)."
        if not services:
            return "No credentials stored. Use: `/credential store <service> <key>`"
        lines = ["**Stored Credentials:**\n"]
        for s in sorted(services):
            try:
                cred = app.credentials.get_credential(s)
                key = cred.get("api_key", "") if cred else ""
                preview = f"{key[:4]}...{key[-4:]}" if len(key) > 12 else "****"
            except Exception:
                preview = "?"
            lines.append(f"  `{s}` — {preview}")
        return "\n".join(lines)

    elif sub == "store" and len(args) >= 3:
        # /credential store openai sk-abc123...
        service = args[1]
        api_key = " ".join(args[2:]).strip()
        if not api_key:
            return f"Usage: `/credential store {service} <your-api-key>`"
        try:
            # Known env var mapping
            _ENV_VARS = {
                "anthropic": "ANTHROPIC_API_KEY",
                "openai": "OPENAI_API_KEY",
                "openrouter": "OPENROUTER_API_KEY",
                "gemini": "GEMINI_API_KEY",
                "telegram": "TELEGRAM_BOT_TOKEN",
                "github": "GITHUB_PERSONAL_ACCESS_TOKEN",
                "brave": "BRAVE_API_KEY",
            }
            env_var = _ENV_VARS.get(service, f"{service.upper()}_API_KEY")
            app.credentials.store_credential(service, {
                "api_key": api_key,
                "env_var": env_var,
            })
            app.audit.log("credential.stored", details={"service": service})
            from mycelos.chat.events import system_response_event, suggested_actions_event
            return [
                system_response_event(
                    f"**Credential `{service}` stored (encrypted).**\n\n"
                    f"Restart to activate:"
                ),
                suggested_actions_event([
                    {"label": "Restart Gateway", "command": "/restart"},
                ]),
            ]
        except Exception as e:
            return f"Failed to store credential: {e}"

    elif sub == "store" and len(args) == 2:
        service = args[1]
        return (
            f"Paste your key:\n"
            f"  `/credential store {service} <your-api-key>`\n\n"
            f"The key is stored encrypted — the AI never sees it."
        )

    elif sub == "store":
        return (
            "Usage: `/credential store <service> <key>`\n\n"
            "Services: `anthropic`, `openai`, `gemini`, `openrouter`, `telegram`, `github`, `brave`"
        )

    elif sub == "delete" and len(args) >= 2:
        service = args[1]
        try:
            app.credentials.delete_credential(service)
            app.audit.log("credential.deleted", details={"service": service})
            return f"Credential `{service}` deleted."
        except Exception as e:
            return f"Failed to delete: {e}"

    else:
        return (
            "Usage:\n"
            "  `/credential list` — show stored credentials\n"
            "  `/credential store <service> <key>` — store a credential\n"
            "  `/credential delete <service>` — delete a credential"
        )
