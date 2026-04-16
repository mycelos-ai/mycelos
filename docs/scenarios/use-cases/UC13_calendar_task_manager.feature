@use-case @calendar @task-management @multi-connector @milestone-5
Feature: Personal Calendar and Task Management Assistant
  As a busy professional
  I want an agent that manages my calendar and tasks
  So that I never miss meetings and stay organized

  Background:
    Given Mycelos is running with email and calendar connectors configured
    And a calendar-agent is registered with capabilities: calendar.events.list, calendar.events.create

  Scenario: Morning briefing combines email and calendar
    Given a workflow combining email-summary and calendar-agent
    When the morning cron job runs at 07:30
    Then the email-summary agent summarizes unread emails
    And the calendar-agent fetches today's events
    And the summary-agent combines both into a daily briefing:
      """
      Good morning Stefan! Here's your day:

      📅 3 meetings today (first at 09:00: Sprint Planning)
      📧 8 unread emails (2 marked urgent)
      ⚡ Action needed: Reply to client proposal (deadline today)
      """

  Scenario: Agent creates calendar event from email context
    Given the email-agent detects a meeting request in an email
    When the agent proposes creating a calendar event
    And the step policy is "confirm"
    Then the system shows the proposed event details
    And waits for user confirmation before creating it
    And the Calendar MCP connector handles the actual API call

  Scenario: Cross-agent knowledge sharing for scheduling
    Given email-agent discovers "Team dinner Friday at 19:00"
    When it writes to shared memory: "event.team_dinner = Friday 19:00"
    Then calendar-agent reads this in its next run
    And proposes adding it to the calendar
