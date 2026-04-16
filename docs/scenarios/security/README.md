# Security Threat Analysis

20 security threat scenarios in Gherkin format, covering potential attack vectors against the Mycelos architecture and the defenses that should prevent them.

## Threat Matrix

| # | File | Attack Vector | Severity | Defense Layers |
|---|------|--------------|----------|----------------|
| SEC01 | `SEC01_prompt_injection_email.feature` | Malicious email content | CRITICAL | Guardian + Capabilities + Sanitization |
| SEC02 | `SEC02_prompt_injection_github.feature` | Injected instructions in PRs/issues | HIGH | Sandbox + Capabilities + Guardian |
| SEC03 | `SEC03_prompt_injection_web.feature` | Hidden instructions on web pages | HIGH | Capabilities + Rate Limits + Guardian |
| SEC04 | `SEC04_credential_direct_access.feature` | Agent tries to access credentials | CRITICAL | Process isolation + Encryption |
| SEC05 | `SEC05_sandbox_escape_filesystem.feature` | Path traversal, symlinks | CRITICAL | Filesystem isolation + Path validation |
| SEC06 | `SEC06_sandbox_escape_network.feature` | Direct network access attempts | CRITICAL | No network in sandbox |
| SEC07 | `SEC07_capability_token_abuse.feature` | Token replay, forging, scope escalation | HIGH | TTL + Scope + Rate limits |
| SEC08 | `SEC08_policy_bypass.feature` | Self-modification, race conditions | HIGH | Immutable AuditorAgent + Human confirm |
| SEC09 | `SEC09_config_tampering.feature` | Modified config snapshots | HIGH | SHA-256 + Verify phase |
| SEC10 | `SEC10_master_key_compromise.feature` | MYCELOS_MASTER_KEY stolen | CRITICAL | Key rotation + Process isolation |
| SEC11 | `SEC11_creator_agent_backdoor.feature` | Malicious generated agent code | CRITICAL | AuditorAgent + Sandbox testing |
| SEC12 | `SEC12_cross_agent_memory.feature` | Reading/writing other agent's memory | HIGH | Scope enforcement + Security Layer |
| SEC13 | `SEC13_artifact_privacy_breach.feature` | Accessing other tasks' artifacts | HIGH | 5-level privacy scoping |
| SEC14 | `SEC14_response_sanitization.feature` | Credential leaks in responses | HIGH | Response Sanitizer patterns |
| SEC15 | `SEC15_auditor_independence.feature` | Compromising the AuditorAgent | CRITICAL | Hardcoded + No external interface |
| SEC16 | `SEC16_dos_generation_flooding.feature` | Config generation DoS | MEDIUM | Rate limits + Circuit breaker |
| SEC17 | `SEC17_replan_storm.feature` | Cascade of workflow replans | HIGH | Max concurrent + Cooldown + Pin |
| SEC18 | `SEC18_webhook_abuse.feature` | Crafted webhooks and flooding | HIGH | HMAC verification + Rate limiting |
| SEC19 | `SEC19_data_exfiltration_error.feature` | Data leaks through error/timing | MEDIUM | Sanitization + Sandbox isolation |
| SEC20 | `SEC20_multi_user_isolation.feature` | Cross-user data access | CRITICAL | user_id scoping on all queries |

## Attack Categories

### Prompt Injection (SEC01-SEC03)
External content (emails, web pages, GitHub) contains hidden instructions targeting agents. Defense relies on multiple independent layers: Guardian Check, Capability enforcement, Response Sanitization.

### Credential Theft (SEC04, SEC10, SEC14)
Attempts to access raw credentials. Defense relies on process isolation, encrypted storage, and the principle that credentials never leave the Credential Proxy.

### Sandbox Escape (SEC05-SEC06)
Agent tries to break out of its isolated environment. Defense relies on filesystem isolation, network isolation, and resource limits.

### Policy/Token Abuse (SEC07-SEC08)
Attempts to bypass or escalate security policies. Defense relies on immutable AuditorAgent, human confirmation requirements, and token validation.

### System Integrity (SEC09, SEC15-SEC17)
Attacks on config integrity, system agents, or resource exhaustion. Defense relies on content-addressing, hardcoded components, and circuit breakers.

### Data Privacy (SEC12-SEC13, SEC19-SEC20)
Cross-boundary data access attempts. Defense relies on multi-level scoping (user, task, step, session, capability).

## Defense-in-Depth Summary

The Mycelos security model uses 7+ independent defense layers:
1. **Process Isolation** — Agent runs in separate process with stripped environment
2. **Filesystem Isolation** — Only /input, /workspace, /output visible
3. **Credential Proxy** — Credentials never reach agent process
4. **Capability Tokens** — Time-limited, scope-limited, rate-limited
5. **Policy Engine** — Per-agent permission rules
6. **Guardian Check** — Heuristic prompt injection detection
7. **Response Sanitization** — Strip reflected credentials from outputs
8. **AuditorAgent** — Independent, immutable code reviewer

Each layer provides protection even if other layers are compromised.
