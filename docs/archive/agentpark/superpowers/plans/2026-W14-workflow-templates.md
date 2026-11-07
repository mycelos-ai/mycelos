# Plan: Workflow Templates with Prerequisites + Community Marketplace

> **Week:** 14 (2026)
> **Status:** Planned
> **Priority:** High — key differentiator for user experience

## Vision

Workflows as a library of ready-to-use automations. Users don't build from scratch — they browse, discover, and activate. The Builder agent knows all templates and suggests them before creating new ones.

Community can contribute templates via Pull Request or a marketplace.

## Workflow YAML: `requires` Field

```yaml
name: email-digest
description: "Daily summary of unread emails"
requires:
  connectors: [email]                   # needs email connector
  tools: [email_inbox, email_search]    # needs these tools available
  capabilities: []                      # no special agent capabilities
  models: []                            # any model works (haiku is fine)

plan: |
  You are the Email Digest Agent...
model: haiku
allowed_tools: [email_inbox, email_search, email_read, email_count]
```

### Possible `requires` fields:

| Field | Example | Meaning |
|-------|---------|---------|
| `connectors` | `[email]`, `[github, slack]` | These connectors must be configured |
| `tools` | `[email_inbox]` | These tools must be registered |
| `capabilities` | `[vision]` | LLM needs specific capabilities |
| `models` | `[tier:opus]` | Needs at least this model tier |
| `mcp_servers` | `[playwright]` | Needs this MCP server running |

## How the Builder Uses Templates

No extra engine — the Builder's system prompt gets the template list as context:

```
## Available Workflow Templates

- email-digest: Daily email summary (requires: email connector)
- daily-news: Morning news briefing (requires: nothing)
- pr-summary: GitHub PR daily summary (requires: github connector)
- document-analyzer: Analyze uploaded documents (requires: vision model)
- meeting-prep: Calendar check + relevant notes (requires: google/calendar)

When the user asks for something that matches a template:
1. Check if prerequisites are met (connectors configured, tools available)
2. If yes → suggest activating the template
3. If no → tell user what's missing and offer to help set it up
4. Only create a NEW workflow if no template matches
```

## Template Categories

### Communication
- `email-digest` — Daily unread email summary (requires: email)
- `email-urgent-notify` — Forward urgent emails to Telegram (requires: email, telegram)
- `email-to-knowledge` — Save important emails as notes (requires: email)
- `slack-digest` — Slack channel summary (requires: slack)

### Development
- `pr-summary` — Daily GitHub PR summary (requires: github)
- `issue-tracker` — Monitor new issues and notify (requires: github, telegram)
- `code-review-prep` — Summarize PR changes for review (requires: github)

### Knowledge & Research
- `daily-news` — Morning news briefing (requires: nothing — uses web search)
- `topic-monitor` — Track mentions of a topic online (requires: nothing)
- `document-analyzer` — Analyze uploaded documents (requires: vision model)
- `note-intake` — Auto-classify incoming notes (requires: nothing)

### Productivity
- `check-reminders` — Check due tasks and notify (requires: nothing)
- `meeting-prep` — Calendar check + relevant notes (requires: google/calendar)
- `weekly-review` — Summarize what happened this week (requires: nothing)

### Custom / Community
- User-contributed templates via `artifacts/workflows/`
- Pull Requests to add new templates
- Future: marketplace/registry for downloading templates

## Discovery: How Users Find Templates

### 1. After Connector Setup
```
✓ Email connected!

Suggested workflows for Email:
  [Set up Email Digest (read-only)]
  [Set up Urgent Alerts → Telegram]
```

### 2. User Intent → Builder Matches
```
User: "Ich möchte meine Emails automatisch zusammenfassen"
Builder: "Dafür hab ich den 'Email Digest' Workflow.
         Der liest täglich deine Inbox und schickt dir
         eine Zusammenfassung. Soll ich ihn aktivieren?"
```

### 3. Dashboard / Workflows Page
Web UI shows available templates grouped by category, with:
- Ready (green) — all prerequisites met
- Setup needed (yellow) — what's missing
- Tags for filtering

### 4. CLI: `/workflow templates`
```
$ mycelos workflow templates

Available Workflow Templates:

  Communication:
    ✓ email-digest      Daily email summary
    ✗ slack-digest      Slack channel summary (needs: slack connector)

  Development:
    ✗ pr-summary        GitHub PR summary (needs: github connector)

  Knowledge:
    ✓ daily-news        Morning news briefing
    ✓ note-intake       Auto-classify notes

  Productivity:
    ✓ check-reminders   Check due tasks
```

## Community Marketplace (Future)

### Phase 1: Git-based (now)
- Templates in `artifacts/workflows/`
- Community contributes via Pull Request
- Reviewed + merged → available to all users on next update

### Phase 2: Registry (future)
- Online registry at `workflows.mycelos.com` (or similar)
- `/workflow search email` → searches the registry
- `/workflow install community/expense-tracker` → downloads YAML
- Rating system, download counts
- Similar to MCP Registry but for workflows

### Phase 3: Sharing (future)
- Users export their custom workflows: `/workflow export my-workflow`
- Share as YAML file or link
- Import: `/workflow import <url-or-path>`

## Implementation Steps

1. Add `requires` field to existing workflow YAMLs
2. Update Builder prompt with template list + prerequisite checking
3. After connector setup: suggest matching workflows
4. `/workflow templates` CLI command
5. Web UI: Workflows page shows templates with status
6. Community contribution guide in docs

## What We Don't Build (yet)

- No auto-activation (user must explicitly activate)
- No dependency resolution (if email-digest needs email, we don't auto-install email)
- No version management for templates
- No paid marketplace
- No template sandboxing (templates are trusted, like Homebrew formulae)
