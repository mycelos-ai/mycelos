# Onboarding Redesign: Level-Driven Progressive Onboarding

**Date:** 2026-04-10
**Status:** Draft
**Scope:** Init welcome text, level-aware system prompt, hint refactor, onboarding flow cleanup, agent rename bugfix

## Problem

The current onboarding has several issues:

1. **No identity/positioning** — Mycelos never explains what it is or why it's different
2. **Feature dump** — the onboarding prompt suggests Connectors (Telegram, GitHub) to a brand-new user who hasn't even created a first note
3. **Level not in LLM prompt** — the gamification system exists but doesn't influence how Mycelos talks to the user; wastes tokens on advanced users
4. **Agent rename bug** — onboarding saves the chosen agent name to Memory (`agent.display_name`), but the UI reads from the `agents` DB table. The name never appears in the UI.
5. **Generic hints** — current hints are randomized feature announcements, not benefit-oriented suggestions

## Design Principles

- **No new subsystem** — use the existing level system as the onboarding engine
- **No onboarding workflow** — no resumable state, no checkpoints. The level progression IS the onboarding.
- **Natural fade-out** — hints get less frequent as level rises, no explicit "onboarding complete" moment
- **Honest positioning** — don't claim "fully local" when we use cloud LLMs. Position correctly: your data stays local, cloud LLMs are tools not storage, local models over time.
- **Token-efficient** — higher levels get shorter prompt blocks

## Changes

### 1. Static Init Welcome Text (`init_cmd.py`)

After successful `mycelos init`, display a Rich Panel with Mycelos' positioning:

```
╭─ Mycelos ─────────────────────────────────────────────╮
│                                                       │
│  Mycelos is your personal AI assistant                │
│  that grows with you.                                 │
│                                                       │
│  • Your notes, tasks, and data stay local             │
│  • Cloud LLMs are tools — not your storage            │
│  • Over time you can switch to local models           │
│  • You build your system step by step                 │
│                                                       │
│  Start with: mycelos chat                             │
│                                                       │
╰───────────────────────────────────────────────────────╯
```

- Text via `t()` i18n (keys: `init.welcome_box.*`)
- English and German translations
- Replaces the current plain "Setup complete" message

### 2. Level-Aware System Prompt (`gamification.py` + `mycelos_handler.py`)

New function `get_level_prompt(level: int) -> str` in `gamification.py` returns a prompt block sized to the user's level.

**Level 1-2 (Newcomer/Explorer) — ~200 tokens:**

```
The user is new to Mycelos (Level: {name}).
- Explain what you can do when relevant. Don't list features unprompted, but when the user asks something, mention related capabilities.
- Proactively suggest next steps: "I can also remind you about this — want me to set a reminder?"
- When the user creates their first note, acknowledge it warmly.
- Mention what makes Mycelos different when it fits naturally: data stays local, the system grows with them.
- Suggest one unused feature per conversation, framed as a benefit, not a tutorial.
```

**Level 3-4 (Builder/Architect) — ~80 tokens:**

```
The user is experienced with Mycelos (Level: {name}).
- They know the basics. Skip explanations for notes, reminders, search.
- Mention advanced features only when directly relevant: workflows, agents, automations.
- Keep confirmations brief.
```

**Level 5-6 (Power User/Guru) — ~30 tokens:**

```
The user is a Mycelos power user (Level: {name}).
- Terse responses. No feature suggestions unless asked.
- Focus on efficiency and optimization.
```

**Integration in `mycelos_handler.py`:**

In `get_system_prompt()`, after building the base prompt:

```python
from mycelos.gamification import check_milestones, get_level_prompt

level = check_milestones(self._app, "default")
level_prompt = get_level_prompt(level)
prompt += "\n\n" + level_prompt
```

This **replaces** the current conditional blocks:
- `_ONBOARDING_PROMPT` (removed — level prompt handles new users)
- `_SETUP_HINTS_PROMPT` (removed — hints handle connector suggestions)

The `if not user_name` check stays only for the onboarding conversation injection in `service.py` (Step 4 below), not for the system prompt.

### 3. Benefit-Oriented Hints (`gamification.py`)

Refactor `_get_contextual_hint()` from random selection to relevance-ordered, benefit-oriented suggestions.

**New hint structure:**

```python
@dataclass
class Hint:
    message_key: str      # i18n key
    min_level: int        # Show from this level
    max_level: int        # Stop showing at this level
    condition: Callable   # Returns True if hint is relevant
```

**Hint definitions (ordered by priority within each level range):**

| Level Range | Condition | Hint (i18n key) | Example text |
|-------------|-----------|-----------------|--------------|
| 1-2 | notes == 0 | `hint.first_note` | "What's on your mind? I can capture it for you right now." |
| 1-2 | notes > 0, no reminders | `hint.first_reminder` | "I can also remind you about things. Want to try?" |
| 2-3 | no telegram connector | `hint.telegram_benefit` | "Via Telegram I can send you reminders on your phone — want to set it up?" |
| 2-3 | notes 1-5 | `hint.kb_growing` | "You have {count} notes now. They'll auto-organize into topics as you add more." |
| 3-4 | no github connector | `hint.github_benefit` | "I can read your GitHub repos and help document code. Want to connect?" |
| 3-4 | notes 5-20 | `hint.kb_linking` | "Your knowledge base is growing — I'm organizing it into topics automatically." |
| 4-5 | no custom workflows | `hint.workflow_benefit` | "Repetitive tasks can run automatically. Want me to create a workflow?" |
| 5+ | — | (no hints) | — |

**Selection logic:** Iterate hints in order. Return the first whose `min_level <= level <= max_level` AND `condition()` is True. No randomization — the most relevant hint always wins.

**Frequency:** Unchanged. Newcomer sees hints every session, higher levels less often (existing `show_every = max(1, earned_level)` logic stays).

### 4. Onboarding Conversation Flow (`service.py`)

The onboarding system message injected into the conversation for new users (when `onboarding_completed` is not set and `user.name` is not known) is simplified:

**New flow (replaces current Steps 1-6):**

| Step | What happens | Artifact |
|------|-------------|----------|
| 1 | Detect language from user's first message | — |
| 2 | Introduce: "Hi! I'm Mycelos — your personal AI assistant that grows with you." Ask user's name. | — |
| 3 | "My default name is Mycelos, but you can give me another name if you like." | `user.name` in Memory, `agents.display_name` in DB |
| 4 | "What do you want to use me for?" | `user.goals` in Memory |
| 5 | "What's on your mind right now? I'll capture it for you." → Create first Knowledge note | First note created |
| 6 | Save `onboarding_completed = true` | — |

**What changed vs. current:**
- **Connector suggestions removed** — that's Level 3 territory, handled by hints
- **First note added** — immediate success moment, moves user toward Level 2 (needs 1 note + 10 messages)
- **Shorter** — 6 steps but less text per step, no feature listing

### 5. Agent Rename Bugfix (`service.py`)

**Bug:** The onboarding prompt tells the LLM to save the agent's chosen name via `memory_write` with key `agent.display_name`. But `MycelosHandler.display_name` reads from the `agents` table column `display_name`. The name is stored in the wrong place.

**Fix:** In `service.py`, after the LLM writes to Memory with key `agent.display_name`, also call `app.agent_registry.rename("mycelos", value)` to update the agents table.

Implementation approach: add a post-write hook in the memory_write tool execution path that checks for the `agent.display_name` key and syncs to the agents table. This keeps the LLM prompt simple (it still uses `memory_write`) while fixing the storage.

### 6. i18n Updates (`en.yaml` + `de.yaml`)

**New keys:**

```yaml
# Init welcome box
init:
  welcome_box:
    title: "Mycelos"
    line1: "Mycelos is your personal AI assistant"
    line2: "that grows with you."
    bullet1: "Your notes, tasks, and data stay local"
    bullet2: "Cloud LLMs are tools — not your storage"
    bullet3: "Over time you can switch to local models"
    bullet4: "You build your system step by step"
    start: "Start with: mycelos chat"

# Benefit-oriented hints (replace generic hints)
gamification:
  hint:
    first_note: "What's on your mind? I can capture it for you right now."
    first_reminder: "I can also remind you about things — want to try?"
    telegram_benefit: "Via Telegram I can send you reminders on your phone — want to set it up?"
    kb_growing: "You have {count} notes. They'll auto-organize into topics as you add more."
    github_benefit: "I can read your GitHub repos and help document code. Want to connect?"
    kb_linking: "Your knowledge base is growing — I'm organizing it into topics automatically."
    workflow_benefit: "Repetitive tasks can run automatically. Want me to create a workflow?"
```

German translations follow the same keys in `de.yaml`.

Existing generic hint keys (`hint.setup_telegram`, `hint.send_files`, etc.) are removed and replaced by the benefit-oriented versions above.

## What Does NOT Change

- **Level calculation** — milestones and thresholds stay the same
- **Level-up celebration** — still shown when user advances
- **Session greeting as delivery** — hints are still shown via `get_session_greeting()`
- **No new DB tables** — no onboarding state tracking
- **No new workflow** — level progression IS the onboarding
- **LLM prompt language** — stays English (Constitution Rule 6)
- **Greeting frequency logic** — `show_every = max(1, earned_level)` unchanged

## Testing

- **Unit tests for `get_level_prompt()`** — verify each level returns appropriate prompt length and content
- **Unit tests for refactored hints** — verify condition-based selection returns the most relevant hint
- **Integration test for agent rename** — verify that `memory_write` with `agent.display_name` updates both Memory and agents table
- **Existing gamification tests** — must still pass (milestone checks unchanged)
