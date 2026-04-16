@config-change @trust @policy @learning @risk-low
Feature: Trust Escalation Through Repeated Approvals
  Scenario: System learns user trusts email.read for all agents
    Given the user has approved email.read for 5 different agents
    When a new agent requests email.read
    Then the system suggests: "Grant email.read to all agents permanently?"
    And the user can choose:
      | option                              | risk classification |
      | Yes, for all agents                 | LOW (restricting)   |
      | Yes, for this agent only            | LOW                 |
      | No, keep asking                     | no config change    |

  Scenario: Trust escalation for write operations is more cautious
    Given the user has approved email.send 3 times for email-agent
    Then the system does NOT yet suggest "always" for email.send
    Because write operations require more confirmations (threshold: 10)
    And the suggestion threshold is configurable per operation type
