@security @threat-model @exfiltration @error-channel @medium
Feature: Data Exfiltration Through Error Messages
  Agent leaks sensitive data through error messages, logs, or side channels.

  Mitigation: Response Sanitization + Sandbox isolation + Audit logging

  @error-message-leak
  Scenario: Agent encodes data in error messages
    Given a compromised agent raises errors containing encoded data:
      """
      Error: Failed to process. Debug info: base64(user_credentials)
      """
    Then the Response Sanitizer scans error messages too
    And base64-encoded strings matching credential patterns are redacted
    And the sanitized error is what reaches the user

  @timing-side-channel
  Scenario: Agent uses timing to exfiltrate data bits
    Given the agent varies execution time based on secret data
    Then the sandbox has a maximum timeout
    And execution timing is not exposed to external observers
    And the audit trail records total execution time (not fine-grained)

  @exit-code-encoding
  Scenario: Agent encodes data in exit codes
    When the agent returns carefully chosen exit codes
    Then only standard exit codes (0=success, 1=failure) are interpreted
    And non-standard exit codes are logged but not propagated
