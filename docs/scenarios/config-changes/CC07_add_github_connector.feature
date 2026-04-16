@config-change @connector @github @mcp @risk-high
Feature: Adding GitHub Connector via MCP
  As a developer using Mycelos
  I want to add a GitHub connector
  So that agents can interact with my repositories

  @mcp-registration
  Scenario: GitHub MCP server tools become capabilities
    When the user runs "mycelos connector setup github"
    And provides a Personal Access Token
    Then the token is stored in the Credential Proxy
    And the GitHub MCP server is started
    And its tool schemas are auto-registered as capabilities:
      | mcp_tool          | registered_capability    |
      | repo.clone        | github.repo.clone        |
      | pr.list           | github.pr.list           |
      | pr.create         | github.pr.create         |
      | pr.comment        | github.pr.comment        |
      | issue.list        | github.issue.list        |
      | issue.create      | github.issue.create      |
    And the Blueprint Plan classifies: Risk HIGH (6 new capabilities)

  @security-layer-integration
  Scenario: Security Layer mediates all GitHub access
    Given the GitHub connector is configured
    When an agent calls github.pr.create
    Then the request flows through:
      | layer              | action                                |
      | Security Layer     | capability check (agent has pr.create?)|
      | Security Layer     | policy check (always/confirm/never?)  |
      | Guardian Check     | PR content plausibility               |
      | Credential Proxy   | inject PAT into MCP server process    |
      | MCP Server         | execute GitHub API call                |
      | Security Layer     | response sanitization (strip tokens)  |
      | Audit Logger       | log the entire operation               |

  @dry-run-github
  Scenario: Dry-run mode for GitHub write operations
    Given a workflow with github.pr.create in dry-run mode
    When the agent calls github.pr.create
    Then the MCP server returns a dummy response:
      """
      {"status": "dry-run", "would_create": "PR #42: Add feature X"}
      """
    And no actual PR is created on GitHub
    And read operations (pr.list, issue.list) execute normally
