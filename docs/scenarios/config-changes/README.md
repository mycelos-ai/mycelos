# Configuration Change Scenarios

20 configuration change scenarios in Gherkin format, describing what happens when users modify the Mycelos system through the Blueprint Lifecycle.

## Scenarios

| # | File | Description | Risk |
|---|------|-------------|------|
| CC01 | `CC01_add_email_connector.feature` | Adding email connector with MCP registration | HIGH |
| CC02 | `CC02_change_llm_provider.feature` | Switching from Anthropic to OpenAI | MEDIUM |
| CC03 | `CC03_register_new_agent.feature` | Registering a new agent (always requires human confirm) | HIGH |
| CC04 | `CC04_modify_agent_policy.feature` | Escalating or restricting agent permissions | HIGH/LOW |
| CC05 | `CC05_add_scheduled_task.feature` | Adding a new cron job with frozen permissions | HIGH |
| CC06 | `CC06_credential_rotation.feature` | Rotating API keys (rollback safety!) | MEDIUM |
| CC07 | `CC07_add_github_connector.feature` | Adding GitHub MCP connector | HIGH |
| CC08 | `CC08_sandbox_config_change.feature` | Changing sandbox type or settings | CRITICAL |
| CC09 | `CC09_add_slack_channel.feature` | Adding Slack as communication channel | MEDIUM |
| CC10 | `CC10_workflow_update.feature` | Updating workflow steps and scope | MEDIUM |
| CC11 | `CC11_model_tier_change.feature` | Changing an agent's model tier | MEDIUM |
| CC12 | `CC12_trust_escalation.feature` | Learning from repeated user approvals | LOW |
| CC13 | `CC13_deprecate_agent.feature` | Deprecating and archiving agents | LOW |
| CC14 | `CC14_retention_policy.feature` | Changing data retention settings | LOW/MEDIUM |
| CC15 | `CC15_add_filesystem_connector.feature` | Granting filesystem access with R/W arrays | HIGH |
| CC16 | `CC16_event_trigger.feature` | Adding event-based triggers (webhooks, FS) | HIGH |
| CC17 | `CC17_media_processing_config.feature` | Toggling auto-processing settings | LOW |
| CC18 | `CC18_guardian_rules.feature` | Modifying Guardian Check rules | CRITICAL |
| CC19 | `CC19_concurrency_settings.feature` | Changing scheduler concurrency mode | MEDIUM |
| CC20 | `CC20_generation_flood_protection.feature` | Rate limiting and circuit breaker for configs | LOW |

## Blueprint Lifecycle Coverage

Every scenario documents the 5-phase Blueprint Lifecycle:
1. **Resolve** — Desired change as declarative ChangeSpec
2. **Verify** — SHA-256 integrity, deduplication, race condition detection
3. **Plan** — Risk classification (LOW/MEDIUM/HIGH/CRITICAL), user approval
4. **Apply** — Atomic pointer swap, audit event
5. **Status** — Guard period with health monitoring

## Risk Classification

| Risk | Guard Period | Approval | Examples |
|------|-------------|----------|----------|
| LOW | 2 min | Auto-approve | Description changes, scope restriction |
| MEDIUM | 5 min | User confirmation | Provider change, tool config |
| HIGH | 10 min | User confirmation | New agents, policy expansion, new schedules |
| CRITICAL | 30 min | User + acknowledgment | Security layer, Guardian rules, sandbox |
