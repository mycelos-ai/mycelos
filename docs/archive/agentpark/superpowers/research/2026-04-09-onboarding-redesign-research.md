# Onboarding Redesign — Research Briefing

**Date:** 2026-04-09
**Purpose:** Preparation for the onboarding brainstorm session. Two expert analyses: business strategy + UX interaction design.

---

## Part 1: Business Strategy Analysis

### Key Insights

1. **The aha moment for AI tools is retrieval, not storage.** The user dumps info in, and later the tool surfaces it at the right time. For Mycelos, the aha should be: "The AI remembered something about me and used it." This must happen in the first session, ideally under 3 minutes.

2. **Minimize configuration before value.** The API key is a hard prerequisite (no LLM without it), so it stays in the init flow. But everything AFTER the key should be conversational, not a wizard. No filesystem permissions, no connector setup, no provider comparison — just the key, then straight into the chat. The gap between "key entered" and "first useful response" must be under 30 seconds.

3. **The empty state is a death spiral.** No context leads to generic AI responses leads to abandonment. Successful tools seed state during onboarding (one note, one preference, one reminder) so the AI has enough surface area to demonstrate value on Day 2.

4. **Levels should change AI behavior, not gate features.** Duolingo adapts difficulty to the user. For Mycelos: Level 1 = proactive, chatty, explains what it did. Level 3 = concise, assumes familiarity. Level 5 = terse, autonomous. Never gate basic functionality — gate complexity and autonomy.

5. **Retention for local-first tools needs external triggers.** Without push notifications, users forget. The Telegram channel becomes the trigger layer — a morning briefing message creates the hook.

### Proposed "First 5 Minutes" (Business View)

| Time | What Happens | What Gets Created |
|------|-------------|-------------------|
| 0:00 | DB created silently, Mycelos greets user | Config |
| 0:15 | "What should I call you?" | Memory: user name |
| 0:30 | "What will you mainly use me for?" | Memory: usage context |
| 1:00 | "Tell me one thing you want to remember." | First knowledge note |
| 1:30 | "Shall I remind you to check back tomorrow?" | First reminder (24h) |
| *(pre)* | *API key already provided during `mycelos init` — hard prerequisite* | *Credential* |
| 3:00 | "Here's what I know about you so far:" — summary | — |

### Anti-Patterns

1. Unnecessary configuration-before-value (API key is required, but nothing else should gate the first conversation)
2. Feature tours (tooltip walkthroughs have near-zero completion)
3. Empty dashboards ("No notes yet" = "this tool is useless")
4. Punishing levels (less hand-holding without user understanding why)

---

## Part 2: UX Interaction Design

### Step-by-Step Chat Script

**Phase A — First Contact (0-30s):**

Mycelos auto-sends (no user action needed):
> Hey — I'm Mycelos, your personal AI assistant.
>
> I learn what matters to you and grow with your needs. Right now I'm starting simple, but over time I'll get much more useful.
>
> What's your name?

User types name. Mycelos saves to memory.

> Hi Stefan! Nice to meet you.
>
> Let me show you the one thing I think you'll use most: I can capture and organize anything you want to remember.
>
> What's on your mind right now? An idea, a task, something you want to look into later — anything.

**Phase B — First Note (30s-2min):**

User types something like "I need to research vector databases for my project."

Mycelos saves it as a note, shows a status_card widget, offers to research it immediately. If the user says yes, Mycelos searches the web and adds results to the note.

> I've added this to your "Research vector databases" note so you don't lose it. You can always find it again by asking me.

Sidebar Knowledge nav gets a subtle notification dot.

**Phase C — Topic Introduction (2-3min):**

> By the way — you can organize notes into topics. Want me to create a "Project" topic for this research?

User confirms. Mycelos creates topic and moves the note.

**Phase D — Quick Capture Pattern (3-4min):**

> Whenever something pops into your head, just tell me. "Remember that ...", "Add a task to ...", "Look up ..." — I'll capture it instantly.
>
> What else is on your list today?

User: "Remind me to call the dentist tomorrow" → Mycelos saves task with reminder.

**Phase E — Natural Wrap-up (4-5min):**

No "congratulations" screen. The onboarding IS the conversation. The level system activates silently. Next session greeting comes from the gamification system.

### Level Adaptation Rules

**Level 1 (Newcomer):**
```
## User Level: Newcomer (Level 1)
The user is new. Keep it simple:
- Explain what you did after each action
- Suggest ONE next step at a time, never a list
- Use status_card widgets for results
- Never mention: workflows, connectors, agents, MCP, config, slash commands
- Focus on: notes, tasks, reminders, web search
```

**Level 3 (Builder):**
```
## User Level: Builder (Level 3)
The user knows the basics:
- Skip explanations for notes, topics, tasks
- Brief confirmations ("Saved." / "Done — added to Project.")
- Mention connectors when relevant
- Suggest workflows for repetitive tasks
```

**Level 5+ (Power User):**
```
## User Level: Power User (Level 5)
The user is experienced:
- Minimal confirmations
- Proactively suggest optimizations (merge notes, automate patterns)
- Reference any feature freely
- Just do complex things — don't ask for confirmation on safe operations
```

### Progressive Disclosure Sequence

| When | What's Introduced |
|------|-------------------|
| Minute 0-1 | Chat, user name, one open question |
| Minute 1-3 | note_write, search_web, topics, Knowledge page (via dot) |
| Minute 3-5 | Reminders, task due dates, "just tell me" capture pattern |
| Day 2+ | Connectors (when user mentions email/Telegram), workflows (when repetitive), agents (when complex), Cmd+K (via gamification hint) |

### Empty States

| Page | Message | Action |
|------|---------|--------|
| Chat (first visit) | No static state — Mycelos auto-sends greeting | — |
| Chat (returning) | Placeholder: "Ask anything, capture a thought, or start a task..." | — |
| Knowledge (empty) | "Your Knowledge Base is empty — everything you tell Mycelos to remember appears here." | Link: "Go to Chat" |
| Workflows (empty) | "No workflows yet. Workflows automate things you do regularly." | Link: "Tell Mycelos about a task to automate" |

### Anti-Patterns

1. **No feature menus during onboarding** — the current prompt lists 7 capabilities. Replace with one open question.
2. **No tutorial overlays** — no coach marks, no modals, no "Step 1 of 4". Everything in the chat.
3. **No pre-configuration** — don't ask about Telegram, email, GitHub until the user mentions a need. Just-in-time config only.

---

---

## Part 3: Progressive Onboarding Chain Research

### The "Aha Chain" Pattern

Each step produces a visible artifact that makes the next step obvious. Slack: send message → channel feels needed → create channel → feels empty → invite teammate → want integrations. Superhuman: teaches workflows not features — user finishes onboarding having actually done the thing.

**For Mycelos:** The chain works when each step creates a need the next step solves. User says "remind me to check deployment" → Mycelos creates it → "Want me to send it via Telegram so you get it on your phone?" The reminder *existing* is what motivates Telegram setup. Don't pitch Telegram abstractly.

### Resumable Onboarding

- **Notion**: onboarding is a literal page inside the product (deletable content, not UI chrome)
- **Linear**: front-loads into one session, no "welcome back" nags
- **Figma**: contextual tooltips when you first encounter a feature organically
- **Anti-pattern**: Intercom checklists ("2 of 5 steps!") have 70%+ dismissal rates

**For Mycelos:** Store onboarding state in memory (existing 4-scope system). No checklist UI. Contextual suggestions: when user creates 3rd reminder but has no Telegram → suggest it. Track declined suggestions — never re-suggest the same connector within a week.

### Progressive Connector Introduction

- **Zapier**: shows "Popular Zaps" based on first app connected, not all 5000
- **Make.com**: templates as discovery ("When email with attachment → save to Drive" implicitly introduces two connectors)
- **n8n**: community-shared workflows as social proof

**For Mycelos:** Never present connectors as a list. Present as capabilities: "You asked me to summarize that webpage — if you connect a browser, I can do this automatically." The connector is the solution to a need the user already expressed.

### Daily Briefing as Retention

- **Headspace**: frames as "when do you want to meditate?" not "want reminders?" (assumes commitment, asks timing only → 3x retention)
- **Todoist**: morning delivery has 2x open rate vs evening
- **Strava**: weekly summary only valuable if you logged activities → virtuous cycle

**For Mycelos:** Don't offer briefing as a feature. Offer when 3+ active tasks exist: "You have a few things this week. Want a morning summary at 8am?" First briefing must contain real data, never be empty.

### LLM-Driven Onboarding

No major product uses unconstrained LLM-driven onboarding. What works:
- **Structured state machine with LLM phrasing** — steps/transitions are defined, but wording is LLM-generated based on context
- **LLM as trigger detector** — classifies when to suggest (user mentioned email + no email connector → suggest), but suggestion content is templated
- **LLM hands off to wizard** — "Want me to connect Telegram?" → user says yes → deterministic `/connector setup` wizard takes over → LLM re-engages after ("Got it, I'll send tomorrow's reminder there.")

### Stefan's Concrete Chain (from session discussion)

```
1. API Key (init, prerequisite)
2. First Idea → "Willst du mir deine erste Idee festhalten?"
   → immediate artifact (note saved)
3. Task/Reminder → "Ich kann dich auch erinnern"
   → immediate artifact (reminder set)
4. Telegram → "Das funktioniert am besten über Telegram"
   → triggered by reminder existing, immediate proof (message arrives on phone)
5. Email → "Ich kann auch deine E-Mails durchsuchen"
   → triggered by user need, not checklist
6. Browser → "Mit Playwright kann ich Webseiten lesen"
   → triggered when user shares URLs manually
7. Morning Briefing → "Was hältst du von einem Morgen-Briefing?"
   → triggered when 3+ tasks exist, real data in first briefing
```

Each step triggered by user behavior, not a schedule. LLM detects the right moment; setup wizard handles mechanics. Declined suggestions tracked in memory, not re-prompted within a week.

---

## Implementation Touchpoints

1. `src/mycelos/chat/service.py` — Replace `_ONBOARDING_PROMPT` with conversational approach + level-based prompt injection
2. `src/mycelos/agents/handlers/mycelos_handler.py` — Extend `get_system_prompt` to inject level-specific prompts
3. `src/mycelos/frontend/pages/chat.html` — Auto-greeting when no messages + no user name in memory
4. `src/mycelos/i18n/locales/en.yaml` + `de.yaml` — New onboarding strings
5. `src/mycelos/gamification.py` — Level-to-prompt mapping
