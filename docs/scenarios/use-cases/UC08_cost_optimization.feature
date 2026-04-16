@use-case @cost @escalation @reputation @milestone-3
Feature: Cost-Optimized Execution with Model Escalation
  As a cost-conscious Mycelos user
  I want the system to use the cheapest effective method
  So that I don't waste money on tasks that don't need expensive models

  Background:
    Given Mycelos is running with multiple agents of different types
    And model usage is tracked in the model_usage table

  @deterministic-first
  Scenario: Deterministic agent handles task for free
    Given a log-parser agent (deterministic, no LLM)
    When the user asks "Parse today's error logs"
    Then the Planner assigns the task to log-parser agent
    And the agent runs with zero LLM cost
    And the result is evaluated with deterministic checks only
    And model_usage shows $0.00 for this task

  @escalation-chain
  Scenario: Task escalates from Haiku to Sonnet to Opus
    Given a research task is assigned to research-agent (Haiku)
    When the agent's first attempt is evaluated as insufficient
    Then the Planner decides: retry with same model or escalate
    When retry with Haiku also fails evaluation
    Then the system escalates to Sonnet
    And the Sonnet attempt succeeds
    And the cost breakdown shows:
      | attempt | model  | cost  | result      |
      | 1       | haiku  | $0.01 | insufficient|
      | 2       | haiku  | $0.01 | insufficient|
      | 3       | sonnet | $0.05 | success     |
    And the agent's reputation decreases (needed escalation)

  @budget-tracking
  Scenario: Budget limit prevents runaway costs
    Given a task has a budget of $0.50
    When the task has already spent $0.45 on 3 attempts
    And the next attempt would cost approximately $0.10
    Then the system returns a partial result
    And explains to the user what was accomplished and what remains
    And the task status is set to "partial"

  @evaluation-cost
  Scenario: Evaluation uses cheapest effective method
    Given an agent produces structured JSON output
    Then the EvaluatorAgent first runs deterministic checks:
      | check           | cost | result |
      | exit code == 0  | $0   | pass   |
      | valid JSON      | $0   | pass   |
      | required fields | $0   | pass   |
      | field types     | $0   | pass   |
    And no LLM evaluation is needed (all deterministic checks passed)
    When an agent produces free-text output
    Then deterministic checks run first (non-empty, length, forbidden patterns)
    And only if deterministic checks pass, Haiku evaluates content quality
