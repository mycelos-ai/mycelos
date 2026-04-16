@security @chat @policy @sanitization @critical
Feature: ChatService Security Gate
  The ChatService is the main entry point for user interaction.
  Every tool call must pass through the PolicyEngine and ResponseSanitizer.

  Background:
    Given Mycelos is initialized
    And the PolicyEngine is wired into the ChatService
    And the ResponseSanitizer is wired into the ChatService

  # --- Policy Enforcement ---

  Scenario: Tool call is checked against policy
    Given a policy exists:
      | user_id | resource    | decision |
      | default | search.web  | always   |
    When the LLM invokes search_web
    Then the PolicyEngine should evaluate "search.web"
    And the call should be allowed
    And an audit event "policy.evaluated" should be logged

  Scenario: Tool call is blocked when "never"
    Given a policy exists:
      | user_id | resource      | decision |
      | default | filesystem.write | never |
    When the LLM invokes filesystem_write
    Then the PolicyEngine should return "never"
    And the tool call should NOT be executed
    And the LLM should receive an error message:
      "Tool 'filesystem_write' is blocked by policy."
    And an audit event "policy.denied" should be logged

  Scenario: Tool call prompts on "confirm"
    Given a policy exists:
      | user_id | resource       | decision |
      | default | filesystem.write | confirm |
    When the LLM invokes filesystem_write
    Then the user should be asked whether to allow the call
    And on "Yes" the call should be executed
    And on "No" the call should be blocked

  Scenario: Default policy is "always" for chat tools
    Given no specific policy for search_web exists
    When the LLM invokes search_web
    Then the default policy "always" should apply
    And the call should be executed without prompting

  # --- Response Sanitization ---

  Scenario: API key in tool result is redacted
    Given search_web returns a result containing "sk-ant-abc123xyz"
    When the result passes through the ResponseSanitizer
    Then "sk-ant-abc123xyz" should be replaced with "[REDACTED]"
    And the LLM should see the redacted result

  Scenario: SSH paths are redacted
    Given filesystem_read returns a result containing "/home/stefan/.ssh/id_rsa"
    When the result passes through the ResponseSanitizer
    Then the path should be replaced with "[REDACTED_PATH]"

  Scenario: Normal content is not modified
    Given search_web returns a result about "AI News 2026"
    When the result passes through the ResponseSanitizer
    Then the content should remain unchanged

  # --- Audit Trail ---

  Scenario: Every tool call is logged
    When the LLM invokes search_web with query="AI News"
    Then an audit event should be logged:
      | field      | value                |
      | event_type | tool.executed        |
      | details    | tool=search_web      |
    And the event should contain the session ID
    And the event should contain the user ID

  Scenario: Blocked call is logged
    Given the policy for http_get is "never"
    When the LLM invokes http_get
    Then an audit event should be logged:
      | field      | value                |
      | event_type | tool.blocked         |
      | details    | tool=http_get, reason=policy:never |
