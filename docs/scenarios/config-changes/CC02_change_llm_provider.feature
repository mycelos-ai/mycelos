@config-change @llm @provider @blueprint-lifecycle @risk-medium
Feature: Changing LLM Provider from Anthropic to OpenAI
  As a Mycelos user
  I want to switch my LLM provider
  So that I can use a different model or optimize costs

  A provider change is MEDIUM risk because it affects model behavior
  but doesn't change security policies or capabilities.

  Background:
    Given the system uses Anthropic as default LLM provider
    And 5 agents are active, some using Haiku, some Sonnet

  @provider-switch
  Scenario: Switching default provider via Blueprint Lifecycle
    When the user runs "mycelos config apply" with provider change
    Then the plan phase shows:
      """
      Blueprint Plan (Gen 5 → Gen 6):
        ~ llm.default_provider   anthropic → openai   Risk: MEDIUM
        ~ llm.default_model      claude-haiku → gpt-4o-mini  Risk: MEDIUM

        0 additions, 0 removals, 2 changes
        Risk level: MEDIUM — User confirmation required
      """
    And the guard period is 5 minutes (MEDIUM risk)

  @credential-handling
  Scenario: New provider credentials are added securely
    When switching to OpenAI
    Then the system asks for an OpenAI API key
    And the key is encrypted via Credential Proxy
    And the old Anthropic key is NOT deleted (may need rollback)
    And both credentials exist in the Credential Proxy

  @rollback-provider
  Scenario: Auto-rollback if new provider fails
    Given the provider was switched to OpenAI
    When the guard period detects 50% error rate (model API errors)
    Then the system auto-rolls back to the Anthropic generation
    And all agents seamlessly use Anthropic again
    And the user is notified: "Auto-rollback: OpenAI provider caused errors"

  @agent-impact
  Scenario: Agents adapt to new provider transparently
    Given the provider is switched to OpenAI
    Then the LLM Broker translates all agent requests to OpenAI format
    And agents don't need code changes (Broker handles translation)
    And tool-use protocol is translated between formats
    And streaming behavior is preserved
