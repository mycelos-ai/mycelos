@use-case @init @onboarding
Feature: Simplified Init for Non-Technical Users
  As a non-technical Mycelos user
  I want the init process to be as simple as possible
  So that I can start using Mycelos in under a minute

  The init wizard asks for exactly ONE provider, auto-selects both a
  capable and cheap model, sets up smart defaults for all agents
  automatically, and hints at `mycelos model` for advanced tuning.

  @happy-path
  Scenario: Quick init with Anthropic
    When the user runs "mycelos init"
    And selects Anthropic as the provider
    And enters a valid API key
    Then the system auto-selects claude-sonnet-4-6 as the capable model
    And auto-selects claude-haiku-4-5 as the cheap model
    And registers both models in the database
    And registers the 4 system agents (Creator, Planner, Evaluator, Auditor)
    And applies smart defaults (sonnet for Creator/Auditor, haiku for Planner/Evaluator)
    And registers built-in connectors (DuckDuckGo, HTTP)
    And creates the first config generation
    And shows a brief summary: "2 model(s) configured"
    And hints: "mycelos model — add providers, manage model assignments"

  @happy-path @openai
  Scenario: Quick init with OpenAI
    When the user runs "mycelos init"
    And selects OpenAI as the provider
    And enters a valid API key
    Then the system auto-selects gpt-4o as the capable model
    And auto-selects gpt-4o-mini as the cheap model
    And the process completes in the same number of steps

  @no-provider
  Scenario: Skip provider selection — init still creates DB
    When the user runs "mycelos init"
    And selects an invalid provider number
    Then the database is still initialized
    And system agents are registered
    And the user sees "You can configure later with 'mycelos model add'"

  @api-key-from-env
  Scenario: API key auto-detected from environment
    Given ANTHROPIC_API_KEY is set in the environment
    When the user runs "mycelos init" and selects Anthropic
    Then the system picks up the key automatically
    And does NOT prompt for a key
    And stores it encrypted in the credential proxy

  @no-manual-model-selection
  Scenario: No manual model selection during init
    When the user runs "mycelos init"
    Then the user is NOT asked to pick individual models
    And the user is NOT asked to manually assign models to agents
    And the user is NOT asked about connectivity testing
    And all these features are available via "mycelos model"

  @plausibility-shown
  Scenario: Plausibility warnings shown non-interactively
    Given only expensive models are available for the selected provider
    When init auto-selects models
    Then the user sees a warning about missing cheap tier
    But the init continues without asking for confirmation
    And the user can fix it later via "mycelos model add"
