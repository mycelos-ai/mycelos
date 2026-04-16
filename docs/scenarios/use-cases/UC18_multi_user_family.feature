@use-case @multi-user @privacy @isolation @milestone-future
Feature: Multi-User Family Setup
  As a family sharing one Mycelos instance
  I want each family member to have their own agents and data
  So that our personal information stays private

  Background:
    Given Mycelos is configured for multi-user operation
    And two users exist: "stefan" and "anna"

  @user-isolation
  Scenario: Users cannot see each other's data
    Given stefan has an email-agent with agent memory
    And anna has a calendar-agent with agent memory
    When stefan's email-agent tries to access anna's memory
    Then the Security Layer blocks the access (user_id mismatch)
    And stefan's agents can only see stefan's artifacts, sessions, and workflows

  @separate-agents
  Scenario: Each user has their own agent ecosystem
    Given stefan has configured 5 agents for his work
    And anna has configured 3 agents for her tasks
    Then stefan's Planner only sees stefan's agents
    And anna's Planner only sees anna's agents
    And agent reputation is tracked per-user

  @shared-resources
  Scenario: LLM tokens are shared but tracked separately
    Given both users share the same LLM Broker
    Then model_usage tracks costs per user_id
    And each user can see only their own usage
    And budget limits can be set per user
