@config-change @circuit-breaker @dos-protection @risk-low
Feature: Config Generation Flood Protection
  The system has built-in limits to prevent pathological generation growth.

  Scenario: Warning when approaching generation limit
    Given 18 config changes have been made in the last hour
    When another change is attempted
    Then the system shows a warning: "20 changes/hour is unusual. Proceed?"
    And the change proceeds after confirmation

  Scenario: Hard limit blocks excessive changes
    Given 100 config changes have been made in the last hour
    When another change is attempted
    Then the system blocks: "Circuit breaker: 100 generations/hour exceeded"
    And the change is rejected
    And the user must wait or use "--force" to override

  Scenario: Daily limit prevents runaway automation
    Given an automated process created 480 generations today
    When the 501st change is attempted
    Then the hard daily limit (500) blocks the change
    And the user is alerted about excessive automated changes
    And the audit trail records the blocked attempt
