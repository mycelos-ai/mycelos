@security @threat-model @artifact @privacy @high
Feature: Artifact Privacy Scope Bypass
  Agent attempts to access artifacts from other tasks or sessions.

  Mitigation: 5-level privacy scoping + Capability checks

  @cross-task-access
  Scenario: Agent tries to read artifacts from another task
    Given task-A produced artifact "confidential-report.pdf"
    And task-B's agent tries to access confidential-report.pdf
    Then the artifact system checks task scope
    And denies access (artifact belongs to task-A, not task-B)

  @cross-session-access
  Scenario: Agent tries to read uploads from another session
    Given session-1 has uploaded "passport-scan.jpg"
    When an agent in session-2 tries to access it
    Then session scope isolation blocks the access
    And the agent can only see artifacts from its own session

  @step-scope-violation
  Scenario: Workflow step tries to read parallel step's output
    Given step-A and step-B run in parallel
    When step-A tries to read step-B's output
    Then step scope isolation blocks the access
    And each step only sees its explicitly assigned inputs
    And outputs from previous sequential steps in the chain
