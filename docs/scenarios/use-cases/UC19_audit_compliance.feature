@use-case @audit @compliance @observability @milestone-2
Feature: Audit Trail and Compliance Reporting
  As a compliance-conscious user
  I want a complete audit trail of all system actions
  So that I can prove what happened and when

  Background:
    Given Mycelos is running with full audit logging enabled

  @comprehensive-logging
  Scenario: Every action creates an audit event
    When the following actions occur:
      | action                    | event_type                    | severity |
      | user starts chat session  | session.started               | info     |
      | Planner selects workflow  | plan.reused                   | info     |
      | agent starts execution    | agent.execution.started       | info     |
      | capability token issued   | capability.issued             | info     |
      | credential accessed       | credential.accessed           | info     |
      | agent completes           | agent.execution.completed     | info     |
      | config generation applied | config.generation.applied     | info     |
      | policy violation blocked  | capability.denied             | warning  |
      | auto-rollback triggered   | config.generation.rollback    | critical |
    Then each event is stored in audit_events with:
      | field         | populated                          |
      | timestamp     | ISO 8601 with milliseconds         |
      | user_id       | always present                     |
      | agent_id      | when applicable                    |
      | task_id       | when applicable                    |
      | generation_id | current config generation          |
      | details       | JSON with event-specific data      |

  @credential-audit
  Scenario: Credential access is logged without exposing secrets
    When an agent accesses the email connector's credentials
    Then the audit event records:
      | field            | value                    |
      | event_type       | credential.accessed      |
      | agent_id         | email-summary            |
      | details.service  | email_imap               |
      | details.operation| inject_auth_header       |
    And the actual credential value is NEVER in the audit log

  @audit-cli
  Scenario: User queries the audit trail
    When the user runs "mycelos audit log --type capability.denied --last 7d"
    Then all policy violation events from the past 7 days are shown
    And each entry includes: timestamp, agent, attempted action, reason for denial
