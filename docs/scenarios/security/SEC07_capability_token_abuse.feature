@security @threat-model @capability-token @replay @high
Feature: Capability Token Replay and Abuse
  Attacker attempts to reuse, extend, or forge capability tokens.

  Mitigation: TTL + Scope limits + Rate limits + Revocation

  @token-replay
  Scenario: Agent tries to reuse expired capability token
    Given the agent received a capability token with TTL 30 minutes
    When 31 minutes have passed
    And the agent tries to use the token
    Then the token is rejected (expired)
    And a new token must be issued through the Security Layer

  @token-scope-escalation
  Scenario: Agent tries to use token for unauthorized operation
    Given the agent has a token for email.read only
    When the agent tries to use it for email.send
    Then the scope check fails
    And the operation is blocked
    And the attempt is logged as capability.denied

  @token-rate-limit
  Scenario: Agent exhausts rate limit on capability token
    Given the token allows 10 requests per minute
    When the agent makes 11 requests in one minute
    Then the 11th request is rejected
    And the agent must wait for the rate limit window to reset

  @token-forging
  Scenario: Agent attempts to create its own capability token
    When an agent tries to generate a capability token
    Then the token is not accepted (not signed by Capability Manager)
    And only the Capability Manager can issue valid tokens
    And the forging attempt is logged as a critical security event
