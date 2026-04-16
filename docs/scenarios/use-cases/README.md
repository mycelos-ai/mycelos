# User Journey Use Cases

20 end-to-end user journey scenarios in Gherkin format, covering how a user goes from zero to productive with Mycelos.

## Scenarios

| # | File | Description | Tags |
|---|------|-------------|------|
| UC01 | `UC01_personal_email_assistant.feature` | Complete journey from init to daily email summaries | @email @onboarding |
| UC02 | `UC02_github_pr_review.feature` | Automated PR review with event triggers | @github @code-review |
| UC03 | `UC03_invoice_processing.feature` | Multi-agent invoice pipeline with OCR and extraction | @invoice @artifact |
| UC04 | `UC04_cross_channel_interaction.feature` | Terminal to Telegram session continuity | @multi-channel |
| UC05 | `UC05_agent_evolution.feature` | Agent improvement over time via reputation system | @agent-lifecycle @evolution |
| UC06 | `UC06_config_rollback_recovery.feature` | Auto-rollback and manual recovery after bad config | @rollback @guard-period |
| UC07 | `UC07_scheduled_task_management.feature` | Cron jobs with pause-points and circuit breakers | @scheduler @huey |
| UC08 | `UC08_cost_optimization.feature` | Cost-optimized execution with model escalation | @cost @escalation |
| UC09 | `UC09_workflow_composition.feature` | Multi-agent workflow composition and reuse | @workflow @multi-agent |
| UC10 | `UC10_demo_mode.feature` | 60-second guided demo with mock connectors | @demo @onboarding |
| UC11 | `UC11_memory_management.feature` | Memory scoping, context engine, and privacy | @memory @context-engine |
| UC12 | `UC12_degraded_mode.feature` | System operation during LLM provider outage | @degraded-mode @resilience |
| UC13 | `UC13_calendar_task_manager.feature` | Calendar and task management assistant | @calendar @multi-connector |
| UC14 | `UC14_data_analysis_pipeline.feature` | CSV analysis with deterministic + LLM agents | @data-analysis @cost-zero |
| UC15 | `UC15_complaint_handler.feature` | Customer complaint handling with human-in-the-loop | @customer-service @async |
| UC16 | `UC16_web_research_agent.feature` | Web research with Guardian protection | @web-research @guardian |
| UC17 | `UC17_shell_automation.feature` | Shell command automation with whitelist | @shell @sandbox |
| UC18 | `UC18_multi_user_family.feature` | Multi-user family setup with isolation | @multi-user @privacy |
| UC19 | `UC19_audit_compliance.feature` | Audit trail and compliance reporting | @audit @compliance |
| UC20 | `UC20_durable_execution.feature` | Crash recovery via checkpoint-based execution | @durable-execution @crash-recovery |

## Usage with pytest-bdd

These `.feature` files are designed to be used with `pytest-bdd` or `behave` for automated testing:

```bash
pip install pytest-bdd
pytest --feature docs/scenarios/use-cases/
```

## Tags

- `@use-case` — All scenarios in this folder
- `@milestone-1` through `@milestone-5` — Implementation milestone
- `@security` — Security-relevant scenarios
- `@happy-path` — Expected normal flows
- `@error-recovery` — Error handling scenarios
