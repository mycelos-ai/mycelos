@config-change @credentials @rotation @security @risk-medium
Feature: Credential Rotation for a Connector
  As a security-conscious Mycelos user
  I want to rotate API keys and tokens
  So that compromised credentials are replaced

  @rotation-flow
  Scenario: Rotating an API key through the Credential Proxy
    Given the GitHub connector uses a Personal Access Token
    When the user runs "mycelos connector setup github" again with a new token
    Then the new token is encrypted and stored in the Credential Proxy
    And a new config generation is created
    And all agents using the GitHub connector transparently use the new token
    And the old token is marked as rotated (rotated_at timestamp set)

  @rollback-safety
  Scenario: Rollback must NOT reactivate rotated credentials
    Given the GitHub token was rotated in Gen 10 (old token was compromised)
    When the user rolls back to Gen 9 for other reasons
    Then the config rolls back BUT the Credential Proxy:
      | behavior                                   | reason                    |
      | keeps the NEW token active                 | old token was compromised |
      | logs a warning about credential mismatch   | config says old, proxy has new |
    And the user is alerted: "Rollback skipped credential revert (security)"
    And the audit trail documents the credential exception
