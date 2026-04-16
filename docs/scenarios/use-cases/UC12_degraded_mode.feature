@use-case @degraded-mode @resilience @llm-outage @milestone-3
Feature: System Operation During LLM Provider Outage
  As a Mycelos user
  I want the system to keep working even when the LLM is down
  So that deterministic agents and cached workflows still function

  Background:
    Given Mycelos is running with agents of various types
    And the LLM provider (Anthropic) becomes unreachable

  @deterministic-continues
  Scenario: Deterministic agents run normally during LLM outage
    Given the LLM Broker detects provider unreachable
    When a task requires the ocr-agent (deterministic, no LLM)
    Then the ocr-agent runs normally
    And produces its usual output
    And model_usage records $0.00

  @llm-agents-pause
  Scenario: LLM-based agents pause gracefully
    When a task requires the email-summary agent (Haiku)
    Then the LLM Broker attempts retry with exponential backoff (max 5)
    And if LiteLLM has fallback providers configured, tries the next provider
    When all providers fail
    Then the task pauses with a checkpoint
    And the user sees: "LLM provider not reachable. Deterministic commands still work."

  @cached-plans
  Scenario: Planner uses cached plans when LLM is unavailable
    Given the Planner cannot create new plans (needs LLM)
    When a user request matches an existing workflow in the registry
    Then the Planner uses the cached workflow directly
    And deterministic steps execute normally
    And LLM steps pause at their checkpoints

  @recovery
  Scenario: System resumes automatically when LLM recovers
    Given several tasks are paused due to LLM outage
    When the LLM Broker successfully connects again
    Then paused tasks resume from their last checkpoint
    And the user is notified of resumed tasks
