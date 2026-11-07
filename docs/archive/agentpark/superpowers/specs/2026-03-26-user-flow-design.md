# User Flow — Design Specification

**Date:** 2026-03-26
**Status:** DRAFT
**Scope:** Init → Onboarding → Routing — the complete user journey from install to productive use

---

## 1. Overview

The goal: **2 minutes from install to first conversation.** After that,
an intelligent onboarding workflow guides the user through setup. Once
onboarding is done, the system routes every request through the right
path: existing workflow, planner, or creator.

### The Three Phases

```
Phase 1: Init (2 minutes)
  mycelos init → API key → done, gateway starts

Phase 2: Onboarding (first chat session, one-time)
  Workflow asks: name, goals, connectors, preferences
  → Sets up Telegram, email, etc. interactively

Phase 3: Normal Operation
  User says something → System finds the right path:
    1. Matching workflow? → Execute
    2. Planner can handle? → Ad-hoc plan
    3. Needs new agent? → Creator
```

---

## 2. Phase 1: Simplified Init

### Current Problem

`mycelos init` asks too many questions (provider, model selection, etc.).
Most users just want to get started with their Anthropic or OpenAI key.

### New Init Flow

```
$ mycelos init

  Welcome to Mycelos!

  Paste your API key or Ollama server URL:
  > sk-ant-...

  ✓ Anthropic key detected
  ✓ Database initialized
  ✓ Default model: claude-sonnet-4-6
  ✓ Built-in workflows ready (3)

  Testing connection...
  ✓ API reachable — Claude responded!

  Start chatting: mycelos chat
  Or start the server: mycelos gateway
```

**One prompt, auto-detect, connectivity check, done.**

### Provider Auto-Detection

| Input | Detection | Default Model |
|-------|-----------|---------------|
| `sk-ant-...` | Anthropic | claude-sonnet-4-6 |
| `sk-...` | OpenAI | gpt-4o |
| `sk-or-...` | OpenRouter | openrouter/anthropic/claude-sonnet-4-6 |
| `AIza...` | Google (Gemini) | gemini/gemini-2.5-flash |
| `http://...` or `https://...` | Ollama / local server | auto-discover from server |
| Anything else | Ask: "Which provider?" | User selects |

### Ollama / Local Server Detection

When the user pastes a URL:

```
$ mycelos init

  Paste your API key or Ollama server URL:
  > http://192.168.1.50:11434

  Checking server...
  ✓ Ollama server found at 192.168.1.50:11434

  Available models:
    1. llama3 (8B)
    2. mistral (7B)
    3. codellama (13B)

  Which model? [1]:
  > 1

  ✓ Configured: llama3 via Ollama (192.168.1.50:11434)
  Testing...
  ✓ Model responded!
```

The system calls `/api/tags` on the Ollama server to discover models.

### Google (Gemini) Detection

Google API keys start with `AIza`. When detected:

```
  ✓ Google (Gemini) key detected
  ✓ Default model: gemini-2.5-flash
  ✓ Bonus: Gemini also supports embeddings and STT!
```

### Connectivity Check

After storing the credential, init makes a real API call to verify:

```python
def _check_connectivity(provider, model, credential_proxy):
    """Send a tiny test prompt to verify the API key works."""
    try:
        broker = LiteLLMBroker(default_model=model, credential_proxy=credential_proxy)
        response = broker.complete([
            {"role": "user", "content": "Say 'hello' in one word."}
        ])
        if response.content:
            return True, response.content.strip()
    except Exception as e:
        return False, str(e)
    return False, "No response"
```

If it fails:
```
  ✗ Connection failed: Invalid API key
    Check your key and try again: mycelos credential store anthropic
```

If it succeeds:
```
  ✓ API reachable — model responded with: "Hello"
```

### Smart Defaults per Provider

| Provider | Chat Model | Embedding | STT |
|----------|-----------|-----------|-----|
| Anthropic | claude-sonnet-4-6 | Local (no OpenAI key) | Not available |
| OpenAI | gpt-4o | text-embedding-3-small | whisper-1 |
| OpenRouter | claude-sonnet-4-6 (via OR) | Local | Not available |
| Google | gemini-2.5-flash | Gemini embedding | Gemini STT |
| Ollama | auto-discover | Local | Not available |

### Unrecognized Key Format

```
  Paste your API key or Ollama server URL:
  > some-unknown-key-format

  I couldn't detect the provider. Which one?
    1. Anthropic (Claude)
    2. OpenAI (GPT, Whisper)
    3. OpenRouter (multi-provider)
    4. Google (Gemini)
    5. Ollama / local server

  > 1
  ✓ Stored as Anthropic key
```

### Advanced Setup

Users who want more control use the existing commands anytime:

```
mycelos credential store openai      # Add another provider
mycelos model add                    # Add/configure models
mycelos model test                   # Test connectivity
mycelos connector setup telegram     # Set up Telegram
```

These are NOT part of init — they're available anytime, including during
the onboarding conversation.

---

## 3. Phase 2: Onboarding Workflow

### Trigger

On the **first chat message ever** (no previous sessions), the system
detects: "This is a new user" and starts the onboarding workflow
instead of normal conversation.

Detection: check if `users` table has a record for this user_id AND
if they have completed onboarding (Memory key: `system.onboarding_completed`).

### Onboarding Steps

The onboarding is a **built-in workflow** (`onboarding`) that runs as
a structured conversation:

```
Mycelos: "Welcome! I'm Mycelos, your personal AI assistant.
         Let me help you set up. What's your name?"

User: "Stefan"
→ Memory: user.name = "Stefan"

Mycelos: "Hi Stefan! What would you like to use Mycelos for?
         Some examples:
         • Personal task management & reminders
         • Email processing & organization
         • Research & knowledge collection
         • Software development support
         • All of the above"

User: "Task management and email"
→ Memory: user.goals = ["task_management", "email"]
→ Suggests relevant connectors

Mycelos: "Great! For email, I can connect to Gmail.
         Want to set that up now? (You can also do this later)"

User: "Later"

Mycelos: "No problem. How about Telegram?
         With Telegram, you can message me from your phone —
         including voice messages.
         Want to set it up? It takes about 2 minutes."

User: "Yes"

Mycelos: "Here's how:
         1. Open Telegram and message @BotFather
         2. Send /newbot and follow the instructions
         3. Copy the bot token
         Then run: /connector setup telegram
         Or just paste the token here."

User: "123456:ABC-DEF..."
→ Stores credential, starts polling
→ "✓ Telegram connected! Send me a message there to test."

Mycelos: "You're all set! Here's what you can do:
         • 'Remember that...' — save notes & facts
         • 'What tasks are due?' — manage tasks
         • 'Research X' — web research with summary
         • 'Create an agent for...' — build automations
         • /help — see all commands

         What would you like to do first?"
```

### Onboarding Workflow Definition

```yaml
id: onboarding
name: Welcome Onboarding
description: >
  First-time user setup: name, goals, connector suggestions,
  optional Telegram/email setup. Runs once, then marked complete.
steps:
  - id: greeting
    action: conversation
    description: Ask name, store in memory
  - id: goals
    action: conversation
    description: Ask what user wants to do, suggest connectors
  - id: connectors
    action: conversation
    description: Offer to set up Telegram, email, GitHub
  - id: summary
    action: conversation
    description: Show what's available, suggest first actions
tags: [builtin, onboarding, setup]
```

### Completion

After onboarding finishes:
```python
app.memory.set("default", "system", "onboarding_completed", "true")
```

The workflow is then removed from the active workflow list (or marked
as completed) so it doesn't interfere with normal workflow matching.

### Skip Option

At any point the user can say "skip" or "later" and the onboarding
ends. The system marks it as completed anyway — the user can always
set things up later via `/connector setup` or chat commands.

---

## 4. Phase 3: Normal Operation — Routing Flow

### The Decision Tree

```
User message arrives
  |
  v
Is this a slash command (/help, /cost, /inbox)?
  → Yes: execute directly, no LLM
  |
  v
Is there an active workflow for this session?
  → Yes: continue workflow (like interview)
  |
  v
Is this a simple request? (greeting, note, question, tool call)
  → Classifier: CONVERSATION
  → Mycelos handles with tools (note_write, search, file_analyze, etc.)
  |
  v
Is this a complex request? (brainstorming, research, analysis)
  → Classifier: TASK_REQUEST
  → Planner searches workflows (embedding match)
    → Match found? Execute workflow
    → No match? Create ad-hoc plan
    → Needs new capability? → Creator
  |
  v
Does user explicitly want a new agent?
  → Classifier: CREATE_AGENT
  → Creator Agent interview
```

### Workflow Lifecycle

Built-in workflows are always available:
- `brainstorming-interview` — Idea collection
- `research-summary` — Research + summarize
- `daily-briefing` — Task overview

User-created workflows (via Planner) are stored in DB and become
searchable. They grow organically as the user uses the system.

### Workflow Discovery Priority

1. **Exact match** — Workflow name/tags match closely
2. **Embedding similarity** — Description matches user intent
3. **Planner judgment** — LLM decides if a workflow fits
4. **Ad-hoc plan** — No workflow, Planner creates steps
5. **Creator** — Needs new agent/automation

---

## 5. Core Workflows (Built-in)

### Shipped with Mycelos

| Workflow | Trigger | Result |
|----------|---------|--------|
| `onboarding` | First chat ever | User configured |
| `brainstorming-interview` | "Collect ideas", "Brainstorm" | KB note |
| `research-summary` | "Research X", "Find out about" | KB note |
| `daily-briefing` | "What's up", "Briefing" | Task list + news |

### Removable After Use

`onboarding` is removed from active workflows after completion.
It can be re-triggered with `/onboarding` if needed.

### Dynamic Workflows

When the user repeatedly does something similar, the system suggests
creating a workflow (Phase 2 — Workflow Auto-Discovery):

```
"You've asked for Git stats twice now.
 Shall I create a 'weekly-git-stats' workflow?"
```

---

## 6. Init Technical Changes

### Simplified init_cmd.py

```python
@click.command()
def init_cmd():
    """Initialize Mycelos — one key, ready to go."""
    console.print("\n  [bold]Welcome to Mycelos![/bold]\n")

    # 1. API Key
    api_key = click.prompt("  Enter your API key (Anthropic or OpenAI)")

    # 2. Detect provider
    if api_key.startswith("sk-ant-"):
        provider = "anthropic"
        default_model = "anthropic/claude-sonnet-4-6"
    elif api_key.startswith("sk-"):
        provider = "openai"
        default_model = "openai/gpt-4o"
    else:
        console.print("  [yellow]Unknown key format — assuming OpenAI compatible[/yellow]")
        provider = "openai"
        default_model = api_key_to_model(api_key)

    # 3. Initialize
    app = App(data_dir)
    app.initialize()

    # 4. Store credential
    app.credentials.store_credential(provider, {
        "api_key": api_key,
        "env_var": f"{provider.upper()}_API_KEY",
    })

    # 5. Register model
    app.model_registry.register_model(default_model, provider)

    # 6. Done
    console.print(f"\n  ✓ {provider.title()} key stored")
    console.print(f"  ✓ Default model: {default_model}")
    console.print(f"  ✓ Built-in workflows ready")
    console.print(f"\n  [bold]Start chatting:[/bold] mycelos chat")
    console.print(f"  [bold]Or start server:[/bold] mycelos gateway\n")
```

### Onboarding Detection in ChatService

```python
def handle_message(self, message, session_id, user_id):
    # Check if onboarding needed (first-time user)
    onboarding_done = self._app.memory.get("default", "system", "onboarding_completed")
    if not onboarding_done and session_id not in self._active_workflows:
        # Start onboarding workflow
        self._start_workflow(session_id, "onboarding", user_id)
        return self._handle_workflow_message(message, session_id)

    # ... normal routing
```

---

## 7. Embedding Provider for Init

### The Problem

Embeddings and STT need specific providers. Not every provider supports
everything.

### What Works With Each Provider

```
Anthropic only:
  → Chat: ✓ Claude
  → Embeddings: local model (sentence-transformers, 80MB)
  → STT: ✗ (needs OpenAI or Google)

OpenAI only:
  → Chat: ✓ GPT-4o
  → Embeddings: ✓ text-embedding-3-small
  → STT: ✓ Whisper

Google (Gemini) only:
  → Chat: ✓ Gemini
  → Embeddings: ✓ Gemini embedding
  → STT: ✓ Gemini audio

OpenRouter:
  → Chat: ✓ (any model)
  → Embeddings: local model
  → STT: ✗ (needs OpenAI or Google)

Ollama:
  → Chat: ✓ (local model)
  → Embeddings: local model
  → STT: ✗

Best setup (Anthropic + OpenAI):
  → Chat: Claude (best quality)
  → Embeddings: OpenAI (best quality)
  → STT: Whisper (best quality)
```

### Onboarding Explains This Naturally

The onboarding workflow only asks about embeddings/STT when relevant:

```
Mycelos: "Want to use voice messages? (requires Telegram + speech recognition)"
  → "You'll need an OpenAI key for that. Want to add one?"
  → Or if Google: "Your Gemini key already supports speech recognition!"

Mycelos: "Want better search in your notes? (Knowledge Base with embeddings)"
  → If OpenAI/Google key present: "Already set up!"
  → If Anthropic only: "I'll use a local model (80MB download). OK?"
```

---

## 8. Connector Onboarding

### Intelligent Suggestions

Based on the user's stated goals:

| Goal | Suggested Connectors |
|------|---------------------|
| Task management | Telegram (mobile), Knowledge Base |
| Email | Gmail (Google connector) |
| Development | GitHub, Filesystem |
| Research | Web Search (Brave/DuckDuckGo) |
| All | Telegram + Gmail + GitHub |

### Setup Flow in Chat

The user doesn't need to leave the chat. When they say "yes" to a
connector suggestion:

```
Mycelos: "To set up Telegram, I need a bot token from @BotFather.
         Here's how to get one:
         1. Open Telegram, search for @BotFather
         2. Send /newbot
         3. Choose a name for your bot
         4. Copy the token (looks like: 123456:ABC-DEF...)

         Paste it here when you have it, or say 'skip'."

User: "7706222429:AAH..."
→ /connector setup telegram (internally)
→ "✓ Telegram bot connected! Try sending me a message there."
```

---

## 9. Testing Strategy

### Unit Tests

- Init: one-key flow, provider detection, credential storage
- Onboarding detection: first-time vs returning user
- Workflow routing: classifier → planner → workflow match
- Connector setup via chat: token detection + validation

### Integration Tests

- Full flow: init → first chat → onboarding starts → completes
- Returning user: onboarding skipped, normal routing
- Workflow matching: "brainstorm ideas" → brainstorming workflow

### Manual Tests

- Fresh install: `mycelos init` → `mycelos chat` → onboarding
- Telegram setup during onboarding
- Skip onboarding → can still set up later

---

## 10. Scope Boundary

### In Scope

- Simplified init (one key, auto-detect provider)
- Onboarding workflow (first-time, interactive)
- Onboarding detection in ChatService
- Connector setup within chat (no separate CLI needed)
- Embedding provider explanation during onboarding
- Built-in onboarding workflow template

### Out of Scope (Future)

- Web UI for onboarding (chat-based is sufficient)
- Multi-user onboarding
- Organization/team setup
- Automated connector discovery
- Onboarding analytics/tracking
