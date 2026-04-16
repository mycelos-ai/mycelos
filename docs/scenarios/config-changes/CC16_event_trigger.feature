@config-change @event-trigger @webhook @scheduler @risk-high
Feature: Adding Event-Based Triggers
  Scenario: GitHub PR event trigger
    When the user adds: "mycelos trigger add --event mcp:github:pr_opened --workflow pr-review"
    Then the Blueprint Plan shows risk HIGH (new automated process)
    And the Gateway registers a listener on GitHub MCP notifications
    And each PR event creates a task with the frozen permission set

  Scenario: Filesystem watcher trigger
    When the user adds: "mycelos trigger add --event fs:~/invoices/*.pdf --workflow invoice-process"
    Then the Gateway starts a filesystem watcher (watchdog/inotify)
    And new PDF files trigger the invoice processing workflow
    And the Blueprint Plan shows risk HIGH

  Scenario: Webhook trigger with HMAC authentication
    When the user configures a Stripe webhook trigger
    Then the webhook endpoint requires HMAC signature verification
    And unauthenticated webhook requests are rejected
    And valid webhooks create tasks via Huey queue
