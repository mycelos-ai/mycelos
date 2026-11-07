# Strategic Use Case Analysis — Mycelos vs. OpenClaw

**Date:** 2026-03-23
**Status:** STRATEGIC (reference document)

---

## Market Context

OpenClaw is the closest competitor — open source, locally hosted AI agent.
Top complaints: complex setup, bugs, security risks, no rollback.
Top loves: multi-channel, persistent memory, self-hosting, extensibility.

## Our Competitive Advantages

1. **Security-First** — OpenClaw's #1 criticism. Our PolicyEngine, Capability Scoping, Credential Isolation, Config Rollback.
2. **NixOS-Style Rollback** — OpenClaw doesn't have this. Config broken? `/config rollback`.
3. **MCP-Native** — Standard protocol, not proprietary "Skills". Ecosystem grows without us.
4. **Constitution** — 8 non-negotiable principles checked on every change.
5. **TDD Pipeline** — Creator Agent builds agents with Gherkin → Tests → Code → Audit.

## Use Cases We Can Cover

### Tier 1: Ready Now
| Use Case | Status | Tools |
|----------|--------|-------|
| Web Research + Summary | Working | search_web, search_news, http_get |
| GitHub Automation | Working | GitHub MCP (26 tools via HTTP) |
| Daily Briefing | Working | Workflow + Schedule + Telegram |
| File Management | Working | Filesystem MCP (14 tools) |
| Code Generation | Working | Creator Pipeline (TDD) |

### Tier 2: 1-2 Sessions Away
| Use Case | What's Needed |
|----------|---------------|
| E-Mail Management | Google MCP Server (gog CLI exists) |
| Calendar Management | Google Calendar MCP |
| Content Creation | Workflow: search → write → filesystem |
| CRM/Contacts | Google Contacts MCP + Memory |
| Meeting Protocols | Transcript input + summarize workflow |

### Tier 3: Needs Design
| Use Case | What's Needed |
|----------|---------------|
| Social Media Automation | Twitter/X MCP Server |
| Smart Home | Home Assistant MCP |
| Health Tracking | Wearable data input + pattern analysis |
| Multi-User/Family | User isolation (SEC20 scenarios) |

## Priority Roadmap (next sessions)

### P0: Make Workflows Work End-to-End
- Fix Planner handoff (plan → execute)
- "Send me daily AI news" → works without manual steps
- This unlocks UC1, UC2, UC3, UC6, UC15

### P1: Google Integration
- Google MCP (Gmail, Calendar, Drive, Contacts)
- Unlocks UC1, UC2, UC5, UC17

### P2: More Channels
- Slack, Discord, WhatsApp via MCP
- Web UI (Next.js plan exists)

### P3: Proactive Agent
- Heartbeat: agent checks for things to do periodically
- Push notifications via Telegram
- Learning from patterns (Session Summary V2)

## Voice / Speech-to-Text

OpenClaw supports voice input via Whisper API. We should offer this too.

### Providers
| Provider | API | Model | Cost | Notes |
|----------|-----|-------|------|-------|
| OpenAI | Whisper API | whisper-1 | $0.006/min | Most popular, good quality |
| Deepgram | REST API | nova-2 | $0.0043/min | Fast, streaming support |
| AssemblyAI | REST API | universal | $0.01/min | Best accuracy, speaker ID |
| Local (Whisper.cpp) | CLI | whisper-large-v3 | Free | Needs GPU, slower |

### Integration Plan
- Add `stt_provider` to `mycelos init` wizard (optional step)
- Store STT API key in Credential Proxy (same as LLM keys)
- Telegram: voice messages auto-transcribed before ChatService
- Terminal: optional voice input mode (microphone → STT → text)
- Config in channels table: `voice_enabled: true/false` per channel

### Priority
P3 — after workflows and Google integration work.
Voice is a UX enhancement, not a blocker for core functionality.

## Playwright MCP — Browser Automation

Microsoft's official Playwright MCP server (29.5k stars) gives the agent a real browser.
Unlike screenshot-based approaches, it uses accessibility tree snapshots — no vision model needed.

**Install:** `npx @playwright/mcp@latest` (stdio) or HTTP/SSE transport
**Tools:** navigate, click, fill forms, read page content, console messages, code generation

### Use Cases This Unlocks
- Web scraping and data extraction (structured, not just http_get)
- Form filling (expense reports, invoices, booking)
- Web app testing and monitoring
- Price comparison, deal finding
- Account management (login, check status, download statements)

### Integration
- Add as MCP recipe: `command="npx @playwright/mcp@latest"`, transport=stdio
- Tools auto-discovered like GitHub/Filesystem
- Scoped via capabilities: `browser.navigate`, `browser.click`, `browser.fill`
- Security: browser runs in separate process, no access to Mycelos internals

### Priority
P3 — powerful but not core. After Knowledge Base and Google MCP.
OpenClaw does NOT have proper browser automation — this would be a differentiator.

## Anti-Roadmap (learn from OpenClaw failures)

| OpenClaw Problem | Our Solution |
|------------------|--------------|
| Complex setup | In-chat onboarding, smart defaults |
| Bugs/stability | 1338+ tests, TDD pipeline |
| Security risks | Constitution, PolicyEngine, fail-closed |
| No rollback | NixOS-style config generations |
| Expensive tokens | Smart Tool Discovery (meta-tools), cost tracking |
| No emergency stop | Policy "never", confirm flow, audit trail |
| Junk memory | Memory review agent, content sanitization |
