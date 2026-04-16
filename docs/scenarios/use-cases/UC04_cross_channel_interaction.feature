@use-case @multi-channel @telegram @terminal @milestone-5
Feature: Cross-Channel Interaction - Terminal to Telegram
  As a user who works from both desktop and mobile
  I want to start tasks on my terminal and continue on Telegram
  So that I stay productive even when away from my computer

  Background:
    Given Mycelos is initialized and the gateway is running
    And the terminal channel is active
    And the Telegram channel is configured via "mycelos connector setup telegram"

  @session-identity
  Scenario: Session persists across channels
    Given the user starts "mycelos chat" in the terminal
    And starts a conversation about email summaries
    When the user switches to Telegram
    Then the same session is available (matched by user_id)
    And the conversation history is visible
    And any pending tasks are shown in the inbox briefing

  @inbox-briefing @channel-switch
  Scenario: Inbox briefing reflects actions from other channels
    Given the user approved an email draft via Telegram at 10:00
    When the user opens "mycelos chat" in the terminal at 11:00
    Then the inbox briefing does NOT show the already-approved draft
    And the briefing shows only new items since the Telegram approval

  @rich-content @progressive-enhancement
  Scenario: Rich content adapts to channel capabilities
    Given the email-summary agent produces output with:
      | layer | content                                    |
      | 0     | Plain text summary                         |
      | 1     | Markdown with tables and headers            |
      | 2     | Bar chart showing email priorities          |
      | 3     | Approve/Reject buttons for draft responses  |
    When viewed in the terminal
    Then the terminal renders: Rich markdown tables, ASCII chart via plotext, alt_text for buttons
    When viewed on Telegram
    Then Telegram renders: HTML formatted text, PNG chart via matplotlib, inline keyboard buttons

  @pause-point @notification
  Scenario: Scheduled task notifies user on Telegram
    Given a scheduled task runs at 08:00 and hits a "prepare" step
    When the result is placed in the inbox
    Then a push notification is sent to Telegram
    And the notification includes action buttons: [Approve] [Edit] [Reject]
    When the user taps [Approve] on Telegram
    Then the paused step resumes execution
    And the action is logged with channel="telegram"
