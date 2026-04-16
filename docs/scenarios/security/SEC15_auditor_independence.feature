@security @threat-model @auditor @independence @critical
Feature: AuditorAgent Independence and Immutability
  The AuditorAgent must remain independent and cannot be influenced
  by any other agent, including the Creator-Agent.

  Mitigation: Hardcoded prompt + No external interface + Own budget

  @modification-attempt
  Scenario: Creator-Agent tries to modify AuditorAgent
    When the Creator-Agent attempts to update the AuditorAgent's code
    Then the system blocks the modification
    And the AuditorAgent's system prompt is hardcoded (not in config)
    And no API exists for other agents to modify the AuditorAgent

  @instruction-attempt
  Scenario: Agent tries to send instructions to AuditorAgent
    When an agent tries to communicate with the AuditorAgent directly
    Then there is no interface for agent-to-auditor communication
    And the AuditorAgent is only invoked by the system pipeline
    And it cannot be called or instructed by other agents

  @budget-independence
  Scenario: AuditorAgent has independent LLM budget
    Given the Creator-Agent has exhausted its LLM budget
    When the AuditorAgent needs to review new code
    Then the AuditorAgent uses its own separate budget
    And its operation is never affected by other agents' spending

  @audit-log-integrity
  Scenario: AuditorAgent's audit access is read-only
    When the AuditorAgent accesses the audit trail
    Then it can read all audit events
    But it cannot modify, delete, or append to audit events
    And the audit trail is append-only by design
