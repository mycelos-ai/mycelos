@use-case @notifications @approvals @channels @scheduled-tasks @milestone-3
Feature: Notifications and Approvals — How the System Reaches You
  As a Mycelos user with scheduled tasks
  I want to be notified and asked for approvals in the right way
  So that urgent things reach me fast and non-urgent things don't interrupt

  Notification preferences are set per workflow during creation — not in
  a global settings page. The Creator-Agent asks naturally: "Wie wichtig
  ist das fuer dich? Muss ich dich sofort fragen oder kann das warten?"
  This makes the notification model feel personal, not technical.

  Background:
    Given Mycelos is running with the gateway active
    And the user has Terminal and Telegram channels configured
    And the user has 2 active scheduled tasks

  # ── Notification Preferences During Workflow Creation ───────────

  @workflow-creation @notification-interview
  Scenario: Creator-Agent asks about notification preferences during setup
    Given the user says "Richte einen Agent ein der meine Rechnungen verarbeitet"
    And the Creator-Agent has designed the workflow with a "prepare" step
    When the Creator-Agent presents the approval step
    Then it asks about notification preferences naturally:
      """
      Bei Schritt 4 bereitet der Agent die extrahierten Daten vor,
      und du pruefst ob alles stimmt bevor es in die Buchhaltung geht.

      Wie soll ich dich fragen?
      a) Sofort per Telegram (Push-Nachricht mit Buttons)
      b) Wenn du das naechste Mal den Chat oeffnest (Inbox)
      c) Beides — Telegram sofort + Inbox als Backup

      Und falls du nicht reagierst — wie lange soll ich warten?
      Standard ist 24 Stunden, dann ueberspringe ich den Schritt.
      """
    When the user chooses "a) Sofort per Telegram"
    And keeps the default timeout of 24 hours
    Then the workflow YAML stores the notification preference:
      """
      steps:
        - id: verify-extraction
          policy: prepare
          notification:
            channel: telegram
            urgency: immediate
            timeout: 24h
            timeout_action: skip
      """

  @workflow-creation @urgency-detection
  Scenario: Creator-Agent infers urgency from context
    When the user says "Jeden Morgen um 7 meine Emails zusammenfassen"
    And the workflow has a "prepare" step for email drafts
    Then the Creator-Agent infers the timing context:
      """
      Die Zusammenfassung kommt um 7 Uhr — da bist du vermutlich
      noch nicht am Rechner. Soll ich Antwort-Entwuerfe per
      Telegram schicken? Dann kannst du sie am Handy freigeben.
      """
    When the user says "Ja, gute Idee"
    Then the notification channel is set to Telegram for this workflow
    And the Creator-Agent confirms:
      """
      Perfekt. Morgens um 7 laueft die Zusammenfassung, und wenn
      ein Antwort-Entwurf dabei ist, bekommst du ihn auf Telegram.
      """

  @workflow-creation @no-push
  Scenario: User prefers not to be disturbed
    When the user says "Verarbeite Rechnungen, aber stoer mich nicht"
    Then the Creator-Agent respects the preference:
      """
      Verstanden — keine Push-Nachrichten. Ergebnisse und
      Rueckfragen liegen in deiner Inbox wenn du das naechste
      Mal "mycelos chat" oeffnest.

      Wenn du laenger als 48 Stunden nicht reinschaust und eine
      Entscheidung aussteht, soll ich dann doch eine Nachricht
      schicken? Oder einfach warten?
      """
    When the user says "Nach 48 Stunden darfst du mich anschreiben"
    Then the workflow has a fallback notification:
      """
      notification:
        channel: inbox_only
        urgency: low
        timeout: 48h
        timeout_action: notify_telegram
      """

  # ── Approval Flow in Action ─────────────────────────────────────

  @approval @telegram @push
  Scenario: Telegram approval with action buttons
    Given the email-summary workflow ran at 07:00
    And step "send-draft" is in "prepare" mode with Telegram notification
    When the step produces an email draft
    Then the user receives a Telegram message:
      """
      📧 Email-Entwurf bereit

      An: chef@firma.de
      Betreff: Re: Projektbericht

      "Hallo Herr Mueller, danke fuer die Info.
       Der Bericht ist bis Freitag fertig. Gruesse, Stefan"

      [✅ Senden]  [✏️ Bearbeiten]  [❌ Verwerfen]
      """
    When the user taps [✅ Senden]
    Then the email is sent via the Credential Proxy
    And the action is logged: "user.approved via telegram"
    And the workflow continues

  @approval @telegram @edit
  Scenario: User edits a draft before approving on Telegram
    Given a draft email is waiting for approval on Telegram
    When the user taps [✏️ Bearbeiten]
    Then Telegram shows an input prompt:
      """
      Schick mir den geaenderten Text und ich sende ihn dann:
      """
    When the user sends the corrected text
    Then the draft is updated
    And the user sees:
      """
      Geaendert! Soll ich jetzt senden?
      [✅ Senden]  [❌ Verwerfen]
      """

  @approval @inbox @pull
  Scenario: Approval waits in inbox for terminal users
    Given the invoice-processor workflow ran at 09:00
    And step "verify-extraction" is in "prepare" mode with inbox notification
    When the user opens "mycelos chat" at 12:00
    Then the inbox briefing shows:
      """
      Wartende Entscheidungen:

        ⏳ Rechnungsverarbeitung (seit 09:00, 3h her)
           Rechnung von Meyer GmbH: 1.234,56 EUR
           Erkannte Felder: Betrag, Datum, USt-ID, Bankverbindung
           [Bestaetigen]  [Korrigieren]  [Verwerfen]
      """
    And the user can review and decide inline in the chat

  @approval @timeout
  Scenario: Approval times out and action is taken
    Given an email draft has been waiting 24 hours for approval
    And the timeout_action is "skip"
    Then the step is skipped
    And the user is notified (via configured channel):
      """
      ⏰ Timeout: Email-Entwurf an chef@firma.de nicht gesendet.
      Du hast 24 Stunden nicht reagiert. Der Entwurf wurde verworfen.

      Falls du ihn doch senden willst: mycelos task retry task-567
      """

  @approval @timeout @escalation
  Scenario: Timeout with escalation to different channel
    Given the invoice verification has been waiting 48 hours in the inbox
    And the fallback is "timeout_action: notify_telegram"
    Then after 48 hours a Telegram push is sent:
      """
      Hallo Stefan, du hast eine Rechnung die seit 2 Tagen
      auf deine Bestaetigung wartet:

      Meyer GmbH, 1.234,56 EUR

      [Bestaetigen]  [Spaeter]  [Verwerfen]
      """

  # ── Notification Types ──────────────────────────────────────────

  @notification @types
  Scenario: Different events have different default notification behavior
    Given the following events occur:
      | event                      | default_channel | push? | reason                      |
      | prepare step waiting       | per workflow     | yes*  | needs user action            |
      | task completed             | inbox only       | no    | informational, no action     |
      | task failed                | inbox + push     | yes   | user should know             |
      | auto-rollback              | inbox + push     | yes   | important system event       |
      | budget warning (>80%)      | inbox + push     | yes   | financial, time-sensitive    |
      | circuit breaker triggered  | inbox + push     | yes   | scheduled task deactivated   |
      | agent improved             | inbox only       | no    | informational, positive      |
      | weekly summary             | configurable     | maybe | user preference              |
    # *push only if workflow has push notification configured

  @notification @quiet-hours
  Scenario: User sets quiet hours for push notifications
    When the user says "Schick mir zwischen 22 und 7 Uhr keine Nachrichten"
    Then the system stores quiet hours:
      """
      Keine Push-Nachrichten zwischen 22:00 und 07:00.
      Nachrichten aus dieser Zeit landen in deiner Inbox
      und werden um 07:00 als Sammel-Nachricht geschickt.
      """
    And push notifications during quiet hours are queued
    And at 07:00 a single summary push is sent with all queued items

  # ── Notification Preferences Changes ────────────────────────────

  @notification @change
  Scenario: User changes notification preferences for existing workflow
    When the user says "Schick mir die Email-Entwuerfe nicht mehr auf Telegram, Inbox reicht"
    Then the Creator-Agent updates the workflow:
      """
      OK, Email-Entwuerfe kommen jetzt nur noch in deine Inbox.
      Du wirst nicht mehr per Telegram gefragt.

      Andere Nachrichten (Fehler, Warnungen) schicke ich
      weiterhin per Telegram. Passt das?
      """
    And the workflow YAML notification section is updated
    And the change goes through the Blueprint Lifecycle (Risk: LOW)

  @notification @global-override
  Scenario: User sets a global notification preference
    When the user says "Generell: nur Fehler und Warnungen per Telegram, alles andere Inbox"
    Then the system stores a global notification policy:
      """
      Globale Einstellung gespeichert:
        Telegram: Nur Fehler, Warnungen, Budget-Alarme
        Inbox: Alles

      Workflow-spezifische Einstellungen haben Vorrang.
      Dein Email-Workflow schickt weiterhin Entwuerfe per
      Telegram (wie du es eingerichtet hast).
      """
    And workflow-specific settings override the global default
