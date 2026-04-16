@security @threat-model @dos @replan @high
Feature: Replan Storm After Config Update
  A config update causes many running workflows to fail simultaneously,
  triggering a cascade of replans that could overwhelm the system.

  Mitigation: Circuit breaker + Max concurrent replans + Cooldown + Pin-on-failure

  @cascade-detection
  Scenario: System detects and handles replan cascade
    Given 10 workflows are running
    When a config update causes 6 workflows to fail within 2 minutes
    Then cascade detection triggers (threshold: 5 failures / 2 minutes)
    And the guard period is immediately set to CRITICAL
    And auto-rollback is triggered
    And only 3 workflows are allowed to replan concurrently
    And others are paused until rollback completes

  @pin-on-failure
  Scenario: Workflow pinned after repeated generation-related failures
    Given workflow-A fails twice due to generation changes
    Then workflow-A is pinned to the last working generation
    And continues executing with the pinned generation
    And the user is notified to investigate
