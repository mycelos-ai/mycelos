@security @threat-model @multi-user @isolation @critical
Feature: Multi-User Data Isolation
  One user attempts to access another user's data, agents, or credentials.

  Mitigation: user_id scoping on every query + Row-level isolation

  @cross-user-data
  Scenario: User A cannot access User B's data
    Given user "stefan" and user "anna" share a Mycelos instance
    When stefan's agent queries the database
    Then every query includes WHERE user_id = 'stefan'
    And stefan cannot see anna's:
      | data type          | isolation method    |
      | memory entries     | user_id column      |
      | agents             | user_id column      |
      | workflows          | user_id column      |
      | artifacts          | user_id column      |
      | sessions           | user_id column      |
      | credentials        | user_id column      |
      | audit events       | user_id column      |
      | config generations | user_id column      |

  @cross-user-credentials
  Scenario: User A cannot access User B's credentials
    Given anna has configured a GitHub connector
    When stefan's agent requests GitHub capabilities
    Then the Credential Proxy checks user_id
    And returns no credentials (stefan has no GitHub connector)
    And the attempt is logged

  @shared-llm-budget
  Scenario: Users share LLM but costs are tracked separately
    Given both users use the same LLM Broker
    Then model_usage tracks user_id per request
    And budget limits can be set per user
    And one user cannot exhaust another user's budget
