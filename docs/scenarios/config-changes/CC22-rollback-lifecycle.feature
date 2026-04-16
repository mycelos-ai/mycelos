Feature: Config Generation Rollback
  Als Mycelos-Benutzer moechte ich jederzeit zu einem frueheren Systemzustand
  zurueckkehren koennen, damit ich riskante Aenderungen gefahrlos ausprobieren kann.

  Background:
    Given a fresh Mycelos installation with Generation 1
    And the Object Store is initialized

  Scenario: Rollback restores connectors
    Given I set up connector "web-search-brave" with capabilities ["search.web.brave"]
    And a new Generation 2 is created
    When I rollback to Generation 1
    Then connector "web-search-brave" should not exist in the connectors table
    And capability "search.web.brave" should not exist in connector_capabilities
    And Generation 1 should be active

  Scenario: Rollback restores agent capabilities
    Given I register agent "news-agent" with capabilities ["search.web", "search.news"]
    And a new Generation 2 is created
    When I add capability "http.get" to agent "news-agent"
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then agent "news-agent" should have capabilities ["search.web", "search.news"]
    And agent "news-agent" should NOT have capability "http.get"

  Scenario: Rollback restores agent code via Object Store hashes
    Given I register agent "news-agent" with code hash "abc123"
    And a new Generation 2 is created
    When I update agent "news-agent" code with hash "def456"
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then agent "news-agent" should have code_hash "abc123"
    And Object Store should still contain both "abc123" and "def456"

  Scenario: Rollback restores agent LLM assignments
    Given I register agent "news-agent" with models:
      | model_id                     | priority | purpose   |
      | anthropic/claude-haiku-4-5   | 1        | execution |
      | ollama/llama3                | 2        | execution |
    And a new Generation 2 is created
    When I change agent "news-agent" models to:
      | model_id                     | priority | purpose   |
      | openai/gpt-4o               | 1        | execution |
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then agent "news-agent" should have execution models ["anthropic/claude-haiku-4-5", "ollama/llama3"]
    And agent "news-agent" should NOT have model "openai/gpt-4o"

  Scenario: Rollback restores policies
    Given policy for "search.web" is "always"
    And a new Generation 2 is created
    When I change policy for "search.web" to "confirm"
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then policy for "search.web" should be "always"

  Scenario: Rollback restores LLM primary model
    Given primary model is "anthropic/claude-sonnet-4-6"
    And a new Generation 2 is created
    When I change primary model to "openai/gpt-4o"
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then primary model should be "anthropic/claude-sonnet-4-6"

  Scenario: Rollback skips rotated credentials
    Given I store credential "connector:brave" with api_key "old-key"
    And a new Generation 2 is created
    When I rotate credential "connector:brave" to "new-key" with security_rotated=1
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then credential "connector:brave" should still have the rotated key
    And an audit event "rollback.credential_skipped" should be logged

  Scenario: Rollback to non-existent generation fails gracefully
    When I try to rollback to Generation 999
    Then I should get a GenerationNotFoundError

  Scenario: Dedup prevents unnecessary generations
    Given the current state produces hash "snapshot-hash-1"
    When I call apply_from_state twice without changes
    Then only one Generation should exist with that hash

  Scenario: Object Store files survive rollback
    Given I store code "v1-code" in Object Store with hash "hash-v1"
    And I store code "v2-code" in Object Store with hash "hash-v2"
    When I rollback to a generation referencing "hash-v1"
    Then Object Store should contain "hash-v1"
    And Object Store should contain "hash-v2"

  Scenario: Rollback restores system LLM defaults
    Given system defaults are:
      | model_id                     | priority | purpose   |
      | anthropic/claude-sonnet-4-6  | 1        | execution |
      | anthropic/claude-haiku-4-5   | 2        | execution |
    And a new Generation 2 is created
    When I change system defaults to:
      | model_id                     | priority | purpose   |
      | openai/gpt-4o               | 1        | execution |
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then system execution models should be ["anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5"]

  Scenario: Agent-specific models override system defaults
    Given system default execution model is "anthropic/claude-sonnet-4-6"
    And agent "news-agent" has execution models ["anthropic/claude-haiku-4-5", "ollama/llama3"]
    When I resolve models for agent "news-agent" purpose "execution"
    Then the result should be ["anthropic/claude-haiku-4-5", "ollama/llama3"]

  Scenario: Agent without own models falls back to system defaults
    Given system default execution models are ["anthropic/claude-sonnet-4-6"]
    And agent "simple-agent" has NO execution models configured
    When I resolve models for agent "simple-agent" purpose "execution"
    Then the result should be ["anthropic/claude-sonnet-4-6"]

  Scenario: Full round-trip — snapshot, modify, rollback, verify
    Given I set up connector "duckduckgo" with capabilities ["search.web"]
    And I register agent "search-agent" with capabilities ["search.web"] and code "v1"
    And I set policy "search.web" to "always"
    And a new Generation 2 is created
    When I add connector "brave" with capabilities ["search.web.brave"]
    And I add capability "http.get" to agent "search-agent"
    And I change primary model to "openai/gpt-4o"
    And a new Generation 3 is created
    And I rollback to Generation 2
    Then connector "duckduckgo" should exist with capabilities ["search.web"]
    And connector "brave" should NOT exist
    And agent "search-agent" should have capabilities ["search.web"] only
    And policy "search.web" should be "always"
