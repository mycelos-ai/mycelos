@config-change @llm @model-tier @risk-medium
Feature: Changing an Agent's Default Model Tier
  Scenario: Upgrading agent from Haiku to Sonnet
    Given email-summary agent uses model_tier "haiku"
    When the user upgrades it to "sonnet"
    Then the Blueprint Plan shows risk MEDIUM (model within same provider)
    And the guard period monitors cost changes and output quality
    And model_usage tracks the cost difference

  Scenario: Downgrading agent to save costs
    Given research-agent uses model_tier "opus"
    When the user downgrades to "sonnet"
    Then the guard period monitors for quality degradation
    And if output quality drops significantly, user is alerted
