@security @threat-model @sandbox-escape @filesystem @critical
Feature: Sandbox Filesystem Escape Attempts
  Agent attempts to access files outside its sandbox boundaries.

  Mitigation: Filesystem isolation + Bind mounts + Path validation

  @path-traversal
  Scenario: Agent uses path traversal to escape sandbox
    Given the agent's sandbox has /input, /workspace, /output
    When the agent tries to access "/input/../../etc/passwd"
    Then the path is resolved and validated against sandbox boundaries
    And the access is blocked
    And the attempt is logged as a security event

  @symlink-attack
  Scenario: Agent creates symlink pointing outside sandbox
    Given the agent has write access to /workspace
    When the agent creates a symlink: /workspace/link -> /etc/shadow
    Then the sandbox prevents symlinks pointing outside boundaries
    And the symlink creation fails
    And the attempt is logged

  @proc-filesystem
  Scenario: Agent tries to read /proc to discover host information
    When the agent tries to access /proc/self/environ
    Then the sandbox does not mount /proc
    And the access fails with "No such file or directory"
    When the agent tries to access /proc/1/cmdline (init process)
    Then this is also blocked (no /proc available)

  @host-filesystem-via-connector
  Scenario: Agent tries to access paths not in connector config
    Given the filesystem connector allows read: ["/Users/stefan/projects"]
    When the agent tries to read "/Users/stefan/.ssh/id_rsa"
    Then the filesystem connector checks the path against allowed paths
    And blocks access (not in read array)
    And logs the unauthorized access attempt
