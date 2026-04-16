@use-case @customer-service @async @pause-point @milestone-5
Feature: Customer Complaint Handler with Human-in-the-Loop
  As a small business owner
  I want automated complaint handling with my review before sending
  So that responses are professional but I maintain quality control

  Background:
    Given email connector is configured
    And a complaint-handler workflow is registered

  @async-workflow
  Scenario: Daily complaint handling with pause-points
    Given the complaint handler runs daily at 09:00
    When it processes incoming complaint emails:
      | step              | policy  | action                              |
      | identify          | always  | Scan emails for complaint patterns  |
      | analyze           | always  | Analyze complaint, classify severity|
      | draft-response    | always  | Draft professional response         |
      | send-response     | prepare | PAUSE - user reviews before sending |
    Then the user receives a notification:
      """
      Complaint Handler waiting for approval:
      From: customer@example.com
      Subject: "Defective delivery"
      Severity: HIGH
      Proposed response: [view draft]
      [Approve] [Edit] [Reject]
      """

  @multi-complaint
  Scenario: Batch processing multiple complaints
    Given 4 complaint emails arrived overnight
    Then the workflow processes each independently
    And each draft response appears separately in the inbox
    And the user can approve/reject each independently
    And approved responses are sent via email.send
    And rejected responses are discarded with logged reason

  @timeout
  Scenario: Unapproved drafts expire after timeout
    Given a draft response has been pending for 24 hours
    Then the step is automatically skipped
    And the complaint is flagged as "needs manual attention"
    And the user is reminded in the next inbox briefing
