@config-change @channel @slack @multi-channel @risk-medium
Feature: Adding Slack as a Communication Channel
  As a team user
  I want to add Slack as a channel
  So that I can interact with Mycelos from Slack

  @channel-setup
  Scenario: Configuring Slack channel via Gateway
    When the user adds Slack as a communication channel
    Then the system requires the Gateway to be running
    And the Slack webhook URL is configured
    And Slack-specific renderer is activated for Rich Content
    And the Blueprint Plan shows:
      | change                    | risk   |
      | + channels.slack          | MEDIUM |
    And the guard period is 5 minutes

  @session-identity
  Scenario: Slack messages link to existing user sessions
    Given the Slack channel is configured
    When a user sends a message via Slack
    Then the session identity service maps the Slack user to the Mycelos user
    And the conversation continues in the same session context
    And inbox items already handled via Terminal don't appear again

  @rich-content-rendering
  Scenario: Rich content renders in Slack Block Kit format
    When an agent produces output with charts and buttons
    Then the Slack renderer converts:
      | content type | slack format            |
      | markdown     | mrkdwn blocks           |
      | tables       | mrkdwn tables           |
      | charts       | PNG image blocks        |
      | buttons      | interactive button blocks|
