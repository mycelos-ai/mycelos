@config-change @concurrency @scheduler @risk-medium
Feature: Changing Scheduled Task Concurrency Settings
  Scenario: Switching from skip to queue mode
    Given daily-report task uses concurrency_mode="skip"
    When the user changes to concurrency_mode="queue"
    Then the Blueprint Plan shows risk MEDIUM
    And missed triggers will now be queued instead of skipped
    And Huey enqueues waiting tasks

  Scenario: Enabling parallel execution
    When the user sets concurrency_mode="parallel" for a stateless agent
    Then the system requires explicit confirmation
    And warns: "Parallel execution creates multiple sandbox instances"
    And the Blueprint Plan shows risk MEDIUM

  Scenario: Adjusting circuit breaker threshold
    When the user changes circuit breaker from 3 to 5 consecutive failures
    Then the Blueprint Plan shows risk MEDIUM
    And failing tasks get more retry attempts before being disabled
