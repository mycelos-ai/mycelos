@use-case @testing @mocking @llm @developer-experience @milestone-3
Feature: LLM Agent Testing and Mocking
  As a developer working on Mycelos agents
  I want to test LLM-based agents without real API calls
  So that tests are fast, free, deterministic, and CI-friendly

  Three testing levels:
  1. MockLLMBroker — pattern-match input → deterministic output (unit tests)
  2. pytest-recording — record real LLM responses, replay in CI (integration)
  3. Property assertions — test output structure, not exact content (quality)

  Background:
    Given the Mycelos test suite is running
    And no real LLM API keys are required for unit tests

  # ── Level 1: MockLLMBroker ──

  @mock @unit-test
  Scenario: Agent logic tested with MockLLMBroker
    Given a MockLLMBroker configured with:
      | input pattern         | response                           |
      | .*email.*summar.*     | {"emails": 5, "summary": "..."}    |
      | .*create.*workflow.*  | {"action": "create", "steps": [...]}|
    When the PlannerAgent receives "Summarize my emails"
    Then the MockLLMBroker matches the first pattern
    And returns the configured response (no API call)
    And the test verifies the PlannerAgent's decision logic
    And the test runs in < 10ms

  @mock @deterministic
  Scenario: Same input always produces same mock output
    Given a MockLLMBroker with a fixed response for ".*"
    When the same test runs 100 times
    Then the output is identical every time
    And no test flakiness occurs

  @mock @call-logging
  Scenario: MockLLMBroker logs all calls for assertion
    Given a MockLLMBroker is used in a test
    When an agent makes 3 LLM calls
    Then the broker's call_log contains 3 entries
    And each entry has: messages, model, tools
    And the test can assert which tools were offered to the LLM

  # ── Level 2: Recorded Responses ──

  @recording @integration
  Scenario: Record real LLM response for integration tests
    Given a developer runs tests with --record-mode=once
    And ANTHROPIC_API_KEY is set in the environment
    When the test makes a real LLM call
    Then the response is recorded as a YAML cassette in tests/cassettes/
    And the cassette is committed to the repository
    And subsequent test runs replay the cassette (no API call)
    And CI runs with record_mode=none (never makes real calls)

  @recording @filtered
  Scenario: Recorded cassettes have credentials stripped
    Given a test records an LLM response
    Then the cassette YAML does NOT contain:
      | filtered                  |
      | API keys                  |
      | Authorization headers     |
      | MAICEL_MASTER_KEY         |
    And the cassette can be safely committed to a public repo

  # ── Level 3: Property Assertions ──

  @property @output-structure
  Scenario: Test asserts output structure, not exact content
    Given the EvaluatorAgent processes a task result
    Then the test asserts properties:
      | property                    | assertion                    |
      | result is dict              | isinstance(result, dict)     |
      | has score                   | "score" in result            |
      | score is float 0-1          | 0.0 <= result["score"] <= 1.0|
      | has reasoning               | len(result["reasoning"]) > 0 |
    And the test does NOT assert exact text content
    And the test passes regardless of LLM model or version

  @property @robustness
  Scenario: Agent never crashes on arbitrary input (Hypothesis)
    Given a Hypothesis test generates random user input
    When the input is passed to the CreatorAgent
    Then the agent ALWAYS returns a valid response (never raises)
    And the response is either a success or a graceful error
    And this holds for 100+ random inputs

  @property @format-compliance
  Scenario: Agent output always matches AgentOutput contract
    Given any agent is tested with any input
    Then the output ALWAYS has:
      | field      | type        | required |
      | success    | bool        | yes      |
      | result     | Any         | yes      |
      | artifacts  | list[str]   | yes      |
      | metadata   | dict        | yes      |
      | error      | str or None | yes      |
