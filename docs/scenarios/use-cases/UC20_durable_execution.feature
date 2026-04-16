@use-case @durable-execution @crash-recovery @checkpoint @milestone-4
Feature: Durable Execution - Surviving Process Crashes
  As a Mycelos user running long workflows
  I want workflows to survive system crashes
  So that work is not lost if the process restarts

  Background:
    Given a multi-step workflow is running
    And workflow events are logged to SQLite (workflow_events table)

  @crash-recovery
  Scenario: Workflow resumes after process crash
    Given a 5-step workflow is running
    And steps 1-3 have completed (checkpoints saved to workflow_events)
    When the mycelos process crashes unexpectedly
    And the user restarts "mycelos serve"
    Then the Scheduler finds workflows with status "running"
    And for each running workflow, finds the last "completed" event
    And resumes the workflow from step 4 (the first incomplete step)
    And steps 1-3 are NOT re-executed
    And the user is notified: "Resumed workflow from step 4 after crash"

  @checkpoint-data
  Scenario: Checkpoints preserve intermediate results
    Given step 2 produced an artifact "intermediate-results.json"
    When the checkpoint for step 2 is saved
    Then the workflow_events record contains:
      | field       | value                               |
      | step_id     | step-2                              |
      | event_type  | completed                           |
      | checkpoint  | JSON with artifact ID reference     |
    And step 3 can access step 2's artifact via the checkpoint

  @partial-result
  Scenario: Partial results returned when workflow cannot complete
    Given steps 1-3 completed successfully
    And step 4 fails permanently (structural error, no retry helps)
    And budget is exhausted
    Then the system returns partial results from steps 1-3
    And explains to the user: "Completed 3 of 5 steps. Step 4 failed because..."
    And the task status is set to "partial"
    And the partial artifacts are accessible to the user
