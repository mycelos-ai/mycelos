@use-case @email @onboarding @milestone-1
Feature: Personal Email Assistant - From Zero to Daily Summaries
  As a new Mycelos user
  I want to set up a personal email assistant
  So that I get daily summaries of my inbox without manually checking email

  Background:
    Given the system is freshly installed via "pip install mycelos"
    And no agents or connectors are configured

  @init @happy-path
  Scenario: Complete onboarding flow for email assistant
    When the user runs "mycelos init"
    Then the system creates the SQLite database with initial schema
    And the system creates the initial config generation (Gen 1)
    And the system prompts for a LLM provider selection
    When the user selects "Anthropic" as the LLM provider
    And the user provides an API key
    Then the API key is encrypted via the Credential Proxy
    And the API key is never stored in plaintext
    And the system confirms "Credential stored securely in Credential Proxy"

  @connector-setup
  Scenario: Setting up the email connector before first chat
    Given the system has been initialized with "mycelos init"
    When the user runs "mycelos connector setup email"
    Then the system asks for IMAP server details
    When the user provides "imap.gmail.com" and selects OAuth
    Then the system opens a browser for Google OAuth flow
    And the OAuth token is stored in the Credential Proxy
    And the MCP tool schemas are registered as capabilities
    And the Security Layer knows about "email.read" and "email.send" capabilities
    And a new config generation (Gen 2) is created via Blueprint Lifecycle

  @creator-agent @agent-creation
  Scenario: Creator-Agent builds the email summary agent
    Given the email connector is configured
    And the user starts "mycelos chat"
    When the user says "I want my unread emails summarized every morning"
    Then the Creator-Agent analyzes the request
    And the Creator-Agent checks for existing agents that could handle this
    And finding none, proposes creating an "email-summary" agent
    And the Creator-Agent generates agent code
    And the Creator-Agent generates tests including:
      | test_type    | description                              |
      | unit         | Agent can parse email headers             |
      | unit         | Agent produces markdown summary           |
      | integration  | Agent connects to email via MCP connector |
      | error        | Agent handles empty inbox gracefully      |
      | error        | Agent handles connector timeout           |
      | boundary     | Agent handles 1000+ emails with truncation|
    And all tests run in the sandbox
    And the AuditorAgent reviews the generated code
    And the AuditorAgent checks for unauthorized network calls
    And the AuditorAgent checks for filesystem access outside sandbox
    And the system asks the user to confirm agent registration

  @policy @permissions
  Scenario: User sets step-level permissions during dry-run
    Given the email-summary agent has been created and tested
    When the system runs the mandatory dry-run
    Then the dry-run discovers required permissions:
      | capability   | discovered_via           |
      | email.read   | Step: fetch unread emails |
      | email.send   | Step: send summary email  |
    And the system asks the user for each permission:
      | capability | options                              |
      | email.read | always / confirm / prepare / never   |
      | email.send | always / confirm / prepare / never   |
    When the user sets email.read to "always"
    And the user sets email.send to "prepare"
    Then the policies are stored in the config generation
    And a new config generation (Gen 3) is created

  @scheduled-task @workflow
  Scenario: Setting up the daily morning schedule
    Given the email-summary agent is registered and active
    When the user says "Run this every weekday at 8 AM"
    Then the Creator-Agent creates a workflow YAML definition
    And the workflow includes steps for fetch, summarize, and optional send
    And the system creates a scheduled task with cron "0 8 * * 1-5"
    And the permission set is frozen at creation time
    And "confirm" policy is converted to "prepare" for scheduled execution
    And a new config generation is created via Blueprint Lifecycle
    And the Blueprint Plan shows risk level HIGH for new scheduled task
    And the user confirms the schedule

  @execution @daily-run
  Scenario: First real execution of morning email summary
    Given the scheduled task "email-morning-summary" is active
    And the gateway is running via "mycelos serve"
    When the cron trigger fires at 08:00
    Then the system creates a new task in the task lifecycle
    And the Security Layer issues a capability token for email.read
    And the token has TTL of 30 minutes and max 100 requests
    And the email-summary agent runs in a sandboxed process
    And the agent reads unread emails via the MCP email connector
    And the Credential Proxy injects auth headers (agent never sees credentials)
    And the agent produces a markdown summary
    And the EvaluatorAgent checks the output:
      | check              | expected                          |
      | format             | markdown                          |
      | contains           | sender, subject, summary per email|
      | max_length         | 2000 characters                   |
      | must_not_contain   | API keys, passwords, tokens       |
    And the summary is placed in the user's inbox
    And the workflow score is updated based on success

  @inbox-briefing
  Scenario: User sees results in next chat session
    Given the morning summary ran successfully at 08:00
    When the user runs "mycelos chat" at 09:30
    Then the inbox briefing shows:
      | status    | task                    | detail                    |
      | completed | email-morning-summary   | 12 emails summarized      |
    And the user can review the summary inline
    And the user can approve or reject any "prepare" step outputs

  @error-recovery
  Scenario: Handling email connector failure gracefully
    Given the scheduled task "email-morning-summary" is active
    When the cron trigger fires but the email server is unreachable
    Then the system retries with exponential backoff: 30s, 2min, 8min, 30min
    And after 4 failed retries the task status is set to "failed"
    And the error is logged in scheduled_task_runs
    And the user is notified via their active channel
    And the agent's reputation score decreases slightly
    And the workflow score is updated to reflect the failure

  @reputation @improvement
  Scenario: Agent reputation triggers improvement cycle
    Given the email-summary agent has run 15 times
    And 6 of those runs failed (reputation drops below 0.5)
    Then the EvaluatorAgent analyzes the failure pattern
    And diagnoses whether it's a workflow problem or agent problem
    When the diagnosis is "agent problem - email parsing fails on HTML emails"
    Then the Creator-Agent is triggered to improve the agent
    And the improved agent goes through the full creation pipeline
    And existing tests serve as regression guards
    And the user must confirm the updated agent registration
