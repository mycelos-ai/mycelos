@config-change @policy @permissions @blueprint-lifecycle @risk-high
Feature: Modifying Agent Permissions and Policies
  As a Mycelos user
  I want to change what an agent is allowed to do
  So that I can grant or restrict capabilities as needed

  Policy changes are HIGH risk when expanding capabilities
  and LOW risk when restricting them.

  @escalate-permissions
  Scenario: Granting additional capability to an agent
    Given email-agent currently has: email.read=always, email.send=never
    When the user changes email.send from "never" to "confirm"
    Then the Blueprint Plan shows:
      | change                          | risk  |
      | ~ policies.email.send never→confirm | HIGH |
    And the user must confirm this capability expansion
    And the guard period is 10 minutes

  @restrict-permissions
  Scenario: Restricting an agent's capability
    Given research-agent currently has: web.fetch=always
    When the user changes web.fetch from "always" to "confirm"
    Then the Blueprint Plan shows:
      | change                             | risk |
      | ~ policies.research.web.fetch always→confirm | LOW |
    And the change may auto-approve (LOW risk)
    And the guard period is 2 minutes

  @trust-escalation
  Scenario: Trust escalation through repeated approvals
    Given email-agent has email.send=confirm
    When the user has confirmed email.send 10 times for this agent
    Then the system suggests: "You've approved email.send 10 times. Make it 'always'?"
    And the user can accept or decline
    And the suggestion is logged for transparency

  @rollback-policy
  Scenario: Rolling back a policy change
    Given email-agent was granted email.send=always in Gen 8
    When the user runs "mycelos config rollback 7"
    Then email-agent's policy reverts to email.send=never
    And any pending email sends from the agent are blocked
    And the rollback is logged in the audit trail
