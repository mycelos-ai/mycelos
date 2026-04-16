@config-change @sandbox @execution @blueprint-lifecycle @risk-critical
Feature: Changing Sandbox Configuration
  As a Mycelos administrator
  I want to change sandbox settings
  So that agents have appropriate isolation levels

  Sandbox configuration changes are CRITICAL risk because they affect
  the security boundary of agent execution.

  @switch-sandbox-type
  Scenario: Switching from LocalSandbox to DockerSandbox
    When the user modifies the sandbox configuration
    Then the Blueprint Plan shows:
      | change                                  | risk     |
      | ~ execution.sandbox_type local→docker   | CRITICAL |
    And the plan requires explicit user acknowledgment
    And the guard period is 30 minutes (CRITICAL risk)

  @filesystem-permissions
  Scenario: Adding filesystem paths to agent sandbox
    When the user adds read access for "/Users/stefan/projects" to code-reviewer agent
    Then the Blueprint Plan shows:
      | change                                              | risk |
      | + connectors.filesystem.code-reviewer.read[0]       | HIGH |
    And the Security Layer updates the sandbox bind-mounts
    And the agent can now read from /Users/stefan/projects
    But ONLY through the filesystem connector (not direct host access)

  @resource-limits
  Scenario: Adjusting agent resource limits
    When the user reduces the timeout from 300s to 60s
    Then the Blueprint Plan shows:
      | change                                   | risk   |
      | ~ execution.timeout 300→60               | MEDIUM |
    And agents exceeding 60s will be terminated
    And the guard period monitors for increased timeout failures
