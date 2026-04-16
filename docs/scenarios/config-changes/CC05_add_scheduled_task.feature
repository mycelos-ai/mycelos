@config-change @scheduler @cron @blueprint-lifecycle @risk-high
Feature: Adding a New Scheduled Task
  As a Mycelos user
  I want to add a new cron job for automated task execution
  So that routine tasks run without my intervention

  New scheduled tasks are HIGH risk because they execute
  autonomously without user presence.

  @creation-flow
  Scenario: Creating a scheduled task via Blueprint Lifecycle
    When the user runs:
      """
      mycelos schedule add --name daily-report --cron "0 8 * * 1-5" --workflow report-gen
      """
    Then the Blueprint Plan shows:
      """
      Blueprint Plan (Gen 12 → Gen 13):
        + scheduled_tasks.daily-report    [NEW]     Risk: HIGH
          Cron: 0 8 * * 1-5 (Mon-Fri 08:00)
          Workflow: report-gen (v2)
          Permissions: email.read=always, email.send=prepare

        1 addition, 0 removals, 0 changes
        Risk level: HIGH — User confirmation required

        Proceed? [y/N]
      """

  @permission-freeze
  Scenario: Scheduled task permissions are frozen at creation
    Given the user sets permissions during task creation:
      | capability   | policy  |
      | email.read   | always  |
      | email.send   | prepare |
    Then these permissions are frozen in the config snapshot
    And at runtime, NO new permissions can be requested
    And "confirm" policy is NOT allowed (no user present)

  @rollback-schedule
  Scenario: Rolling back removes the scheduled task
    Given daily-report was added in Gen 13
    When the user rolls back to Gen 12
    Then the schedule is removed from the active config
    And the Huey consumer unregisters the cron job
    And already completed runs remain in scheduled_task_runs (historical facts)
    And the next trigger at 08:00 will NOT fire
