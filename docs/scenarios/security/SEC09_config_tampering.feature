@security @threat-model @config-tampering @integrity @high
Feature: Config Snapshot Tampering
  Attacker modifies stored config snapshots to inject malicious configuration.

  Mitigation: SHA-256 content addressing + Verify phase + Audit trail

  @snapshot-modification
  Scenario: Tampered config snapshot is detected
    Given config generation 5 has hash "abc123"
    When someone modifies the config_snapshot in the database
    Then on next access, the verify phase recomputes the hash
    And the new hash does NOT match the stored config_hash
    And the system rejects the tampered config
    And an audit event with severity "critical" is logged

  @parallel-modification
  Scenario: Race condition during Blueprint Lifecycle
    Given a Blueprint Lifecycle is in the plan phase
    When another process modifies the active generation
    Then the verify phase detects the change:
      | check                        | result                    |
      | active state changed since resolve? | YES - conflict detected |
    And the lifecycle aborts
    And the resolve phase must be restarted

  @malicious-rollback
  Scenario: Rollback to a known-vulnerable generation
    Given generation 3 had a security vulnerability that was fixed in gen 4
    When someone rolls back to generation 3
    Then the rollback still goes through verify → apply → status
    And the guard period monitors for the known vulnerability pattern
    And the user is warned if rolling back past a security fix
