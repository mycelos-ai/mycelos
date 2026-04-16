# Workflow Templates — Concept

## Problem
Users love workflows but struggle to create them from scratch. The Builder agent helps, but many users want pre-built starting points.

## Proposal: Template Gallery
A curated set of workflow templates that users can install with one click and customize.

## Template Categories

### Productivity
- **Morning Briefing** — weather, calendar, news, tasks summary
- **Weekly Review** — accomplishments, upcoming deadlines, goal progress
- **Email Digest** — summarize unread emails, draft responses

### Research
- **Topic Deep Dive** — search + summarize + save to knowledge
- **Competitor Watch** — monitor competitor websites, report changes
- **Paper Summary** — fetch arxiv paper, extract key findings

### Development
- **PR Review Assistant** — analyze diff, suggest improvements
- **Dependency Audit** — check for outdated/vulnerable packages
- **Release Notes** — generate changelog from git history

### Content
- **Blog Post Draft** — outline + first draft from topic
- **Social Media Pack** — adapt content for Twitter, LinkedIn, Mastodon
- **Newsletter Builder** — curate links + write commentary

## Implementation
1. Templates stored as YAML in `~/.mycelos/templates/`
2. Gallery UI in web interface
3. One-click install creates workflow + required connectors
4. Templates can be shared (export as ZIP)

## Next Steps
- [ ] Design template YAML format
- [ ] Build 5 starter templates
- [ ] Add gallery page to web UI
