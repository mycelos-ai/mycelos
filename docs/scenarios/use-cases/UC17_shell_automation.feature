@use-case @shell @sandbox @security @milestone-4
Feature: Shell Command Automation with Whitelist
  As a DevOps engineer
  I want agents that can run shell commands safely
  So that I can automate system maintenance tasks

  Background:
    Given the shell connector is configured with a command whitelist
    And a "system-monitor" deterministic agent is registered

  @whitelist
  Scenario: Shell connector enforces command whitelist
    Given the shell connector whitelist allows:
      | command | args_pattern        |
      | df      | -h                  |
      | uptime  |                     |
      | ps      | aux                 |
      | tail    | -n * /var/log/*     |
    When the agent runs "df -h"
    Then the command executes successfully
    When the agent tries to run "rm -rf /"
    Then the shell connector blocks the command (not in whitelist)
    And the blocked attempt is logged in the audit trail

  @dry-run @shell
  Scenario: Shell dry-run mode blocks all writes
    Given a workflow with shell commands is in dry-run mode
    When the agent runs "tail -n 100 /var/log/syslog" (read-only)
    Then the command executes normally
    When the agent tries to run "echo test > /tmp/output" (write)
    Then the dry-run mode blocks the write operation
    And returns a dummy response: {"status": "dry-run", "would_write": "/tmp/output"}

  @sandbox-isolation
  Scenario: Shell commands run in isolated sandbox
    When the system-monitor agent runs in its sandbox
    Then the agent process has:
      | isolation          | enforcement                     |
      | no host filesystem | only /input, /workspace, /output|
      | no credentials     | environment stripped            |
      | resource limits    | CPU, memory, timeout enforced   |
      | no network         | only through Security Layer     |
