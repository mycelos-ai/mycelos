---
title: Security
description: Fail-closed security model with credential encryption, capability scoping, and a tamper-evident audit trail.
order: 7
icon: shield
---

Security in Mycelos is not optional. Every component is built with a fail-closed security model.

## SecurityProxy

All external network access goes through a separate SecurityProxy process. Agents never make direct network calls. The proxy enforces rate limits, domain allowlists, and content filtering.

## Credential Encryption

Credentials are encrypted at rest using AES-256-GCM with your `MYCELOS_MASTER_KEY`. They are decrypted only at the moment of use, inside the SecurityProxy process.

## Credential Isolation

No credential ever appears in:

- LLM prompts or context windows
- Log files or audit trails
- Error messages or tracebacks
- Agent memory or knowledge base

## Policy Engine

Per-tool access control with prefix matching. An agent with capability `github.read` can use `github.read.issues` and `github.read.repos` but not `github.write`.

## Audit Trail

Every state-mutating operation is logged. The audit trail is append-only and tamper-evident. View it from the Settings page or CLI:

```bash
mycelos audit list --last 50
```

## Sandbox

Agents run in sandboxed subprocesses with limited filesystem access, no direct network, and restricted system calls.
