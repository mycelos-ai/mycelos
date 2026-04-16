@security @threat-model @credential-theft @isolation @critical
Feature: Agent Attempting Direct Credential Access
  An agent attempts to bypass the Credential Proxy and access secrets directly.

  Mitigation: Process isolation + Stripped environment + Encrypted storage

  @environment-stripping
  Scenario: Agent process has no credential environment variables
    Given an agent is started in its sandbox
    Then the agent's environment contains:
      | variable          | present |
      | MAICEL_MASTER_KEY | NO      |
      | API_KEY           | NO      |
      | GITHUB_TOKEN      | NO      |
      | HOME              | sandboxed path |
      | PATH              | restricted     |
    And no credential-related variables are accessible
    And the agent cannot read the parent process environment

  @database-access
  Scenario: Agent tries to read the credentials table
    Given the agent somehow obtains the path to mycelos.db
    When the agent tries to read the credentials table
    Then the sandbox filesystem isolation blocks access to mycelos.db
    And even if the agent could read it, credentials are encrypted
    And decryption requires MAICEL_MASTER_KEY (not in agent process)

  @proxy-url-abuse
  Scenario: Agent tries to abuse the Credential Proxy URL
    Given the agent knows the Credential Proxy endpoint
    When the agent sends a crafted request to the proxy
    Then the proxy validates the agent's capability token
    And only serves credentials for the agent's registered service
    And the proxy logs the access attempt
    And the proxy never returns raw credentials (only injects headers)
