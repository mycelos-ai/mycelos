@config-change @guardian @security @blueprint-lifecycle @risk-critical
Feature: Modifying Guardian Check Rules
  Guardian rules are CRITICAL because they are the last line of defense
  against prompt injection and unauthorized actions.

  Scenario: Adding a new domain to email allowlist
    When the user adds "company.com" to the Guardian's known email domains
    Then the Blueprint Plan shows risk CRITICAL (security layer change)
    And the guard period is 30 minutes
    And the user must provide explicit acknowledgment

  Scenario: Disabling Guardian for specific connector (NOT recommended)
    When the user attempts to disable Guardian for shell connector
    Then the system warns: "Disabling Guardian removes prompt injection protection"
    And requires explicit double-confirmation
    And the audit log records the security-critical decision
    And the guard period is 30 minutes with heightened monitoring

  Scenario: Rollback of Guardian changes
    When a Guardian rule change causes false positives
    And the user rolls back
    Then the old Guardian rules are restored immediately
    And the false positive incidents are logged for future rule improvement
