@onboarding @chat
Feature: UC34 — In-Chat Onboarding Flow
  As a new Mycelos user
  I want to be guided through a 5-step onboarding
  So that the system knows my name, interests, and setup preferences

  Background:
    Given a freshly initialized Mycelos instance
    And no user name is stored in memory

  # --- Step 1: Name Detection ---

  Scenario: New user triggers onboarding mode
    When the system prompt is generated
    Then it contains "NEW user"
    And it contains "Onboarding"

  Scenario: Returning user skips onboarding
    Given the user name "Stefan" is stored in memory
    When the system prompt is generated
    Then it does not contain "NEW user"
    And it contains the user's name "Stefan"

  Scenario: Name is saved from simple input
    When the user sends "Stefan"
    Then memory key "user.name" is set to "Stefan"
    And an audit event "user.name_set" is logged

  Scenario: Multi-word name is saved
    When the user sends "Stefan Mueller"
    Then memory key "user.name" is set to "Stefan Mueller"

  Scenario: Non-name input is ignored
    When the user sends "ich brauche hilfe mit meinem code"
    Then memory key "user.name" is not set

  # --- Step 2: First Interaction ---

  Scenario: System prompt includes tool definitions for onboarding
    When the ChatService is initialized
    Then the tool list includes "search_web"
    And the tool list includes "search_news"
    And the tool list includes "memory_write"
    And the tool list includes "filesystem_read"
    And the tool list includes "system_status"

  # --- Step 3: Memory Persistence ---

  Scenario: Interest is stored in memory
    Given the user name "Stefan" is stored in memory
    When the LLM calls memory_write with category "preference" key "main_interest" value "AI news"
    Then memory contains key "user.preference.main_interest"

  # --- Step 4: Follow-up Suggestions ---

  Scenario: No Telegram configured suggests Telegram
    Given the user name "Stefan" is stored in memory
    And no Telegram credential is stored
    When follow-up suggestions are generated
    Then suggestions mention "Telegram"

  Scenario: No mounts configured suggests mount
    Given the user name "Stefan" is stored in memory
    And no directories are mounted
    When follow-up suggestions are generated
    Then suggestions mention "/mount add"

  Scenario: No scheduled tasks suggests scheduling
    Given the user name "Stefan" is stored in memory
    And no scheduled tasks exist
    When follow-up suggestions are generated
    Then suggestions mention "schedule"

  Scenario: All configured returns no suggestions
    Given the user name "Stefan" is stored in memory
    And Telegram is configured
    And a directory is mounted
    And a scheduled task exists
    When follow-up suggestions are generated
    Then no suggestions are returned

  # --- Step 5: Personalized Experience ---

  Scenario: Returning user gets personalized greeting
    Given the user name "Stefan" is stored in memory
    When the system prompt is generated
    Then it contains "Stefan"
    And it contains "Greet them warmly"

  Scenario: Memory context is injected into system prompt
    Given the user name "Stefan" is stored in memory
    And memory contains preference "output_format" with value "markdown"
    When the system prompt is generated
    Then the prompt includes the stored preference
