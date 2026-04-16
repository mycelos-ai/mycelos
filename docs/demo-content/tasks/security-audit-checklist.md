# Security Audit Checklist

## Pre-Release Security Review

### Credential Handling
- [x] Master key never logged or exposed
- [x] API keys encrypted at rest (age encryption)
- [x] Credentials never sent to LLM context
- [x] Proxy intercepts and injects credentials
- [ ] Rotate master key procedure documented

### Agent Isolation
- [x] Agents run in sandboxed environment
- [x] Capability-based access control enforced
- [x] Prefix matching for capabilities (no wildcards)
- [x] Unknown agents denied by default
- [ ] Resource limits per agent (CPU, memory, time)

### Network Security
- [x] SSRF protection on HTTP proxy
- [x] Allowlist for outbound connections
- [x] No direct network access for agents
- [ ] TLS for all external connections
- [ ] Certificate pinning for critical services

### Data Protection
- [x] SQLite WAL mode for data integrity
- [x] All mutations logged in audit trail
- [x] Config rollback via generations
- [ ] Backup encryption
- [ ] Data export/deletion (GDPR)

### Code Review
- [ ] Auditor agent reviews all new agent code
- [ ] No eval() or exec() in user-facing code
- [ ] Input validation on all API endpoints
- [ ] SQL injection prevention (parameterized queries)
