@security @threat-model @sanitization @credential-leak @high
Feature: Response Sanitization Preventing Credential Leaks
  API responses or agent outputs accidentally contain credentials
  that must be stripped before reaching the user or other agents.

  Mitigation: Response Sanitizer in Security Layer

  @reflected-token
  Scenario: API response reflects the authentication token
    Given the Credential Proxy injects Bearer token in request
    When the API response contains the reflected token in error message:
      """
      Error: Authentication failed for token "sk-abc123..."
      """
    Then the Response Sanitizer detects the reflected credential
    And replaces it with "[REDACTED]"
    And the agent receives: 'Error: Authentication failed for token "[REDACTED]"'

  @credential-in-output
  Scenario: Agent output accidentally contains credential patterns
    When an agent's output contains text matching credential patterns:
      | pattern                    | example              |
      | sk-[a-zA-Z0-9]{20,}      | sk-abc123def456...   |
      | ghp_[a-zA-Z0-9]{20,}     | ghp_xxxyyy...        |
      | Bearer [a-zA-Z0-9+/=]+   | Bearer eyJhbG...     |
    Then the Response Sanitizer redacts all matches
    And the sanitized output is logged (without credentials)

  @env-var-leak
  Scenario: Agent output contains environment variable values
    When an agent output references system paths or config values
    Then the Guardian Check flags potential data leakage
    And sensitive system information is redacted
