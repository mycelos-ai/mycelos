@use-case @data-analysis @csv @deterministic @milestone-4
Feature: Data Analysis Pipeline with Mixed Agent Types
  As a data analyst
  I want to process CSV data using a combination of deterministic and LLM agents
  So that I get both precise calculations and natural language insights

  Background:
    Given a filesystem connector with read access to "~/data"
    And deterministic agents for data processing are available

  @deterministic @cost-zero
  Scenario: Deterministic agent processes CSV for free
    Given a "csv-parser" deterministic agent (no LLM needed)
    When the user says "Analyze sales data from ~/data/sales-2026.csv"
    Then the csv-parser agent reads the file via filesystem connector
    And performs calculations: totals, averages, trends, outliers
    And produces a structured JSON artifact with statistics
    And the cost is $0.00

  @mixed-pipeline
  Scenario: LLM agent adds narrative to deterministic results
    Given the csv-parser produced structured statistics
    When the insight-agent (Haiku) receives the statistics
    Then it generates a natural language summary with key findings
    And the EvaluatorAgent checks:
      | check              | method          |
      | numbers match data | deterministic   |
      | format correct     | deterministic   |
      | insight quality    | Haiku evaluator |
    And the total pipeline cost is minimal (only insight step uses LLM)

  @artifact-chain
  Scenario: Artifact chain tracks data provenance
    Given the pipeline produces artifacts:
      | artifact                | derived_from       | type        |
      | sales-2026.csv          | (user upload)      | raw         |
      | sales-statistics.json   | sales-2026.csv     | extraction  |
      | sales-insights.md       | sales-statistics   | summary     |
    Then each artifact has content_hash for integrity
    And the full derivation chain is queryable
