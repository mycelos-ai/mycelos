@config-change @retention @cleanup @storage @risk-low
Feature: Changing Data Retention Policies
  Scenario: Extending audit log retention
    When the user changes audit retention from 90 days to 180 days
    Then the Blueprint Plan shows risk LOW (metadata change)
    And no data is deleted
    And the cleanup scheduler adjusts its retention window

  Scenario: Reducing artifact retention
    When the user changes task artifact retention from 30 to 7 days
    Then the Blueprint Plan shows risk MEDIUM (potential data loss)
    And a warning is shown: "Artifacts older than 7 days will be cleaned up"
    And the user must confirm

  Scenario: Config generation compression settings
    When the user adjusts the active zone from 50 to 100 generations
    Then more generations keep full snapshots
    And rollback remains instant for more history
    And storage usage increases accordingly
