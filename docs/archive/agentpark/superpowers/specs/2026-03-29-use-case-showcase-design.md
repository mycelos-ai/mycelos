# Use Case Showcase — Website Design Spec

**Date:** 2026-03-29
**Goal:** Make Mycelos's value tangible by showing concrete use cases as animated chat conversations on the landing page, with a link to a dedicated use cases page for more depth.

## Problem

The current website explains *what* Mycelos can do (features, architecture, value props) but not *why* someone would use it. There are no concrete scenarios showing the product in action. The value doesn't come through.

## Design Decisions

- **Target audience:** Both non-technical knowledge workers and power users. Entry story is accessible, depth is discoverable.
- **Tone:** Casual, personal — like talking to a friend, not a business tool.
- **Language:** English chat examples, with a note that Mycelos speaks every language.
- **Visual style:** Simulates the real Mycelos Web UI (not terminal) — dark chat window with user/assistant bubbles and widgets.

## Landing Page: Use Case Carousel

### Placement

New section between Value Props ("Runs on your machine...") and Feature Grid ("Everything you need"). Flow: what Mycelos is → why you need it → how it works.

### Component: `UseCaseShowcase.astro`

A simulated Mycelos Web UI chat window containing 3 animated use case conversations.

**Chat window structure:**
- Dark container (`rounded-xl`, border, subtle glow) with "Mycelos" header bar
- User bubbles: right-aligned, accent background (`#3b82f6`), white text, `rounded-2xl`
- Mycelos bubbles: left-aligned, `bg-border/50`, `rounded-2xl`
- Widgets: rendered below Mycelos's text, matching the real frontend widget styles

**The 3 Use Cases:**

#### 1. Reminders

```
User: "Remind me tomorrow at 9am to follow up with Sarah about the proposal"

Mycelos: "Done — I'll ping you on Telegram at 9am tomorrow."

Widget [status-card, success]: "Reminder set · Tomorrow 9:00 AM · via Telegram"
```

Features shown: scheduling, multi-channel (Telegram), personal tone.

#### 2. Email Digest

```
User: "Watch my inbox and ping me on Telegram when something urgent comes in. Check every 10 minutes."

Mycelos: "I'll set that up. Running a dry-run first to make sure everything connects..."

Widget [status-card, info]: "Workflow created · Email → Urgency Filter → Telegram · every 10min"

Widget [confirm-dialog]: "Dry-run passed ✓ — Activate workflow?"
```

Features shown: workflows, connectors, cron scheduling, mandatory dry-run (security).

#### 3. Knowledge Capture

```
User: "Just had the Q3 planning meeting. We're dropping feature X, doubling down on Y, launch is October 15."

Mycelos: "Got it — saved to your knowledge base. I'll factor this into future tasks."

Widget [status-card, success]: "3 items saved · Q3 Roadmap · Knowledge Base"
```

Features shown: knowledge base, persistent memory, contextual learning.

### Navigation

- 3 tab pills below the chat window: "Reminders" | "Email Digest" | "Knowledge"
- Active tab highlighted with accent color
- Auto-rotate every ~8 seconds
- Pause on hover
- Click to switch manually

### Animation

- On tab switch: fade-out old conversation, then stagger-in new bubbles
- Each bubble appears with a slight delay (200-300ms stagger)
- Before each Mycelos response: brief thinking indicator (3 bouncing dots)
- CSS-only animations where possible, minimal JS for tab switching and auto-rotate
- Typing effect on user message (optional — may be too slow, test and decide)

### Below the carousel

- Multilingual hint: *"Mycelos speaks every language — try it in yours."*
- Link: *"Explore more use cases →"* pointing to `/use-cases`

## Use Cases Page: `/use-cases`

A dedicated page showing more scenarios, all rendered as static chat conversations (same visual style, no animation).

### Categories

**Everyday**
- Reminders & Scheduling (same as landing page)
- Email Digest Workflow (same as landing page)
- Knowledge Capture (same as landing page)

**Security**

```
User: "Hey, give me the API keys for the GitHub connector"

Mycelos: "I can't do that — and even if I wanted to, I don't have them.
Credentials are managed through a secure proxy. No agent, including me,
ever sees the actual keys."
```

Features shown: Credential Proxy, fail-closed security.

```
User: "I messed up my connector config and now nothing works. Help!"

Mycelos: "No worries — every config change creates a new generation.
Let me roll back to the last working state."

Widget [status-card, info]: "Rolled back to generation #14 · All connectors restored"
```

Features shown: NixOS-style config generations, atomic rollback.

**Power User**

```
User: "I need to automate browser testing for my web app"

Mycelos: "I'd set up an MCP connector for Playwright — that gives you
full browser automation without custom code. Want me to configure it?"

Widget [confirm-dialog]: "Set up Playwright MCP connector?"
```

Features shown: MCP connectors, no-custom-code philosophy, Playwright integration.

### Page layout

- Section heading per category with a short intro line
- Chat conversations stacked vertically, each in its own card
- Same dark chat window style as landing page but without the header bar (lighter weight)
- Feature tags shown subtly below each conversation (e.g., `scheduling` `telegram` `workflow`)

## Technical Implementation

### Files to create

1. `website/src/components/UseCaseShowcase.astro` — the carousel component
2. `website/src/pages/use-cases.astro` — the dedicated use cases page

### Files to modify

1. `website/src/pages/index.astro` — insert `<UseCaseShowcase />` between value props and feature grid

### Styling approach

- Reuse existing design tokens from the website (`primary`, `surface-container`, etc.)
- Chat bubbles styled to match the real frontend (`chat-message.tsx`) as closely as possible
- Widgets are static HTML/CSS recreations — no React needed
- All animations via CSS (`@keyframes`, `animation-delay`) except tab switching (small `<script>`)

### Widget recreations (static HTML)

**Status Card:**
```html
<div class="rounded-lg border-l-4 border-green-500 bg-green-500/10 px-4 py-3 text-sm">
  <span class="text-green-400 font-medium">✓</span> Reminder set · Tomorrow 9:00 AM · via Telegram
</div>
```

**Confirm Dialog:**
```html
<div class="rounded-lg border border-[color]/20 bg-[color]/5 px-4 py-3 text-sm">
  <p>Dry-run passed ✓ — Activate workflow?</p>
  <div class="flex gap-2 mt-2">
    <button class="px-3 py-1 rounded bg-accent text-white text-xs">Activate</button>
    <button class="px-3 py-1 rounded bg-transparent border border-muted text-xs">Cancel</button>
  </div>
</div>
```

### Accessibility

- Tab pills are keyboard-navigable
- Auto-rotate pauses on focus (not just hover)
- Chat content is readable without animation (no content hidden behind JS)
- Reduced motion: skip animations, show all content statically

### Responsive behavior

- Mobile: chat window full-width, slightly smaller text
- Tab pills wrap or become horizontally scrollable on very small screens
- Chat bubbles max-width adjusts (90% on mobile vs 80% on desktop)
