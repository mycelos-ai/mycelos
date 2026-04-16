@config-change @agent @deprecation @lifecycle @risk-low
Feature: Deprecating and Archiving an Agent
  Scenario: Automatic deprecation due to low reputation
    Given log-parser agent has reputation 0.18 after 12 executions
    And an alternative log-parser-v2 exists with reputation 0.75
    Then the system automatically sets log-parser to "deprecated"
    And the Planner stops assigning tasks to log-parser
    And the user is notified in their inbox
    And the deprecated agent's code is preserved for reference

  Scenario: User manually archives an unused agent
    When the user runs "mycelos agent archive web-scraper"
    Then the Blueprint Plan shows:
      | change                              | risk |
      | ~ agents.web-scraper active→archived| LOW  |
    And the agent is removed from the Planner's registry
    And the agent code remains in artifacts/agents/
    And the change auto-approves (LOW risk)

  Scenario: Rollback reactivates archived agent
    Given web-scraper was archived in Gen 15
    When the user rolls back to Gen 14
    Then web-scraper is active again
    And the Planner can assign tasks to it
