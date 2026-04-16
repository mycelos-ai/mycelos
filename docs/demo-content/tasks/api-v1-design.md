# API v1 Design — Task Tracker

## Goal
Design the public REST API for Mycelos, enabling third-party integrations and the mobile app.

## Endpoints (Draft)

### Chat
- `POST /api/chat` — Send a message, get response
- `GET /api/sessions` — List chat sessions
- `GET /api/sessions/:id` — Get session history

### Knowledge
- `GET /api/knowledge` — List notes
- `POST /api/knowledge` — Create note
- `GET /api/knowledge/:id` — Get note
- `PUT /api/knowledge/:id` — Update note
- `DELETE /api/knowledge/:id` — Delete note
- `GET /api/knowledge/search?q=` — Semantic search

### Agents
- `GET /api/agents` — List agents
- `POST /api/agents` — Register new agent
- `GET /api/agents/:id/status` — Agent health

### Workflows
- `GET /api/workflows` — List workflows
- `POST /api/workflows/:id/run` — Execute workflow
- `GET /api/workflows/:id/runs` — Run history

## Open Questions
- [ ] Auth: API keys or OAuth2?
- [ ] Rate limiting: per-user or per-key?
- [ ] Streaming: SSE or WebSocket for chat?
- [ ] Versioning: URL path (/v1/) or header?

## Timeline
- Week 16: API spec finalized
- Week 17: Implementation starts
- Week 19: Internal beta
- Week 20: Public beta
