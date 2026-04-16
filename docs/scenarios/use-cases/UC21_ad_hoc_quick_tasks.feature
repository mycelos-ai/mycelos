@use-case @ad-hoc @daily-use @milestone-2
Feature: Ad-Hoc Quick Tasks Without Workflow Setup
  As a Mycelos user
  I want to ask quick one-off questions and get tasks done immediately
  So that I don't need to create a workflow for every small thing

  Mycelos is not just for scheduled automations. The most common daily
  interaction is a quick ad-hoc request — "summarize this", "draft a reply",
  "what's on my calendar tomorrow". These must work instantly without
  agent creation, workflow setup, or permission dialogs.

  Background:
    Given the system is initialized with at least one connector
    And the user is in an active chat session via "mycelos chat"

  @quick-task @happy-path
  Scenario: Quick one-off email summary without workflow creation
    Given the email connector is configured with "always" read permission
    When the user says "Summarize my unread emails"
    Then the PlannerAgent recognizes this as an ad-hoc task
    And the PlannerAgent finds the existing email-summary workflow (Fast Path)
    And the task executes immediately without:
      | skipped step              | reason                                |
      | workflow creation         | existing workflow matches             |
      | permission dialog         | email.read already set to "always"    |
      | dry-run                   | workflow already proven (score > 0.8) |
      | user confirmation         | Fast Path, low risk                   |
    And the user sees the email summary within seconds
    And the task is logged in the task history

  @quick-task @no-existing-workflow
  Scenario: Ad-hoc task when no matching workflow exists
    Given no calendar connector is configured
    When the user says "What meetings do I have tomorrow?"
    Then the PlannerAgent recognizes this needs a calendar connector
    And the system responds conversationally:
      """
      Dafuer brauche ich Zugriff auf deinen Kalender.
      Soll ich den Google Calendar Connector einrichten?
      Das dauert ca. 2 Minuten.
      """
    And the user can choose to set it up now or skip
    When the user says "Ja, mach das"
    Then the system guides through Google OAuth inline in the chat
    And after setup, the original question is answered immediately
    And the system suggests: "Soll ich das jeden Morgen automatisch machen?"

  @quick-task @one-shot
  Scenario: One-shot task that doesn't need a persistent agent
    When the user says "Rechne mir 19% MwSt auf 1.250 EUR"
    Then the PlannerAgent recognizes this as a simple calculation
    And the task runs deterministically (no LLM needed, cost: $0.00)
    And the user sees the result immediately:
      """
      Netto:  1.250,00 EUR
      MwSt:     237,50 EUR (19%)
      Brutto: 1.487,50 EUR
      """
    And no workflow or agent is created for this

  @quick-task @context-aware
  Scenario: Ad-hoc task that uses conversation context
    Given the user previously asked for an email summary
    And the summary mentioned an email from "Chef" about "Projektbericht"
    When the user says "Schreib eine Antwort auf die Mail vom Chef"
    Then the PlannerAgent uses the session context to identify the email
    And the email-summary agent drafts a reply
    And the draft is shown as a "prepare" output (user reviews before sending)
    And the user can edit, approve, or discard the draft

  @quick-task @attachment
  Scenario: User sends a file for quick processing
    Given the user is chatting via Telegram
    When the user sends a PDF file with the message "Was steht da drin?"
    Then the system creates a temporary artifact from the upload
    And the PlannerAgent routes to OCR processing (deterministic, $0.00)
    And the extracted text is summarized by a light model (Haiku)
    And the user sees the summary in the chat
    And the temporary artifact expires after 24 hours

  @quick-task @cost-transparency
  Scenario: User sees what a quick task costs
    When the user says "Analysiere meine letzten 50 GitHub Commits"
    Then the PlannerAgent estimates the cost before execution:
      """
      Das braucht ca. 50 API-Calls und eine LLM-Zusammenfassung.
      Geschaetzte Kosten: ~$0.08
      Soll ich loslegen?
      """
    When the user confirms
    Then the task runs and shows actual cost after completion:
      """
      Analyse abgeschlossen (47 Commits, $0.06).
      """
