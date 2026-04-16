#!/usr/bin/env python3
"""Debug script: Simulate what happens when a Telegram message arrives.

Tests the full chain: ChatService → LLM prompt → tool calls → response
WITHOUT needing Telegram or the Gateway running.

Usage:
    python scripts/debug_telegram_chat.py "Welche Connectors habe ich?"
    python scripts/debug_telegram_chat.py "Suche AI News"
    python scripts/debug_telegram_chat.py --db   # just dump DB state
"""

import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

DATA_DIR = Path.home() / ".maicel"


def load_app():
    """Load Maicel app with master key."""
    if not os.environ.get("MAICEL_MASTER_KEY"):
        key_file = DATA_DIR / ".master_key"
        if key_file.exists():
            os.environ["MAICEL_MASTER_KEY"] = key_file.read_text().strip()

    from maicel.app import App
    return App(DATA_DIR)


def dump_db_state(app):
    """Dump relevant DB state for debugging."""
    print("\n=== CONNECTORS ===")
    for row in app.storage.fetchall("SELECT id, name, status FROM connectors ORDER BY id"):
        caps = app.storage.fetchall(
            "SELECT capability FROM connector_capabilities WHERE connector_id = ?", (row["id"],)
        )
        cap_list = [c["capability"] for c in caps]
        print(f"  {row['id']:25s} {row['status']:8s} caps={cap_list}")

    print("\n=== POLICIES ===")
    for row in app.storage.fetchall("SELECT user_id, agent_id, resource, decision FROM policies ORDER BY resource"):
        print(f"  {row['user_id']:15s} agent={row['agent_id'] or '*':10s} {row['resource']:25s} → {row['decision']}")

    print("\n=== CHANNELS ===")
    for row in app.storage.fetchall("SELECT * FROM channels ORDER BY id"):
        print(f"  {row['id']:15s} mode={row['mode']:10s} status={row['status']:8s} allowed={row['allowed_users']}")

    print("\n=== CREDENTIALS (services only) ===")
    for row in app.storage.fetchall("SELECT service FROM credentials ORDER BY service"):
        print(f"  {row['service']}")

    print("\n=== MEMORY (user.*) ===")
    for row in app.storage.fetchall(
        "SELECT key, value FROM memory_entries WHERE key LIKE 'user.%' ORDER BY key"
    ):
        print(f"  {row['key']:40s} = {str(row['value'])[:60]}")

    print("\n=== RECENT AUDIT (last 10) ===")
    for row in app.storage.fetchall(
        "SELECT event_type, details, created_at FROM audit_events ORDER BY created_at DESC LIMIT 10"
    ):
        details = row["details"] or ""
        if len(details) > 80:
            details = details[:80] + "..."
        print(f"  {row['created_at'][-12:]:12s} {row['event_type']:30s} {details}")


def test_context(app):
    """Show what the LLM sees as context."""
    from maicel.chat.context import build_context
    ctx = build_context(app)
    print("\n=== LLM CONTEXT ===")
    print(ctx)


def test_system_prompt(app):
    """Show the full system prompt."""
    from maicel.chat.service import ChatService
    svc = ChatService(app)
    user_name = app.memory.get("default", "system", "user.name")
    prompt = svc.get_system_prompt(user_name=user_name)
    print(f"\n=== SYSTEM PROMPT ({len(prompt)} chars) ===")
    # Show first 500 and last 500
    if len(prompt) > 1200:
        print(prompt[:600])
        print(f"\n... ({len(prompt) - 1200} chars omitted) ...\n")
        print(prompt[-600:])
    else:
        print(prompt)


def test_policy_for_tools(app):
    """Check what policy each chat tool gets."""
    tools = [
        "search_web", "search_news", "http_get",
        "memory_read", "memory_write",
        "filesystem_read", "filesystem_write", "filesystem_list",
        "system_status", "search_mcp_servers",
    ]
    print("\n=== POLICY PER TOOL (user=default) ===")
    for tool in tools:
        decision = app.policy_engine.evaluate("default", None, tool)
        # Check if explicit
        explicit = app.storage.fetchone(
            "SELECT decision FROM policies WHERE user_id = 'default' AND agent_id IS NULL AND resource = ?",
            (tool,),
        )
        source = "explicit" if explicit else "default"
        print(f"  {tool:25s} → {decision:10s} ({source})")

    print("\n=== POLICY PER TOOL (user=telegram:1234567890) ===")
    for tool in tools:
        decision = app.policy_engine.evaluate("telegram:1234567890", None, tool)
        explicit = app.storage.fetchone(
            "SELECT decision FROM policies WHERE user_id = 'telegram:1234567890' AND agent_id IS NULL AND resource = ?",
            (tool,),
        )
        source = "explicit" if explicit else "default"
        print(f"  {tool:25s} → {decision:10s} ({source})")


def test_chat_message(app, message: str, user_id: str = "telegram:1234567890"):
    """Send a message through ChatService and show what happens."""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
    # Only debug for our code, not LiteLLM
    logging.getLogger("maicel").setLevel(logging.DEBUG)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Start MCP servers (like the gateway does)
    from maicel.gateway.server import _start_mcp_connectors
    _start_mcp_connectors(app, debug=False)
    mcp_tools = app.mcp_manager.list_tools() if app._mcp_manager else []
    print(f"\n=== MCP TOOLS ({len(mcp_tools)}) ===")
    for t in mcp_tools[:5]:
        has_params = bool(t.get("input_schema", {}).get("properties"))
        print(f"  {t['name']:40s} params={'yes' if has_params else 'NO!'}")
    if len(mcp_tools) > 5:
        print(f"  ... and {len(mcp_tools) - 5} more")

    from maicel.chat.service import ChatService

    svc = ChatService(app)
    session_id = svc.create_session(user_id=user_id)

    print(f"\n=== SENDING MESSAGE ===")
    print(f"  User: {user_id}")
    print(f"  Session: {session_id[:8]}")
    print(f"  Message: {message}")
    print()

    try:
        events = svc.handle_message(
            message=message,
            session_id=session_id,
            user_id=user_id,
        )

        print(f"\n=== EVENTS ({len(events)}) ===")
        for e in events:
            if e.type == "text":
                content = e.data.get("content", "")
                print(f"  TEXT: {content[:200]}")
                if len(content) > 200:
                    print(f"        ...({len(content)} chars total)")
            elif e.type == "text-delta":
                pass  # skip deltas
            elif e.type == "error":
                print(f"  ERROR: {e.data.get('message', '?')}")
            elif e.type == "step-progress":
                print(f"  STEP: {e.data.get('step_id', '?')} → {e.data.get('status', '?')}")
            elif e.type == "done":
                tokens = e.data.get("tokens", 0)
                model = e.data.get("model", "?")
                cost = e.data.get("cost", 0)
                print(f"  DONE: {tokens} tokens, model={model}, cost=${cost:.4f}")
            elif e.type == "system-response":
                print(f"  SYSTEM: {e.data.get('content', '')[:200]}")
            elif e.type == "plan":
                print(f"  PLAN: task={e.data.get('task_id', '?')[:8]}")
            elif e.type == "session":
                pass  # skip
            elif e.type == "agent":
                print(f"  AGENT: {e.data.get('agent', '?')}")
            else:
                print(f"  {e.type}: {str(e.data)[:100]}")

    except Exception as exc:
        print(f"\n  EXCEPTION: {exc}")
        import traceback
        traceback.print_exc()

    # Show audit events from this session
    print(f"\n=== AUDIT EVENTS (this run) ===")
    recent = app.storage.fetchall(
        "SELECT event_type, details FROM audit_events ORDER BY created_at DESC LIMIT 5"
    )
    for r in recent:
        print(f"  {r['event_type']:30s} {(r['details'] or '')[:80]}")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scripts/debug_telegram_chat.py --db          # dump DB state")
        print("  python scripts/debug_telegram_chat.py --context     # show LLM context")
        print("  python scripts/debug_telegram_chat.py --prompt      # show system prompt")
        print("  python scripts/debug_telegram_chat.py --policies    # check tool policies")
        print('  python scripts/debug_telegram_chat.py "message"     # send a test message')
        print('  python scripts/debug_telegram_chat.py --all "msg"   # everything + message')
        sys.exit(1)

    app = load_app()

    if sys.argv[1] == "--db":
        dump_db_state(app)
    elif sys.argv[1] == "--context":
        test_context(app)
    elif sys.argv[1] == "--prompt":
        test_system_prompt(app)
    elif sys.argv[1] == "--policies":
        test_policy_for_tools(app)
    elif sys.argv[1] == "--all":
        message = sys.argv[2] if len(sys.argv) > 2 else "Hallo, was kannst du?"
        dump_db_state(app)
        test_context(app)
        test_policy_for_tools(app)
        test_chat_message(app, message)
    else:
        message = " ".join(sys.argv[1:])
        test_chat_message(app, message)


if __name__ == "__main__":
    main()
