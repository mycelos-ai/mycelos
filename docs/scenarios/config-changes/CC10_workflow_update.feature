@config-change @workflow @versioning @blueprint-lifecycle @risk-medium
Feature: Updating an Existing Workflow Definition
  As a Mycelos user
  I want to update a workflow's steps or configuration
  So that it works better based on experience

  @step-change
  Scenario: Adding a new step to existing workflow
    Given email-summary workflow v3 is active
    When the PlannerAgent adds a "priority-classification" step
    Then a new workflow v4 is created (YAML file)
    And the Blueprint Plan shows:
      | change                                      | risk   |
      | ~ workflows.email-summary v3→v4             | MEDIUM |
      | + workflows.email-summary.steps.classify    | MEDIUM |
    And the user confirms the update
    And the old v3 YAML remains in the filesystem for reference

  @scope-expansion
  Scenario: Workflow gains new capability requirements
    Given a workflow currently requires only email.read
    When an update adds a step requiring email.send
    Then the Blueprint Plan shows:
      | change                                      | risk |
      | ~ workflows.email-summary.scope +email.send | HIGH |
    And the risk is elevated from MEDIUM to HIGH due to scope expansion
    And the user must explicitly approve the new capability

  @running-workflow-update
  Scenario: Update while workflow instance is running
    Given email-summary v3 is currently executing step 2
    When v4 is applied via Blueprint Lifecycle
    Then step 2 completes with v3 logic
    And step 3 uses v4 logic (always-latest-generation rule)
    When v4 causes step 3 to fail (schema incompatibility)
    Then the guard period detects increased error rate
    And auto-rollback to v3 may trigger
