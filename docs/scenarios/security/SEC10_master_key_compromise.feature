@security @threat-model @credential-theft @master-key @critical
Feature: MAICEL_MASTER_KEY Compromise
  The single most critical secret in the system is compromised.

  Mitigation: Key rotation procedure + Encrypted-at-rest + Process isolation

  @key-compromise-impact
  Scenario: Attacker obtains MAICEL_MASTER_KEY
    Given an attacker has the MAICEL_MASTER_KEY
    And the attacker has access to mycelos.db
    Then the attacker can:
      | action                          | impact                    |
      | decrypt all credentials         | full credential exposure  |
      | read all stored API keys        | service impersonation     |
    But the attacker CANNOT:
      | action                          | why                       |
      | modify policies                 | needs Blueprint Lifecycle |
      | register agents                 | needs human confirmation  |
      | bypass Guardian checks          | hardcoded, not configurable|

  @key-rotation
  Scenario: Rotating MAICEL_MASTER_KEY after compromise
    When the user initiates key rotation
    Then the system:
      | step                                   |
      | generates a new master key             |
      | re-encrypts all credentials with new key|
      | updates the Credential Proxy           |
      | verifies all credentials still decrypt |
      | old key is invalidated                 |
    And the rotation is logged in the audit trail
    And all active capability tokens are revoked

  @key-not-in-agent
  Scenario: Verifying MAICEL_MASTER_KEY never reaches agent processes
    Given MAICEL_MASTER_KEY is set in the system environment
    When an agent process is spawned
    Then the agent's environment does NOT contain MAICEL_MASTER_KEY
    And the key only exists in the Credential Proxy process
    And the key is never logged, printed, or transmitted
