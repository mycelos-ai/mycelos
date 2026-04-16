@security @threat-model @dos @config @medium
Feature: Denial of Service via Config Generation Flooding
  Automated process or compromised agent creates excessive config generations
  to fill storage or degrade performance.

  Mitigation: Rate limits + Circuit breaker + Generation limits

  @rate-limiting
  Scenario: Generation rate limits prevent flooding
    Given the system has limits:
      | limit                      | value |
      | max_generations_per_hour   | 100   |
      | max_generations_per_day    | 500   |
      | warning_threshold_per_hour | 20    |
    When 21 generations are created in one hour
    Then a warning is shown to the user
    When 101 generations are attempted in one hour
    Then the circuit breaker blocks further generations
    And the block is logged as a critical event

  @storage-exhaustion
  Scenario: Keyframe+Diff compression prevents storage bloat
    Given 1000 generations have accumulated
    Then the retention system:
      | zone    | generations | storage                    |
      | active  | last 50     | full snapshots             |
      | archive | 50-900      | keyframe every 10 + diffs  |
      | expired | 900+        | keyframes only             |
    And VACUUM runs after large deletion batches
