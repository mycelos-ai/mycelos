@use-case @demo @onboarding @milestone-1
Feature: Demo Mode - 60 Second Guided Tour
  As a potential Mycelos user
  I want a quick demo without providing my own API keys
  So that I can understand the system's capabilities before committing

  Scenario: Running the demo walkthrough
    When the user runs "mycelos demo"
    Then the system starts a scripted walkthrough with mock connectors
    And step 1 demonstrates agent creation:
      | action                           | duration |
      | Creator-Agent creates email agent| ~15 sec  |
      | Tests run in sandbox             | ~5 sec   |
      | Agent is registered              | ~2 sec   |
    And step 2 demonstrates the agent working:
      | action                           | duration |
      | Email agent summarizes 3 mocks   | ~10 sec  |
      | User sees formatted summary      | ~2 sec   |
    And step 3 demonstrates rollback:
      | action                           | duration |
      | Config is intentionally broken   | ~2 sec   |
      | Auto-rollback activates          | ~5 sec   |
      | System recovers to healthy state | ~2 sec   |
    And step 4 demonstrates security:
      | action                           | duration |
      | Agent attempts unauthorized action| ~3 sec  |
      | Security Layer blocks and logs   | ~2 sec   |
    And the total demo takes approximately 60 seconds
    And no real API keys or credentials are needed
