@use-case @memory @context-engine @privacy @milestone-5
Feature: Memory Management Across Agents and Sessions
  As a Mycelos user
  I want the system to remember my preferences and context
  So that agents become more personalized over time

  Background:
    Given Mycelos is running with multiple active agents
    And the memory service is initialized

  @system-memory
  Scenario: System memory stores user profile
    Given the user completed onboarding with name "Stefan"
    Then system memory contains:
      | key              | value    | readable_by    |
      | user.name        | Stefan   | all agents     |
      | user.timezone    | CET      | all agents     |
      | user.language    | de       | all agents     |
    And any agent can read system memory
    And no agent can write to system memory (only system/Creator-Agent)

  @agent-memory @isolation
  Scenario: Agent memory is isolated between agents
    Given email-agent stores "user prefers brief summaries" in agent memory
    When github-agent tries to read email-agent's memory
    Then the Security Layer blocks the access
    And the attempt is logged in the audit trail

  @shared-memory
  Scenario: Shared memory enables cross-agent knowledge
    Given email-agent discovers "Project X deadline is April 15"
    When email-agent writes to shared memory with readable_by=["github-agent"]
    Then github-agent can read "project.x.deadline = 2026-04-15"
    But calendar-agent cannot read it (not in readable_by list)

  @context-engine
  Scenario: Context Engine builds agent context within token budget
    When the Planner invokes email-agent for a task
    Then the Context Engine assembles:
      | layer              | source           | priority |
      | system memory      | user profile     | highest  |
      | agent memory       | email preferences| high     |
      | shared memory      | relevant entries | medium   |
      | session history    | last N messages  | medium   |
      | top-10 workflows   | workflow registry| low      |
    And checks total tokens against model budget
    When the context exceeds 80% of token limit
    Then older session messages are summarized via Haiku
    And a compact_summary record is appended to the JSONL

  @privacy @user-control
  Scenario: User inspects and deletes memory entries
    When the user runs "mycelos memory list --scope agent --agent email-agent"
    Then all memory entries for email-agent are displayed
    When the user runs "mycelos memory delete user.timezone"
    Then the entry is removed from system memory
    When the user runs "mycelos memory purge"
    Then ALL memory entries are deleted after confirmation
