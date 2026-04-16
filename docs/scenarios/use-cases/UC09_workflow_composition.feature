@use-case @workflow @multi-agent @composition @milestone-4
Feature: Multi-Agent Workflow Composition
  As a Mycelos user with multiple agents
  I want to combine agents into workflows
  So that complex tasks are automated end-to-end

  Background:
    Given the following agents are registered:
      | agent_id          | type          | capabilities                |
      | email-reader      | light_model   | email.read                  |
      | github-agent      | light_model   | github.issue.list, github.pr.list |
      | summary-agent     | light_model   | (internal, no external caps) |

  @workflow-creation
  Scenario: Creator-Agent builds a cross-agent workflow
    When the user says "When an email mentions a GitHub issue, show me the issue status"
    Then the Creator-Agent creates a workflow YAML:
      | step_id         | agent          | action                         | policy  |
      | scan-emails     | email-reader   | Find emails mentioning #issues | always  |
      | fetch-issues    | github-agent   | Get issue status from GitHub   | always  |
      | combine-report  | summary-agent  | Create combined report         | always  |
    And the workflow defines input/output relationships between steps
    And the mandatory dry-run validates all connector access
    And the workflow is registered in the workflow_registry

  @checkpoint @error-recovery
  Scenario: Workflow resumes from checkpoint after failure
    Given the cross-agent workflow starts
    And step "scan-emails" completes successfully (checkpoint saved)
    And step "fetch-issues" fails due to GitHub API rate limit
    Then the Planner evaluates recovery strategies:
      | strategy              | applicable | reason                      |
      | retry at step         | yes        | transient error (rate limit) |
      | replan from checkpoint| no         | not a structural error       |
      | partial result        | fallback   | if retries exhausted         |
    And the system retries "fetch-issues" after backoff
    And uses the checkpoint from "scan-emails" (no re-execution)

  @workflow-reuse @scoring
  Scenario: Successful workflow is reused for similar requests
    Given the email-github workflow has been run 5 times with score 0.85
    When the user asks "Check if any emails today reference our GitHub PRs"
    Then the Planner searches the workflow registry:
      | search_stage | method              | result                    |
      | stage_1      | Top-10 context      | email-github found        |
    And the existing workflow is reused (source: "reused")
    And no new plan is created
    And the workflow score increases after successful execution

  @workflow-adaptation
  Scenario: Existing workflow is adapted for new requirement
    Given the email-github workflow exists
    When the user asks "Same thing but also check Slack messages for issue references"
    Then the Planner finds the email-github workflow as a close match
    And adapts it by adding a "scan-slack" step (source: "adapted")
    And the adapted workflow gets a new version number
    And goes through dry-run and Blueprint Lifecycle
