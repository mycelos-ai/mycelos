# Security Architecture Decisions — Process Boundaries & Credential Isolation

**Date:** 2026-03-24
**Status:** DECISION (accepted)

---

## Core Decision

A single-process architecture CAN be secure IF all security boundaries are
enforced centrally, unavoidably, and consistently. Our current state has gaps.

**Three mandatory actions:**
1. Close all Security Gate gaps (ChatService, WorkflowRunner, MCP)
2. Credential Broker in separate process (P1)
3. High-risk gateways/connectors in isolated workers (P2)

---

## Principles (accepted, non-negotiable)

1. LLM is NEVER the trust anchor — it creates intent, never final authorization
2. Deny-by-default — every action needs explicit Capability + Policy
3. No secret passed to LLM — not in prompt, not in tool output
4. Unavoidable Policy Gates — before every tool/DB/network access
5. Isolation beats convention — critical functions in own processes
6. Auditability as security control — all sensitive decisions must be traceable

---

## Architecture: What to isolate, what stays in-process

### In-Process (acceptable when gates enforced)
- Deterministic internal functions
- Memory read/write (scoped, sanitized)
- Policy Engine evaluation
- Config generation management

### Must Isolate (P1: Credential Broker)
- Master key never in LLM orchestrator process
- Access via local authenticated broker API (Unix socket)
- Broker issues short-lived, purpose-bound derived credentials
- Strict operation allowlist, no "get all secrets" path

### Must Isolate (P2: Worker Processes)
- MCP connectors (external subprocess — already done for stdio)
- Shell-adjacent tools
- Generic HTTP/DB tools
- Untrusted plugins/custom agents

---

## Guardrails Against "LLM builds agent with DB/secret access"

1. No dynamic tool registration from model output
2. No raw SQL tool for LLM — only curated, parameterized operations
3. Capability Tokens per step: narrow scope, short TTL
4. Policy Engine enforced everywhere: Chat, Workflow, Runtime, MCP
5. OS-level egress control per worker
6. Filesystem mount whitelists + path normalization
7. Output Guardian/DLP: block/redact secret patterns
8. Tool allowlist + name collision/shadowing protection
9. Human-in-the-loop for high/critical actions
10. Tamper-evident audit for all sensitive events

---

## Acceptance Criteria

- LLM receives no raw credentials in any path
- Every external access backed by Policy + Capability + Audit
- Secret exfiltration tests fail (desired: block/redaction)
- Pen-test scenarios for MCP/connectors pass
- Rollback/change processes for security config are controlled and audited

---

## Implementation Roadmap

### P0 (this sprint) — Close the gates
- Unified enforcement wrapper for all tool calls
- Policy + Capability + Sanitizer + Audit on EVERY path
- Fix the 6 quick-fixes from overnight review

### P1 (next sprint) — Credential Broker
- Separate process for credential management
- Unix socket API with peer authentication
- Short-lived derived credentials instead of raw secrets
- Remove load_credential_to_env from app.py

### P2 (following sprint) — Worker Isolation
- MCP HTTP transport already isolates (no subprocess)
- MCP stdio runs in subprocess (already isolated)
- Guardian as mandatory post-execution check
- Blueprint lifecycle with risk classification
