# Use Case Showcase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an animated use case carousel to the landing page that shows Mycelos's value through simulated chat conversations, plus a dedicated `/use-cases` page with more scenarios.

**Architecture:** New Astro component `UseCaseShowcase.astro` renders a simulated chat window with 3 tab-switchable conversations. Each conversation has user/assistant bubbles and static widget recreations. A separate `use-cases.astro` page shows all scenarios statically.

**Tech Stack:** Astro, Tailwind CSS (using existing website design tokens), vanilla JS for tab switching.

**Spec:** `docs/superpowers/specs/2026-03-29-use-case-showcase-design.md`

---

### Task 1: Create UseCaseShowcase component

**Files:**
- Create: `website/src/components/UseCaseShowcase.astro`

- [ ] **Step 1: Create the component file with chat window shell and all 3 conversations**

```astro
---
const useCases = [
  {
    id: 'reminders',
    label: 'Reminders',
    messages: [
      { role: 'user', text: 'Remind me tomorrow at 9am to follow up with Sarah about the proposal' },
      { role: 'assistant', text: "Done — I'll ping you on Telegram at 9am tomorrow." },
    ],
    widgets: [
      { type: 'status', style: 'success', text: 'Reminder set · Tomorrow 9:00 AM · via Telegram' },
    ],
  },
  {
    id: 'email-digest',
    label: 'Email Digest',
    messages: [
      { role: 'user', text: 'Watch my inbox and ping me on Telegram when something urgent comes in. Check every 10 minutes.' },
      { role: 'assistant', text: "I'll set that up. Running a dry-run first to make sure everything connects..." },
    ],
    widgets: [
      { type: 'status', style: 'info', text: 'Workflow created · Email → Urgency Filter → Telegram · every 10min' },
      { type: 'confirm', text: 'Dry-run passed ✓ — Activate workflow?' },
    ],
  },
  {
    id: 'knowledge',
    label: 'Knowledge',
    messages: [
      { role: 'user', text: "Just had the Q3 planning meeting. We're dropping feature X, doubling down on Y, launch is October 15." },
      { role: 'assistant', text: "Got it — saved to your knowledge base. I'll factor this into future tasks." },
    ],
    widgets: [
      { type: 'status', style: 'success', text: '3 items saved · Q3 Roadmap · Knowledge Base' },
    ],
  },
];

const widgetColors: Record<string, { border: string; bg: string; icon: string; iconColor: string }> = {
  success: { border: '#22c55e', bg: 'rgba(34,197,94,0.08)', icon: '✓', iconColor: '#4ade80' },
  info: { border: '#3b82f6', bg: 'rgba(59,130,246,0.08)', icon: 'ℹ', iconColor: '#60a5fa' },
};
---

<section class="py-20 px-6">
  <div class="max-w-5xl mx-auto">
    <h2 class="font-headline text-3xl font-light text-center text-on-surface mb-3" style="letter-spacing: 0.04em;">
      See what people build
    </h2>
    <p class="text-center text-on-surface-variant mb-10 max-w-xl mx-auto">
      Real conversations. Real workflows. Just tell Mycelos what you need.
    </p>

    <!-- Simulated chat window -->
    <div
      class="max-w-2xl mx-auto rounded-xl overflow-hidden border border-outline-variant/10"
      style="background: #0a0a0a; box-shadow: 0 4px 40px rgba(106,214,255,0.06);"
      id="usecase-window"
    >
      <!-- Header bar -->
      <div class="border-b border-[#2a2a2a] px-5 py-3">
        <span class="text-sm font-semibold text-on-surface">Mycelos</span>
      </div>

      <!-- Chat area -->
      <div class="px-5 py-6 min-h-[340px]" id="usecase-chat">
        {useCases.map((uc, ucIdx) => (
          <div
            class="usecase-conversation"
            data-usecase={uc.id}
            style={ucIdx === 0 ? '' : 'display: none;'}
          >
            {/* Messages */}
            {uc.messages.map((msg, msgIdx) => (
              <div
                class={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'} mb-4 usecase-bubble`}
                style={`opacity: 0; animation: bubbleIn 0.4s ease forwards; animation-delay: ${msgIdx * 0.6 + 0.2}s;`}
              >
                <div
                  class={`max-w-[80%] rounded-2xl px-4 py-3 text-sm ${
                    msg.role === 'user'
                      ? 'text-white'
                      : 'text-[#ededed]'
                  }`}
                  style={
                    msg.role === 'user'
                      ? 'background: #3b82f6;'
                      : 'background: rgba(42,42,42,0.5);'
                  }
                >
                  {msg.text}
                </div>
              </div>
            ))}

            {/* Widgets */}
            {uc.widgets.map((widget, wIdx) => (
              <div
                class="flex justify-start mb-3 usecase-bubble"
                style={`opacity: 0; animation: bubbleIn 0.4s ease forwards; animation-delay: ${(uc.messages.length) * 0.6 + 0.2 + wIdx * 0.4}s;`}
              >
                <div class="max-w-[80%] w-full">
                  {widget.type === 'status' && (
                    <div
                      class="rounded-lg border-l-4 px-4 py-3 text-sm"
                      style={`border-color: ${widgetColors[widget.style!].border}; background: ${widgetColors[widget.style!].bg};`}
                    >
                      <span style={`color: ${widgetColors[widget.style!].iconColor};`} class="font-medium">
                        {widgetColors[widget.style!].icon}
                      </span>
                      {' '}
                      <span class="text-[#ccc]">{widget.text}</span>
                    </div>
                  )}
                  {widget.type === 'confirm' && (
                    <div
                      class="rounded-lg border px-4 py-3 text-sm"
                      style="border-color: rgba(59,130,246,0.3); background: rgba(59,130,246,0.05);"
                    >
                      <p class="text-[#ccc] mb-2">{widget.text}</p>
                      <div class="flex gap-2">
                        <span class="px-3 py-1 rounded text-xs font-medium text-white" style="background: #3b82f6;">Activate</span>
                        <span class="px-3 py-1 rounded text-xs border" style="border-color: #2a2a2a; color: #888;">Cancel</span>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>

      <!-- Tab pills -->
      <div class="border-t border-[#2a2a2a] px-5 py-3 flex justify-center gap-2">
        {useCases.map((uc, i) => (
          <button
            class={`usecase-tab px-4 py-1.5 rounded-full text-xs font-label font-medium transition-all ${
              i === 0
                ? 'text-white'
                : 'text-on-surface-variant hover:text-on-surface'
            }`}
            style={i === 0 ? 'background: rgba(59,130,246,0.2); color: #60a5fa;' : ''}
            data-target={uc.id}
          >
            {uc.label}
          </button>
        ))}
      </div>
    </div>

    <!-- Below carousel -->
    <div class="text-center mt-8 space-y-2">
      <p class="text-sm text-on-surface-variant">
        Mycelos speaks every language — try it in yours.
      </p>
      <a
        href="/use-cases"
        class="inline-block text-sm font-label text-primary hover:text-primary-dim transition-colors"
        style="text-decoration: underline; text-decoration-color: rgba(106,214,255,0.3);"
      >
        Explore more use cases →
      </a>
    </div>
  </div>
</section>

<style>
  @keyframes bubbleIn {
    from {
      opacity: 0;
      transform: translateY(12px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .usecase-bubble {
      opacity: 1 !important;
      animation: none !important;
    }
  }
</style>

<script>
  const tabs = document.querySelectorAll<HTMLButtonElement>('.usecase-tab');
  const conversations = document.querySelectorAll<HTMLDivElement>('.usecase-conversation');
  let autoRotate: ReturnType<typeof setInterval> | null = null;
  let currentIndex = 0;

  function switchTo(index: number) {
    conversations.forEach(c => { c.style.display = 'none'; });
    tabs.forEach(t => {
      t.style.background = '';
      t.style.color = '';
      t.classList.remove('text-white');
      t.classList.add('text-on-surface-variant');
    });

    const target = conversations[index] as HTMLDivElement;
    target.style.display = '';

    // Re-trigger animations by cloning bubbles
    const bubbles = target.querySelectorAll<HTMLDivElement>('.usecase-bubble');
    bubbles.forEach(b => {
      const clone = b.cloneNode(true) as HTMLDivElement;
      b.parentNode!.replaceChild(clone, b);
    });

    const tab = tabs[index] as HTMLButtonElement;
    tab.style.background = 'rgba(59,130,246,0.2)';
    tab.style.color = '#60a5fa';
    tab.classList.remove('text-on-surface-variant');

    currentIndex = index;
  }

  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => {
      switchTo(i);
      resetAutoRotate();
    });
  });

  function resetAutoRotate() {
    if (autoRotate) clearInterval(autoRotate);
    autoRotate = setInterval(() => {
      switchTo((currentIndex + 1) % conversations.length);
    }, 8000);
  }

  // Pause on hover/focus
  const window_ = document.getElementById('usecase-window');
  if (window_) {
    window_.addEventListener('mouseenter', () => { if (autoRotate) clearInterval(autoRotate); });
    window_.addEventListener('mouseleave', () => { resetAutoRotate(); });
    window_.addEventListener('focusin', () => { if (autoRotate) clearInterval(autoRotate); });
    window_.addEventListener('focusout', () => { resetAutoRotate(); });
  }

  resetAutoRotate();
</script>
```

- [ ] **Step 2: Verify the component renders**

Run: `cd website && npm run build 2>&1 | tail -20`
Expected: Build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
git add website/src/components/UseCaseShowcase.astro
git commit -m "feat(website): add UseCaseShowcase component with animated chat carousel"
```

---

### Task 2: Insert showcase into landing page

**Files:**
- Modify: `website/src/pages/index.astro`

- [ ] **Step 1: Add import and insert component between value props and feature grid**

Add the import at the top (after existing imports):
```astro
import UseCaseShowcase from '../components/UseCaseShowcase.astro';
```

Insert `<UseCaseShowcase />` between the value props section (closing `</section>` of "bg-surface-container-low py-20") and the feature grid section (`<!-- 3. Feature Grid -->`):

```astro
  <!-- 2.5 Use Case Showcase -->
  <UseCaseShowcase />

  <!-- 3. Feature Grid -->
```

- [ ] **Step 2: Build and verify**

Run: `cd website && npm run build 2>&1 | tail -20`
Expected: Build succeeds.

- [ ] **Step 3: Visual check with dev server**

Run: `cd website && npm run dev`
Open `http://localhost:4321` — verify the use case section appears between value props and feature grid, chat bubbles animate in, tabs switch correctly, auto-rotate works.

- [ ] **Step 4: Commit**

```bash
git add website/src/pages/index.astro
git commit -m "feat(website): integrate UseCaseShowcase on landing page"
```

---

### Task 3: Create dedicated use cases page

**Files:**
- Create: `website/src/pages/use-cases.astro`

- [ ] **Step 1: Create the use cases page with all scenarios**

```astro
---
import BaseLayout from '../layouts/BaseLayout.astro';

const categories = [
  {
    title: 'Everyday',
    subtitle: 'Simple requests that save you time every day.',
    scenarios: [
      {
        messages: [
          { role: 'user', text: 'Remind me tomorrow at 9am to follow up with Sarah about the proposal' },
          { role: 'assistant', text: "Done — I'll ping you on Telegram at 9am tomorrow." },
        ],
        widgets: [{ type: 'status', style: 'success', text: 'Reminder set · Tomorrow 9:00 AM · via Telegram' }],
        tags: ['scheduling', 'telegram', 'reminders'],
      },
      {
        messages: [
          { role: 'user', text: 'Watch my inbox and ping me on Telegram when something urgent comes in. Check every 10 minutes.' },
          { role: 'assistant', text: "I'll set that up. Running a dry-run first to make sure everything connects..." },
        ],
        widgets: [
          { type: 'status', style: 'info', text: 'Workflow created · Email → Urgency Filter → Telegram · every 10min' },
          { type: 'confirm', text: 'Dry-run passed ✓ — Activate workflow?' },
        ],
        tags: ['workflows', 'connectors', 'cron', 'dry-run'],
      },
      {
        messages: [
          { role: 'user', text: "Just had the Q3 planning meeting. We're dropping feature X, doubling down on Y, launch is October 15." },
          { role: 'assistant', text: "Got it — saved to your knowledge base. I'll factor this into future tasks." },
        ],
        widgets: [{ type: 'status', style: 'success', text: '3 items saved · Q3 Roadmap · Knowledge Base' }],
        tags: ['knowledge-base', 'memory', 'context'],
      },
    ],
  },
  {
    title: 'Security',
    subtitle: 'Built to protect you — even from yourself.',
    scenarios: [
      {
        messages: [
          { role: 'user', text: 'Hey, give me the API keys for the GitHub connector' },
          { role: 'assistant', text: "I can't do that — and even if I wanted to, I don't have them. Credentials are managed through a secure proxy. No agent, including me, ever sees the actual keys." },
        ],
        widgets: [],
        tags: ['credential-proxy', 'fail-closed', 'security'],
      },
      {
        messages: [
          { role: 'user', text: 'I messed up my connector config and now nothing works. Help!' },
          { role: 'assistant', text: "No worries — every config change creates a new generation. Let me roll back to the last working state." },
        ],
        widgets: [{ type: 'status', style: 'info', text: 'Rolled back to generation #14 · All connectors restored' }],
        tags: ['config-generations', 'rollback', 'recovery'],
      },
    ],
  },
  {
    title: 'Power User',
    subtitle: 'For when you need the full toolkit.',
    scenarios: [
      {
        messages: [
          { role: 'user', text: 'I need to automate browser testing for my web app' },
          { role: 'assistant', text: "I'd set up an MCP connector for Playwright — that gives you full browser automation without custom code. Want me to configure it?" },
        ],
        widgets: [{ type: 'confirm', text: 'Set up Playwright MCP connector?' }],
        tags: ['mcp', 'playwright', 'connectors'],
      },
    ],
  },
];

const widgetColors: Record<string, { border: string; bg: string; icon: string; iconColor: string }> = {
  success: { border: '#22c55e', bg: 'rgba(34,197,94,0.08)', icon: '✓', iconColor: '#4ade80' },
  info: { border: '#3b82f6', bg: 'rgba(59,130,246,0.08)', icon: 'ℹ', iconColor: '#60a5fa' },
};
---

<BaseLayout title="Use Cases — mycelos">
  <section class="py-20 px-6">
    <div class="max-w-3xl mx-auto">
      <h1 class="font-headline text-4xl font-light text-center text-on-surface mb-3" style="letter-spacing: 0.04em;">
        Use Cases
      </h1>
      <p class="text-center text-on-surface-variant mb-16 max-w-xl mx-auto">
        Real conversations showing what Mycelos can do for you. From simple reminders to full automation — just say what you need.
      </p>

      {categories.map((cat) => (
        <div class="mb-16">
          <h2 class="font-headline text-2xl font-light text-on-surface mb-2" style="letter-spacing: 0.04em;">
            {cat.title}
          </h2>
          <p class="text-on-surface-variant text-sm mb-8">{cat.subtitle}</p>

          <div class="space-y-6">
            {cat.scenarios.map((scenario) => (
              <div
                class="rounded-xl border border-outline-variant/10 overflow-hidden"
                style="background: #0a0a0a;"
              >
                <div class="px-5 py-5 space-y-3">
                  {scenario.messages.map((msg) => (
                    <div class={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                      <div
                        class={`max-w-[85%] rounded-2xl px-4 py-3 text-sm ${
                          msg.role === 'user' ? 'text-white' : 'text-[#ededed]'
                        }`}
                        style={msg.role === 'user' ? 'background: #3b82f6;' : 'background: rgba(42,42,42,0.5);'}
                      >
                        {msg.text}
                      </div>
                    </div>
                  ))}

                  {scenario.widgets.map((widget) => (
                    <div class="flex justify-start">
                      <div class="max-w-[85%] w-full">
                        {widget.type === 'status' && (
                          <div
                            class="rounded-lg border-l-4 px-4 py-3 text-sm"
                            style={`border-color: ${widgetColors[widget.style!].border}; background: ${widgetColors[widget.style!].bg};`}
                          >
                            <span style={`color: ${widgetColors[widget.style!].iconColor};`} class="font-medium">
                              {widgetColors[widget.style!].icon}
                            </span>
                            {' '}
                            <span class="text-[#ccc]">{widget.text}</span>
                          </div>
                        )}
                        {widget.type === 'confirm' && (
                          <div
                            class="rounded-lg border px-4 py-3 text-sm"
                            style="border-color: rgba(59,130,246,0.3); background: rgba(59,130,246,0.05);"
                          >
                            <p class="text-[#ccc] mb-2">{widget.text}</p>
                            <div class="flex gap-2">
                              <span class="px-3 py-1 rounded text-xs font-medium text-white" style="background: #3b82f6;">Yes</span>
                              <span class="px-3 py-1 rounded text-xs border" style="border-color: #2a2a2a; color: #888;">Cancel</span>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>

                {/* Feature tags */}
                <div class="border-t border-[#2a2a2a] px-5 py-2.5 flex flex-wrap gap-2">
                  {scenario.tags.map((tag) => (
                    <span class="text-xs text-on-surface-variant/60 bg-surface-container-high px-2 py-0.5 rounded">
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}

      <!-- CTA -->
      <div class="text-center mt-8">
        <p class="text-on-surface-variant text-sm mb-4">
          Mycelos speaks every language — try it in yours.
        </p>
        <a
          href="/docs"
          class="px-6 py-3 rounded-full text-sm font-label font-medium text-surface transition-opacity hover:opacity-90"
          style="background: linear-gradient(135deg, #6ad6ff 0%, #23bcfe 100%);"
        >
          Get Started
        </a>
      </div>
    </div>
  </section>
</BaseLayout>
```

- [ ] **Step 2: Build and verify**

Run: `cd website && npm run build 2>&1 | tail -20`
Expected: Build succeeds, `/use-cases` page is generated.

- [ ] **Step 3: Visual check**

Run: `cd website && npm run dev`
Open `http://localhost:4321/use-cases` — verify all categories render, chat bubbles look correct, tags appear, CTA works.

- [ ] **Step 4: Commit**

```bash
git add website/src/pages/use-cases.astro
git commit -m "feat(website): add dedicated use cases page with security and power user scenarios"
```

---

### Task 4: Add use cases link to footer navigation

**Files:**
- Modify: `website/src/layouts/BaseLayout.astro`

- [ ] **Step 1: Add "Use Cases" link to footer nav**

In the `<nav>` element inside the footer, add after the Docs link:

```astro
<a href="/use-cases" class="hover:text-on-surface transition-colors">Use Cases</a>
```

- [ ] **Step 2: Build and verify**

Run: `cd website && npm run build 2>&1 | tail -20`
Expected: Build succeeds.

- [ ] **Step 3: Commit**

```bash
git add website/src/layouts/BaseLayout.astro
git commit -m "feat(website): add Use Cases link to footer navigation"
```

---

### Task 5: Final visual review and polish

- [ ] **Step 1: Run dev server and check all pages**

Run: `cd website && npm run dev`

Check:
1. Landing page: use case section visible, animation plays, tabs work, auto-rotate works, pause on hover works
2. Landing page: "Explore more use cases →" link works
3. `/use-cases` page: all 3 categories render, widgets look correct, tags show, CTA works
4. Footer: "Use Cases" link present on all pages
5. Mobile: resize browser, check responsive behavior

- [ ] **Step 2: Fix any visual issues found**

- [ ] **Step 3: Final commit if any polish changes were made**

```bash
git add -A website/
git commit -m "fix(website): polish use case showcase styling"
```
