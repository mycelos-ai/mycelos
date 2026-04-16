@config-change @connector @email @blueprint-lifecycle @risk-high
Feature: Adding Email Connector to the System
  As a Mycelos user
  I want to add an email connector
  So that agents can read and send emails on my behalf

  The addition of a new connector is a HIGH risk config change because it
  introduces new capabilities that agents could potentially misuse.

  Background:
    Given the system is initialized at config generation 3
    And no email connector is currently configured

  @blueprint-lifecycle @resolve
  Scenario: Connector setup triggers Blueprint Lifecycle
    When the user runs "mycelos connector setup email"
    And provides IMAP server details and completes OAuth
    Then the Credential Proxy encrypts and stores the OAuth token
    And the resolve phase creates a ChangeSpec:
      | field             | value                                  |
      | desired_config    | current config + email connector entry |
      | description       | Add email connector (IMAP/Gmail)       |
      | trigger           | manual                                 |
      | requires_approval | true (new capabilities = HIGH risk)    |

  @verify
  Scenario: Verify phase checks config integrity
    Given the resolve phase produced a target config
    Then the verify phase:
      | step                          | result                     |
      | canonical JSON generation     | sorted keys, no whitespace |
      | SHA-256 computation           | hash: "def456..."         |
      | duplicate check               | no existing gen with hash  |
      | active state changed check    | no parallel modification   |

  @plan @risk-classification
  Scenario: Plan phase shows risk classification
    Then the plan phase computes:
      | change                        | risk   | reason                           |
      | + connectors.email            | HIGH   | new external service integration |
      | + capabilities.email.read     | HIGH   | new capability registered        |
      | + capabilities.email.send     | HIGH   | new write capability             |
    And the CLI shows:
      """
      Blueprint Plan (Gen 3 → Gen 4):
        + connectors.email_imap       [NEW]     Risk: HIGH
        + capabilities.email.read     [NEW]     Risk: HIGH
        + capabilities.email.send     [NEW]     Risk: HIGH

        3 additions, 0 removals, 0 changes
        Risk level: HIGH — User confirmation required

        Proceed? [y/N]
      """

  @apply @security
  Scenario: Apply phase creates new generation with security implications
    When the user confirms
    Then the apply phase:
      | step                          | action                          |
      | create config_generations row | immutable snapshot stored       |
      | verify stored hash            | matches verify-phase hash      |
      | swap active_generation        | atomic pointer to Gen 4         |
      | log audit event               | config.generation.applied       |
      | start guard period            | 10 min (HIGH risk)             |
    And the Security Layer now knows about email.read and email.send
    But NO agent has these capabilities yet (must be explicitly granted)

  @status @guard-period
  Scenario: Guard period monitors the new configuration
    Given the guard period is active (10 minutes)
    Then the Health Monitor tracks:
      | metric              | threshold                    |
      | error rate           | > 40% triggers auto-rollback |
      | latency              | 2x baseline triggers alert  |
    When the guard period passes without issues
    Then the generation is marked as "confirmed"
