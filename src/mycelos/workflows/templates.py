"""Built-in workflow templates that ship with Mycelos.

Every builtin workflow MUST have `plan`, `model`, and `allowed_tools`.
The seeder enforces this on load; missing fields raise at import time,
not at runtime on the user's first /run call.
"""

import json
import logging
from typing import Any

logger = logging.getLogger("mycelos.workflows.templates")


# Default model for conversational/planning workflows. Research-heavy
# flows may override to sonnet or opus.
_DEFAULT_MODEL_CHAT = "anthropic/claude-haiku-4-5"
_DEFAULT_MODEL_RESEARCH = "anthropic/claude-sonnet-4-6"


BUILTIN_WORKFLOWS = [
    # ------------------------------------------------------------------
    # Brainstorming — multi-phase: diverge → challenge → structure → save
    # ------------------------------------------------------------------
    {
        "id": "brainstorming-interview",
        "name": "Brainstorming Session",
        "description": (
            "A guided multi-phase brainstorming session on a topic: diverge "
            "(collect raw ideas), challenge (probe weaknesses, ask hard "
            "questions), converge (group into themes, pick strongest), and "
            "save the result as a structured knowledge base note."
        ),
        "goal": "Run a real brainstorming session and capture the result",
        "steps": json.dumps([
            {"id": "frame", "action": "conversation", "description": "Clarify the topic and what good would look like"},
            {"id": "diverge", "action": "conversation", "description": "Collect many raw ideas without judgement"},
            {"id": "challenge", "action": "conversation", "description": "Probe weaknesses, ask devil's advocate questions"},
            {"id": "converge", "action": "conversation", "description": "Group ideas into themes, identify the strongest"},
            {"id": "save", "action": "note_write", "description": "Write a structured brainstorming note"},
        ]),
        "scope": json.dumps(["knowledge.write"]),
        "tags": json.dumps(["builtin", "brainstorming", "ideas", "interview"]),
        "status": "active",
        "created_by": "system",
        "plan": (
            "You are a brainstorming facilitator. Your job is to run a real "
            "brainstorming session with the user on the topic provided in "
            "inputs (or ask for it if missing), then save the outcome.\n\n"
            "Run these four phases, one message at a time. Each phase is a "
            "back-and-forth with the user — do not skip ahead.\n\n"
            "PHASE 1 — FRAME (1–2 turns)\n"
            "Ask what the topic is and what a successful outcome would look "
            "like. If the user already gave the topic in inputs, just "
            "confirm and clarify the goal in one sentence.\n\n"
            "PHASE 2 — DIVERGE (2–4 turns)\n"
            "Ask for raw ideas. Rules: quantity over quality, no judgement, "
            "wild ideas welcome. If the user runs dry, prompt with angles "
            "they haven't tried (e.g. 'what would the lazy version look "
            "like?', 'what would a 10x budget enable?', 'what would a "
            "hostile competitor do?'). Aim for at least 6–8 raw ideas.\n\n"
            "PHASE 3 — CHALLENGE (2–4 turns)\n"
            "Now switch roles. Play devil's advocate on the 2–3 most "
            "interesting ideas: what are the weakest assumptions? what "
            "could kill this? what's been tried and failed? who would "
            "hate this? Push back gently but honestly. Let the user "
            "defend or refine.\n\n"
            "PHASE 4 — CONVERGE (1–2 turns)\n"
            "Group the surviving ideas into 2–4 themes. Ask the user which "
            "theme feels most promising and why. Capture any next actions.\n\n"
            "FINAL STEP — SAVE\n"
            "Call note_write ONCE at the end with:\n"
            "  title: 'Brainstorming: <topic>'\n"
            "  type: 'note'\n"
            "  tags: ['brainstorming']\n"
            "  content: a structured markdown note with sections:\n"
            "    ## Topic\n    ## Goal\n    ## Raw ideas (bullet list)\n"
            "    ## Challenges raised\n    ## Surviving themes\n"
            "    ## Chosen direction\n    ## Next actions\n\n"
            "Then tell the user where the note was saved and stop.\n\n"
            "Tone: curious, direct, never sycophantic. Push back when an "
            "idea is weak. This is a working session, not a pep talk."
        ),
        "model": _DEFAULT_MODEL_CHAT,
        "allowed_tools": json.dumps(["note_write"]),
    },

    # ------------------------------------------------------------------
    # Research & Summary
    # ------------------------------------------------------------------
    {
        "id": "research-summary",
        "name": "Research & Summary",
        "description": (
            "Search the web for a topic, read the most relevant sources, and "
            "create a summarized knowledge base note with findings and source "
            "links."
        ),
        "goal": "Research a topic and create a summary note",
        "steps": json.dumps([
            {"id": "search", "action": "search_web", "description": "Search for the topic"},
            {"id": "deep_read", "action": "http_get", "description": "Read most relevant pages"},
            {"id": "summarize", "action": "note_write", "description": "Create KB note with findings and source links"},
        ]),
        "scope": json.dumps(["search.web", "http.get", "knowledge.write"]),
        "tags": json.dumps(["builtin", "research", "summary", "analysis"]),
        "status": "active",
        "created_by": "system",
        "plan": (
            "You are a research agent. The user has given you a topic to "
            "research (in inputs.topic, or ask for it if missing).\n\n"
            "1. Call search_web with a focused query for the topic. Prefer "
            "recent, authoritative sources (official docs, reputable news, "
            "primary sources). Get ~5–8 results.\n"
            "2. Pick the 3 most promising results and call http_get on "
            "each to read the actual content. Skip obviously low-value "
            "pages (SEO farms, listicles).\n"
            "3. Synthesize: identify the 3–5 key findings that answer "
            "the user's topic, noting any contradictions between sources.\n"
            "4. Call note_write ONCE with:\n"
            "   title: '<topic> — research summary'\n"
            "   type: 'note'\n"
            "   tags: ['research']\n"
            "   content: markdown with sections:\n"
            "     ## Topic\n     ## Key findings (3–5 bullets)\n"
            "     ## Open questions / contradictions\n     ## Sources (URL list)\n\n"
            "5. Report the note path to the user and stop.\n\n"
            "Do not hallucinate. If the sources don't answer the question, "
            "say so in the note and in the final response."
        ),
        "model": _DEFAULT_MODEL_RESEARCH,
        "allowed_tools": json.dumps(["search_web", "http_get", "note_write"]),
    },

    # ------------------------------------------------------------------
    # Onboarding
    # ------------------------------------------------------------------
    {
        "id": "onboarding",
        "name": "Welcome Onboarding",
        "description": (
            "First-time user setup conversation: learn the user's name, "
            "goals, and preferences; suggest the most relevant next steps. "
            "Stores profile facts in memory and marks onboarding complete."
        ),
        "goal": "Guide new user through initial setup via conversation",
        "steps": json.dumps([
            {"id": "greeting", "action": "conversation", "description": "Welcome user, ask their name"},
            {"id": "goals", "action": "conversation", "description": "Ask what user wants to do"},
            {"id": "suggest", "action": "conversation", "description": "Recommend connectors and workflows"},
            {"id": "save", "action": "memory_set", "description": "Persist profile facts"},
        ]),
        "scope": json.dumps(["memory.write"]),
        "tags": json.dumps(["builtin", "onboarding", "setup"]),
        "status": "active",
        "created_by": "system",
        "plan": (
            "You are running the first-time onboarding for a new Mycelos "
            "user. Keep it short, warm, and concrete — never more than 4 "
            "exchanges.\n\n"
            "1. Greet them, introduce yourself as Mycelos in one sentence, "
            "ask their name.\n"
            "2. When they answer, call memory_set(scope='system', "
            "key='user.name', value=<name>). Then ask in ONE question what "
            "they most want help with — pick 2–3 concrete examples based "
            "on common uses: organizing knowledge, daily briefings, "
            "research, brainstorming, email triage.\n"
            "3. Based on their answer, recommend 1–2 concrete next actions: "
            "either a `/run <workflow>` command, a `/connector add <name>` "
            "suggestion, or just 'try asking me about X'. Store their "
            "stated goal via memory_set(scope='system', key='user.goals', "
            "value=<goal>).\n"
            "4. Mark onboarding complete with memory_set(scope='system', "
            "key='onboarding.completed', value='true'). Close with a warm "
            "one-liner and stop.\n\n"
            "Never ask multiple questions in one message. Never lecture. "
            "If they seem in a hurry, collapse steps 2–3 into a single "
            "message."
        ),
        "model": _DEFAULT_MODEL_CHAT,
        "allowed_tools": json.dumps(["memory_set"]),
    },

    # ------------------------------------------------------------------
    # Daily briefing
    # ------------------------------------------------------------------
    {
        "id": "daily-briefing",
        "name": "Daily Briefing",
        "description": (
            "Morning overview: pulls open and overdue tasks from the "
            "knowledge base and presents a concise briefing with priorities "
            "and suggested focus for the day."
        ),
        "goal": "Give the user a daily status overview",
        "steps": json.dumps([
            {"id": "overdue", "action": "note_search", "description": "Get overdue tasks"},
            {"id": "today", "action": "note_search", "description": "Get tasks due today"},
            {"id": "briefing", "action": "conversation", "description": "Present briefing"},
        ]),
        "scope": json.dumps(["knowledge.read"]),
        "tags": json.dumps(["builtin", "briefing", "daily", "tasks"]),
        "status": "active",
        "created_by": "system",
        "plan": (
            "You are the user's morning briefing agent. Produce a short, "
            "actionable daily overview — never more than ~200 words.\n\n"
            "1. Call note_search(type='task', status='open', overdue=True) "
            "to find overdue tasks.\n"
            "2. Call note_search(type='task', status='open') to find all "
            "open tasks.\n"
            "3. Produce the briefing as a single text response with this "
            "structure:\n"
            "   **Good morning.**\n"
            "   \n"
            "   🔴 **Overdue (<N>):** (bullet list, max 5; if more, say "
            "'and X more')\n"
            "   \n"
            "   📋 **Today's focus:** (pick the 1–3 most important open "
            "tasks for today based on reminder date, tags, or recency)\n"
            "   \n"
            "   💡 **Suggestion:** (one sentence: which task to start "
            "with and why)\n\n"
            "If there are no tasks at all, say so cheerfully and suggest "
            "starting a brainstorming or research session. Do not invent "
            "tasks. Do not call any other tools."
        ),
        "model": _DEFAULT_MODEL_CHAT,
        "allowed_tools": json.dumps(["note_search"]),
    },

    # ------------------------------------------------------------------
    # Knowledge organizer
    # ------------------------------------------------------------------
    {
        "id": "knowledge-organizer",
        "name": "Knowledge Organizer",
        "description": (
            "Organize and enhance the knowledge base: classify untagged notes into topics, "
            "generate or update topic summaries, discover connections between notes, "
            "and report inconsistencies or gaps. Like a librarian that keeps your wiki tidy."
        ),
        "goal": "Classify, summarize, connect, and clean up knowledge base notes",
        "steps": json.dumps([
            {"id": "scan", "action": "note_search",
             "description": "Find all notes without a topic or tagged 'unclassified'. Also list all existing topics for context."},
            {"id": "classify", "action": "note_move",
             "description": "For each unclassified note: read it, determine the best topic (create new topic if needed), move it there."},
            {"id": "summarize", "action": "note_write",
             "description": "For each topic with 3+ notes: generate or update a topic overview note that summarizes key points and lists all notes in the topic."},
            {"id": "connect", "action": "note_link",
             "description": "Find related notes across topics. Add backlinks between notes that reference similar concepts."},
            {"id": "health_check", "action": "conversation",
             "description": "Report findings: notes classified, summaries created/updated, connections found, potential duplicates, gaps, or inconsistencies."},
        ]),
        "scope": json.dumps(["knowledge.read", "knowledge.write"]),
        "tags": json.dumps(["builtin", "knowledge", "organization", "wiki", "cleanup"]),
        "status": "active",
        "created_by": "system",
        "plan": (
            "You are the Knowledge Organizer Agent. Your job is to keep the user's "
            "knowledge base tidy like a librarian.\n\n"
            "IMPORTANT: A 'topic' in Mycelos is a real note with type='topic'. The UI's "
            "topic tree only shows folders that have a corresponding type='topic' record. "
            "Setting parent_path on a regular note is NOT enough — you must also create "
            "the topic note itself, otherwise the UI shows 'Uncategorized'.\n\n"
            "Steps:\n"
            "1. Call note_search with query='' (no filter) to list ALL notes. Group them by "
            "their current parent_path. Note which parent_path values exist.\n"
            "2. Call note_search with type='topic' to list existing topic notes. Compare with "
            "step 1 — every distinct parent_path (e.g. 'topics/architecture') that does NOT "
            "already have a matching type='topic' note is a missing topic.\n"
            "3. For each missing topic: call note_write with type='topic', a clear human "
            "title (e.g. 'Architecture'), and a short description as content. The path will "
            "be derived automatically.\n"
            "4. For notes that still have no parent_path (true uncategorized): read each one "
            "with note_read, pick the best existing topic (or create a new one via step 3), "
            "then call note_move(path, new_parent_path).\n"
            "5. For each topic with 3+ child notes: write or update a topic overview note "
            "summarizing key points and listing all notes in that topic.\n"
            "6. Report a short summary: how many topics you created, how many notes you "
            "classified, any duplicates or gaps you noticed.\n\n"
            "Be concise. Don't ask the user questions — just organize. If you find nothing "
            "to do, say so and stop."
        ),
        "model": _DEFAULT_MODEL_RESEARCH,
        "allowed_tools": json.dumps([
            "note_search", "note_read", "note_write", "note_move",
        ]),
    },
]


# Validate at import time — missing fields are a development bug, not a runtime one.
_REQUIRED_FIELDS = ("id", "name", "description", "goal", "steps", "plan", "model", "allowed_tools")
for _wf in BUILTIN_WORKFLOWS:
    for _field in _REQUIRED_FIELDS:
        if not _wf.get(_field):
            raise RuntimeError(
                f"BUILTIN_WORKFLOWS entry {_wf.get('id', '?')!r} is missing "
                f"required field {_field!r}. Every builtin workflow must have "
                f"plan/model/allowed_tools set — otherwise WorkflowAgent will "
                f"crash on /run."
            )


def seed_builtin_workflows(app: Any) -> int:
    """Register built-in workflows. Inserts new ones and hard-overwrites
    system-owned existing rows so plan/model/allowed_tools updates always
    land on existing installs.

    Returns the number of rows inserted or upgraded.
    """
    count = 0
    for wf in BUILTIN_WORKFLOWS:
        plan = wf["plan"]
        model = wf["model"]
        allowed_tools = wf["allowed_tools"]
        existing = app.workflow_registry.get(wf["id"])
        if existing is None:
            app.storage.execute(
                "INSERT INTO workflows (id, name, description, goal, steps, scope, tags, status, created_by, plan, model, allowed_tools) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    wf["id"], wf["name"], wf["description"], wf["goal"],
                    wf["steps"], wf["scope"], wf["tags"], wf["status"], wf["created_by"],
                    plan, model, allowed_tools,
                ),
            )
            logger.info("seeded builtin workflow %s", wf["id"])
            count += 1
        elif wf.get("created_by") == "system":
            # Hard overwrite — system-owned builtin workflows always re-sync
            # from the template. User-owned workflows with the same ID are
            # not touched.
            app.storage.execute(
                "UPDATE workflows SET name=?, description=?, goal=?, steps=?, scope=?, tags=?, "
                "plan=?, model=?, allowed_tools=? WHERE id=?",
                (
                    wf["name"], wf["description"], wf["goal"],
                    wf["steps"], wf["scope"], wf["tags"],
                    plan, model, allowed_tools,
                    wf["id"],
                ),
            )
            count += 1
    return count
