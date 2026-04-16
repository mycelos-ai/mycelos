@config-change @llm @init @model-validation @plausibility
Feature: Model Plausibility Check
  As a Mycelos user
  I want the system to validate my model configuration
  So that I have both cost-effective and capable models configured correctly

  Mycelos's cost optimization strategy requires both a capable model (sonnet/opus tier)
  for complex tasks and a cheap model (haiku tier) for simple classification/routing.
  Validation runs during init (automatic) and via `mycelos model check` (interactive).

  Background:
    Given the database has been initialized

  @plausibility-check @tier-coverage
  Scenario: User has both capable and cheap models — no warnings
    Given the user has configured the following models:
      | model_id          | tier   | provider  |
      | claude-sonnet-4-6 | sonnet | anthropic |
      | claude-haiku-4-5  | haiku  | anthropic |
    When the plausibility check runs
    Then the summary shows "Configuration looks good"
    And no warnings are displayed
    And both tiers are marked as covered

  @plausibility-check @missing-cheap
  Scenario: User only has expensive models — warns about missing cheap tier
    Given the user has configured the following models:
      | model_id          | tier   | provider  |
      | claude-sonnet-4-6 | sonnet | anthropic |
      | claude-opus-4-6   | opus   | anthropic |
    When the plausibility check runs
    Then the summary shows a warning about missing haiku-tier model
    And the warning explains that classification and routing tasks will use expensive models
    And the warning suggests adding a cheap model like "claude-haiku-4-5" or "gpt-4o-mini"

  @plausibility-check @missing-capable
  Scenario: User only has cheap models — warns about missing capable tier
    Given the user has configured the following models:
      | model_id         | tier  | provider  |
      | claude-haiku-4-5 | haiku | anthropic |
    When the plausibility check runs
    Then the summary shows a warning about missing sonnet/opus-tier model
    And the warning explains that complex tasks may produce lower quality results
    And the warning suggests adding a capable model like "claude-sonnet-4-6" or "gpt-4o"

  @plausibility-check @single-provider
  Scenario: User has models from only one provider — info about resilience
    Given the user has configured the following models:
      | model_id          | tier   | provider  |
      | claude-sonnet-4-6 | sonnet | anthropic |
      | claude-haiku-4-5  | haiku  | anthropic |
    When the plausibility check runs
    Then the summary shows an info that all models are from one provider
    And the info suggests adding a second provider for cross-provider failover

  @plausibility-check @multi-provider
  Scenario: User has models from multiple providers — no resilience warning
    Given the user has configured the following models:
      | model_id          | tier   | provider  |
      | claude-sonnet-4-6 | sonnet | anthropic |
      | gpt-4o-mini       | haiku  | openai    |
    When the plausibility check runs
    Then no resilience warning is shown
    And the summary confirms cross-provider failover is available

  @connectivity-test
  Scenario: LLM connectivity test succeeds for all models
    Given the user has configured "claude-sonnet-4-6" with a valid API key
    When the connectivity test runs via "mycelos model test"
    Then a short test prompt is sent to each model
    And the test confirms "claude-sonnet-4-6" is reachable
    And the model is marked as verified

  @connectivity-test @failure
  Scenario: LLM connectivity test fails for a model
    Given the user has configured "claude-sonnet-4-6" with an invalid API key
    When the connectivity test runs
    Then the test reports that "claude-sonnet-4-6" failed with an error message
    And the model is marked as unreachable
    And the system does NOT abort — other models may still work

  @summary
  Scenario: Configuration summary is shown at the end of init
    Given the user has configured the following models:
      | model_id          | tier   | provider  | cost_in | cost_out |
      | claude-sonnet-4-6 | sonnet | anthropic | 0.003   | 0.015    |
      | claude-haiku-4-5  | haiku  | anthropic | 0.0008  | 0.004    |
    And the plausibility check has completed
    When the configuration summary is displayed
    Then the summary shows number of models per tier
    Then the summary shows number of providers configured
    Then the summary shows any warnings or issues found
