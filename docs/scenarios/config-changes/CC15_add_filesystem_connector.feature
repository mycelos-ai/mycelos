@config-change @connector @filesystem @security @risk-high
Feature: Adding Filesystem Connector with Read/Write Arrays
  Scenario: Granting agent read access to specific directory
    When the user configures the filesystem connector for code-reviewer:
      | read  | /Users/stefan/projects/myapp       |
      | write | []                                 |
    Then the Blueprint Plan shows:
      | change                                    | risk |
      | + connectors.filesystem.code-reviewer     | HIGH |
    And the agent can read files in /Users/stefan/projects/myapp
    But cannot write anywhere outside the sandbox
    And bind-mounts (or symlinks in LocalSandbox) are set up

  Scenario: Adding write access requires extra caution
    When the user adds write access to "/Users/stefan/projects/myapp/output"
    Then the Blueprint Plan shows risk HIGH
    And the Guardian Check monitors write operations
    And writes to paths outside the configured array are blocked
    And all file writes are logged in the audit trail
