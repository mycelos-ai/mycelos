@use-case @model-management @advanced
Feature: Model Management Command
  As a Mycelos power user
  I want to manage models and agent assignments in detail
  So that I can optimize cost, quality, and resilience

  The `mycelos model` command provides an interactive drill-down interface
  for adding/removing providers, viewing agent assignments, and changing
  which model each agent uses.

  @model-list
  Scenario: List all configured models
    Given Mycelos is initialized with 3 models
    When the user runs "mycelos model list"
    Then a table shows all models with provider, tier, cost, and context window
    And a quick validation summary shows any issues

  @model-add
  Scenario: Add a second provider
    Given Mycelos is initialized with Anthropic only
    When the user runs "mycelos model add"
    And selects OpenAI and enters a valid API key
    Then the user sees a table of available OpenAI models
    And can select which models to enable (comma-separated or 'all')
    And selected models are registered in the database
    And the user is asked whether to update smart defaults
    And a new config generation is created

  @model-remove
  Scenario: Remove a model
    Given "gpt-4o-mini" is registered
    When the user runs "mycelos model remove gpt-4o-mini"
    And confirms the removal
    Then the model and its agent assignments are deleted
    And a new config generation is created

  @model-test
  Scenario: Test connectivity for all models
    When the user runs "mycelos model test"
    Then each model is tested with a short prompt
    And reachable models show a green checkmark
    And unreachable models show a red cross with the error

  @model-test-single
  Scenario: Test connectivity for one model
    When the user runs "mycelos model test claude-sonnet-4-6"
    Then only that model is tested
    And the result is shown

  @model-agents @drill-down
  Scenario: View and edit agent model assignments
    Given Mycelos has 4 agents and 3 models configured
    When the user runs "mycelos model agents"
    Then a table shows each agent with its primary and fallback models
    And system defaults (execution + classification) are shown at the bottom
    When the user enters an agent number
    Then the current assignments for that agent are shown
    And a numbered list of available models is displayed
    And the user can enter comma-separated model numbers to set new assignments
    And the first model becomes primary, subsequent ones become fallbacks
    And a new config generation is created

  @model-agents @keep-defaults
  Scenario: Keep current agent assignments
    Given the user is in the agent drill-down view
    When the user presses Enter without entering numbers
    Then the current assignments are kept unchanged

  @model-agents @quit
  Scenario: Quit the agent view
    Given the agent overview table is displayed
    When the user enters 'q'
    Then the command exits cleanly

  @model-check
  Scenario: Run full plausibility check
    When the user runs "mycelos model check"
    Then a tier summary table is shown (opus/sonnet/haiku with counts)
    And all validation issues are listed (warnings + infos)
    And the user is asked whether to test connectivity
    And connectivity results are shown for each model
