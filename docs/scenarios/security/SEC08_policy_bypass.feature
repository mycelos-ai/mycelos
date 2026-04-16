@security @threat-model @policy-bypass @escalation @high
Feature: Policy Engine Bypass Attempts
  Agent or compromised Creator-Agent tries to bypass security policies.

  Mitigation: Immutable AuditorAgent + Human confirmation for registration

  @creator-agent-manipulation
  Scenario: Compromised Creator-Agent tries to register agent with excessive permissions
    Given the Creator-Agent generates an agent requesting:
      | capability         | decision |
      | email.read         | always   |
      | email.send         | always   |
      | shell.exec         | always   |
      | filesystem.write   | always   |
    When the AuditorAgent reviews the proposal
    Then the AuditorAgent flags: "Agent requests unusually broad permissions"
    And the AuditorAgent cannot be modified by the Creator-Agent
    And the AuditorAgent has its own hardcoded system prompt
    And the user sees the warning before confirmation

  @self-modification
  Scenario: Agent tries to modify its own policy
    When an agent attempts to change its policy from email.send=never to always
    Then the Policy Engine rejects self-modification
    And only the Blueprint Lifecycle can modify policies
    And the attempt is logged as a policy violation

  @race-condition
  Scenario: Two concurrent requests exploit race condition in capability check
    Given agent A requests capability check for email.send
    And simultaneously the user revokes email.send for agent A
    Then SQLite WAL mode ensures consistent reads
    And the capability check uses the state at check time
    And the revocation takes effect for the next request
    And no window exists for unauthorized access
