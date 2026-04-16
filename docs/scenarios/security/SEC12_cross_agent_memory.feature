@security @threat-model @memory @isolation @high
Feature: Cross-Agent Memory Access Violation
  An agent attempts to read or write another agent's memory.

  Mitigation: Memory Service scope enforcement + Security Layer checks

  @read-other-agent
  Scenario: Agent tries to read another agent's private memory
    Given email-agent stores "user.email_password = secret" in agent memory
    When github-agent tries: memory.get(scope="agent", key="user.email_password")
    Then the Memory Service checks agent_id
    And the request is denied (github-agent ≠ email-agent)
    And the attempt is logged as a security violation

  @write-other-agent
  Scenario: Agent tries to poison another agent's memory
    When malicious-agent tries: memory.set(scope="agent", agent_id="email-agent", key="config", value="malicious")
    Then the Memory Service rejects the request
    And agents can only write to their own agent memory scope
    And the agent_id parameter is enforced server-side (not trusted from agent)

  @shared-memory-abuse
  Scenario: Agent writes misleading data to shared memory
    Given research-agent writes to shared memory: "project.deadline = 2099-12-31"
    Then the write succeeds (agent has shared memory write permission)
    But the created_by field records "research-agent"
    And other agents can evaluate the reliability of the entry
    And the AuditorAgent can detect anomalous shared memory patterns
