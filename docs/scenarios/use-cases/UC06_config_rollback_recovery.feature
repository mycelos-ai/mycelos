@use-case @rollback @config @guard-period @milestone-2
Feature: Config Rollback and Recovery After Bad Change
  As a Mycelos user
  I want automatic recovery when a config change breaks things
  So that the system stays reliable even when changes go wrong

  Background:
    Given Mycelos is running with config generation 5
    And 3 agents are active and healthy
    And the system has been stable for 7 days

  @blueprint-lifecycle @happy-path
  Scenario: Successful config change through Blueprint Lifecycle
    When the user changes the default LLM model from Haiku to Sonnet
    Then the Blueprint Lifecycle runs:
      | phase   | result                                          |
      | resolve | Full target config snapshot created              |
      | verify  | SHA-256 computed, no duplicate found             |
      | plan    | Diff: llm.default_model haiku→sonnet, Risk: MEDIUM |
      | apply   | Generation 6 created, pointer swapped atomically|
      | status  | Guard period started (5 min for MEDIUM risk)    |
    And during the guard period all agents use the new model
    And error rates are monitored
    And after 5 minutes with no issues, generation 6 is marked "confirmed"

  @auto-rollback @guard-period
  Scenario: Auto-rollback when new config causes failures
    Given the user registers a new agent via Blueprint Lifecycle
    And generation 7 is applied
    When the new agent causes errors in 45% of its executions
    And the guard period (10 min for HIGH risk) detects error rate > 40%
    Then the system automatically rolls back to generation 6
    And the rollback itself goes through: resolve → verify → apply → status
    And the user is notified: "Auto-rollback to Gen 6 due to high error rate"
    And the audit trail records the entire sequence

  @manual-rollback
  Scenario: User manually rolls back to a specific generation
    Given the system is at generation 10
    When the user runs "mycelos config rollback 7"
    Then the system loads generation 7's full snapshot
    And the verify phase checks the snapshot hash
    And the plan phase is skipped (user already decided)
    And generation 7 becomes the active generation
    And all agents resolve against generation 7's config
    And derived state created in gen 8-10 is marked as orphaned

  @state-boundaries
  Scenario: Rollback respects state boundaries
    Given generation 8 added an email agent that sent 5 emails
    When the system rolls back to generation 7
    Then config state is fully rolled back (agent registration removed)
    And derived state (memory entries from gen 8) is archived, not deleted
    And external state is logged: "5 emails sent under Gen 8 cannot be reversed"
    And the audit trail documents all three state classes

  @deduplication
  Scenario: Content-addressed deduplication avoids redundant generations
    Given generation 5 has config hash "abc123"
    When an agent-generated change produces the exact same config
    Then the verify phase detects hash "abc123" already exists
    And no new generation is created
    And the existing generation 5 is reactivated
    And the operation is logged as "deduplicated"
