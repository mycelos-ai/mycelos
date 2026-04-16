@use-case @scheduler @huey @background-tasks @milestone-4
Feature: Scheduled Task Management with Pause-Points
  As a Mycelos user
  I want scheduled tasks that run autonomously with safety controls
  So that routine work happens without my constant attention

  Background:
    Given Mycelos gateway is running via "mycelos serve"
    And the Huey consumer is active with SqliteHuey backend

  @cron-job @permission-freeze
  Scenario: Creating a new scheduled task with frozen permissions
    When the user creates a daily email summary scheduled for "0 8 * * 1-5"
    Then the system runs permission discovery via dry-run
    And the user sets permissions:
      | capability | policy  | reason                              |
      | email.read | always  | Safe read-only operation            |
      | email.send | prepare | User wants to review before sending |
    And "confirm" policy is NOT allowed for scheduled tasks
    And the permission set is frozen at creation time
    And the schedule is added to the config generation via Blueprint Lifecycle
    And the Blueprint Plan shows Risk: HIGH for new scheduled task

  @pause-point @prepare-policy
  Scenario: Scheduled task pauses at prepare-policy step
    Given the email summary cron job runs at 08:00
    When step "fetch-emails" (always) completes successfully
    And step "summarize" (always) completes successfully
    And step "send-draft" (prepare) is reached
    Then the workflow pauses at the send-draft step
    And the draft is placed in the user's inbox
    And the user is notified via their active channel
    And a 24-hour timeout starts
    When the user approves the draft within 24 hours
    Then the send-draft step executes
    When the user does NOT respond within 24 hours
    Then the step is skipped and logged as "timeout"

  @circuit-breaker
  Scenario: Circuit breaker disables failing scheduled task
    Given a scheduled task has failed 3 times consecutively
    Then the circuit breaker activates
    And the task is automatically disabled
    And the user is notified with error details
    And re-activation requires manual "mycelos schedule enable <name>"

  @concurrency @skip-mode
  Scenario: Overlapping schedule runs are handled
    Given task "data-sync" runs every 30 minutes with concurrency_mode="skip"
    When the 08:00 run is still executing at 08:30
    Then the 08:30 trigger is skipped
    And a run record with status "skipped" is logged
    And no duplicate sandbox is created

  @retry @backoff
  Scenario: Transient failures trigger exponential backoff retry
    Given a scheduled task encounters a network timeout
    Then the system retries with exponential backoff:
      | attempt | delay  |
      | 1       | 30s    |
      | 2       | 2min   |
      | 3       | 8min   |
      | 4       | 30min  |
    And after 4 failed retries the task is marked "failed"
    And structural errors (agent bugs) are NOT retried
