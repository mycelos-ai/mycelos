@use-case @agent-lifecycle @evolution @reputation @milestone-3
Feature: Agent Evolution - From Simple to Sophisticated
  As a Mycelos user
  I want my agents to improve over time based on performance
  So that the system becomes more effective without manual intervention

  Background:
    Given Mycelos is running with a basic email-summary agent (v1)
    And the agent uses Haiku model tier
    And the agent has been active for 4 weeks

  @reputation @scoring
  Scenario: Agent reputation reflects real performance
    Given the email-summary agent has completed 50 runs with these results:
      | outcome      | count | impact on reputation    |
      | first-try OK | 35    | high positive           |
      | retry needed | 10    | moderate positive       |
      | escalated    | 3     | low positive            |
      | failed       | 2     | negative                |
    Then the agent reputation score is approximately 0.72
    And the workflow score reflects this agent's contribution

  @improvement-trigger
  Scenario: Score drop triggers improvement analysis
    Given the agent's score drops below 0.5 after repeated failures
    Then the EvaluatorAgent automatically analyzes the last 10 failed runs
    And produces a diagnosis:
      | finding                          | category        |
      | HTML emails not parsed correctly | agent_problem   |
      | New email format from provider   | agent_problem   |
    And the diagnosis is placed in the user's inbox

  @creator-agent @agent-update
  Scenario: Creator-Agent improves the agent
    Given the EvaluatorAgent diagnosed an agent problem
    When the Creator-Agent is triggered to improve email-summary agent
    Then the Creator-Agent generates an updated agent (v2)
    And existing tests from v1 serve as regression guards
    And new tests are added for HTML email parsing
    And all tests (old + new) must pass
    And the AuditorAgent reviews the changes
    And the update goes through Blueprint Lifecycle:
      | phase   | action                                |
      | resolve | New agent code + tests as ChangeSpec  |
      | verify  | SHA-256 check, no duplicate           |
      | plan    | Risk: HIGH (agent code change)        |
      | apply   | User confirms, new generation created |
      | status  | Guard period monitors error rate      |

  @deprecation @optimizer
  Scenario: Optimizer Agent suggests cleanup
    Given a "log-parser" agent has not been used for 30 days
    And a "research-agent" has reputation below 0.2 after 15 runs
    When the OptimizerAgent runs its periodic analysis
    Then it suggests:
      | agent          | suggestion                              |
      | log-parser     | Archive (unused for 30 days)            |
      | research-agent | Overhaul or archive (12% success rate)  |
    And the suggestions require user confirmation
    And no agent is archived without explicit approval

  @reuse-before-create
  Scenario: Creator-Agent extends existing agent instead of creating new one
    Given the email-summary agent is active
    When the user says "I want email priority alerts for urgent messages"
    Then the Creator-Agent checks existing agents
    And finds email-summary can be extended
    And proposes: "Your email agent can do this with an additional capability"
    And does NOT create a new agent (Reuse > Extend > Create)
