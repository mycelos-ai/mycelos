@config-change @agent @registration @blueprint-lifecycle @risk-high
Feature: Registering a New Agent in the System
  As a Mycelos user
  I want to register a new agent created by the Creator-Agent
  So that it can start handling tasks

  Agent registration is always HIGH risk because it introduces new
  executable code and capability requirements.

  Background:
    Given the Creator-Agent has generated a "slack-notifier" agent
    And all tests pass and AuditorAgent has approved

  @blueprint-lifecycle
  Scenario: Agent registration through full lifecycle
    When the Creator-Agent submits the agent for registration
    Then the Blueprint Lifecycle runs:
      | phase   | details                                           |
      | resolve | Config + new agent entry + policy entry            |
      | verify  | Hash computed, no duplicate                        |
      | plan    | + agents.slack-notifier [NEW] Risk: HIGH          |
      |         | + policies.slack-notifier [NEW] Risk: HIGH         |
      | apply   | User confirms, Gen N+1 created                    |
      | status  | Guard period 10 min, monitors new agent's errors  |

  @security-implications
  Scenario: Security Layer enforces new agent's policy
    Given the slack-notifier agent is registered with policy:
      | capability    | decision |
      | slack.send    | confirm  |
      | slack.read    | always   |
      | web.fetch     | never    |
    When the agent tries to use slack.read
    Then the Security Layer allows it immediately
    When the agent tries to use slack.send
    Then the Security Layer pauses and asks the user
    When the agent tries to use web.fetch
    Then the Security Layer blocks and logs the attempt

  @human-confirmation
  Scenario: Agent registration ALWAYS requires human confirmation
    Given the Creator-Agent wants to register a new agent
    Then the system ALWAYS asks for user confirmation
    And this requirement is NOT learnable through trust escalation
    And even if the user has approved 100 agents before
    And even if the agent is marked as low-risk
    Then explicit user confirmation is still required
