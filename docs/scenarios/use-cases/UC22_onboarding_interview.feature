@use-case @onboarding @creator-agent @milestone-1
Feature: Creator-Agent Onboarding Interview
  As a new Mycelos user
  I want the Creator-Agent to guide me through setup in a conversation
  So that I don't need to understand technical concepts to get started

  The onboarding is a guided conversation, not a config wizard. The
  Creator-Agent asks simple questions, explains what it's doing, and
  makes the technical complexity invisible. The goal: first useful
  result within 5 minutes of starting "mycelos chat".

  Background:
    Given "mycelos init" has been run (DB created, LLM provider configured)
    And no connectors or agents are configured yet

  @interview @first-contact
  Scenario: Creator-Agent starts with a friendly interview
    When the user runs "mycelos chat" for the first time
    Then the Creator-Agent greets the user:
      """
      Willkommen bei Mycelos! Ich bin dein persoenlicher Assistent.
      Ich helfe dir, Aufgaben zu automatisieren — Emails zusammenfassen,
      Termine planen, Rechnungen verarbeiten, und vieles mehr.

      Wie moechtest du genannt werden?
      """
    When the user says "Stefan"
    Then the Creator-Agent stores the name in system memory
    And continues:
      """
      Hallo Stefan! Lass uns herausfinden, was ich fuer dich tun kann.
      Was nervt dich im Alltag am meisten? Zum Beispiel:
      - Zu viele Emails
      - Termine im Blick behalten
      - Rechnungen oder Dokumente verarbeiten
      - Code Reviews auf GitHub

      Oder erzaehl mir einfach, was du gerne automatisieren wuerdest.
      """

  @interview @email-path @google-oauth
  Scenario: User wants email help — Creator-Agent sets up Gmail
    Given the interview has started and the user's name is "Stefan"
    When the user says "Ich ertrinke in Emails"
    Then the Creator-Agent responds:
      """
      Verstehe! Ich kann dir einen Email-Assistenten einrichten der:
      - Jeden Morgen deine ungelesenen Emails zusammenfasst
      - Wichtige Emails hervorhebt
      - Antwort-Entwuerfe vorbereitet (du entscheidest ob sie rausgehen)

      Welchen Email-Anbieter nutzt du? Gmail, Outlook, oder etwas anderes?
      """
    When the user says "Gmail"
    Then the Creator-Agent says:
      """
      Gmail — perfekt. Ich oeffne gleich ein Browser-Fenster fuer die
      Google-Anmeldung. Dort erlaubst du Mycelos, deine Emails zu lesen.

      Wichtig: Dein Passwort sehe ich nie. Google gibt mir nur einen
      zeitlich begrenzten Zugang, den du jederzeit unter
      myaccount.google.com widerrufen kannst.

      Bereit? Dann starte ich die Anmeldung.
      """
    When the user confirms
    Then the system opens the Google OAuth consent screen
    And the Creator-Agent waits and shows:
      """
      Warte auf die Google-Anmeldung...
      (Falls sich kein Browser oeffnet: [Link])
      """

  @interview @google-oauth-success
  Scenario: Google OAuth completes successfully
    Given the Google OAuth flow is in progress
    When the user completes the OAuth flow in the browser
    Then the OAuth token is stored in the Credential Proxy (encrypted)
    And the Creator-Agent confirms:
      """
      Perfekt, Gmail ist verbunden! Ich kann jetzt deine Emails lesen.

      Ich richte dir jetzt den Email-Assistenten ein. Das dauert
      ca. 30 Sekunden — ich erstelle den Code, teste ihn, und
      lass ihn von meinem Sicherheits-Kollegen pruefen.
      """
    And the agent creation pipeline runs in the background
    And the user sees a progress indicator:
      """
      [1/4] Agent-Code wird erstellt...
      [2/4] Tests laufen...
      [3/4] Sicherheitspruefung...
      [4/4] Testlauf mit deinen Emails...
      """

  @interview @smart-defaults
  Scenario: Creator-Agent sets smart permission defaults
    Given the email-summary agent has been created and tested
    Then the Creator-Agent presents permissions as simple choices:
      """
      Dein Email-Assistent braucht zwei Berechtigungen:

      1. Emails lesen — das muss er immer duerfen, sonst funktioniert
         die Zusammenfassung nicht. OK? [Ja/Nein]

      2. Emails senden — hier hast du drei Optionen:
         a) Nie (nur Zusammenfassungen, keine Antworten)
         b) Mit Vorschau (er bereitet vor, du entscheidest)
         c) Automatisch (er antwortet selbststaendig)

         Ich empfehle "b) Mit Vorschau" — dann behältst du die Kontrolle.
      """
    When the user says "Ja" for reading and chooses "b) Mit Vorschau"
    Then email.read is set to "always"
    And email.send is set to "prepare"
    And the Blueprint Lifecycle runs in the background (user sees nothing)
    And the Creator-Agent says:
      """
      Alles eingerichtet! Hier ist dein erster Test:
      """

  @interview @first-value
  Scenario: First useful result within the onboarding conversation
    Given permissions have been set for the email agent
    Then the Creator-Agent immediately runs the email summary:
      """
      Ich fasse jetzt deine aktuellen Emails zusammen...
      """
    And the email summary is shown inline in the chat:
      """
      ## Deine Emails (12 ungelesen)

      ⭐ Chef: "Projektbericht bis Freitag" — Deadline erwaehnt
      ⭐ Kundin Meyer: "Angebot erhalten" — Wartet auf Antwort
         Newsletter: "Tech Weekly #42" — Keine Aktion noetig
         ...

      Soll ich das jeden Morgen um 8 Uhr automatisch machen?
      """
    When the user says "Ja, mach das"
    Then the scheduled task is created with cron "0 8 * * 1-5"
    And the Creator-Agent confirms:
      """
      Fertig! Ab morgen fruh bekommst du automatisch deine
      Email-Zusammenfassung. Du findest sie in deiner Inbox
      wenn du "mycelos chat" oeffnest, oder ich schick sie
      dir per Telegram — soll ich das einrichten?
      """

  @interview @google-oauth-failure
  Scenario: Google OAuth fails or user cancels
    Given the Google OAuth flow is in progress
    When the OAuth flow fails or the user closes the browser
    Then the Creator-Agent responds calmly:
      """
      Kein Problem! Die Google-Anmeldung hat nicht geklappt.
      Das passiert manchmal. Du kannst es spaeter nochmal versuchen mit:
        mycelos connector setup email

      Soll ich dir in der Zwischenzeit bei etwas anderem helfen?
      Ich kann zum Beispiel auch mit GitHub, Kalendern oder Dateien arbeiten.
      """
    And no partial configuration is left behind
    And the system remains in a clean state

  @interview @non-technical
  Scenario: User describes need without technical terms
    Given the interview has started
    When the user says "Ich bekomme immer Rechnungen als PDF per Email und muss die manuell in meine Buchhaltung eintippen"
    Then the Creator-Agent understands the multi-step need:
      """
      Das kann ich automatisieren! Der Ablauf waere:
      1. Emails mit Rechnungs-Anhaengen erkennen
      2. PDFs per OCR lesen (kostenlos, kein LLM noetig)
      3. Rechnungsdaten extrahieren (Betrag, Datum, Absender)
      4. Dir die Ergebnisse zur Pruefung vorlegen

      Dafuer brauche ich Email-Zugang. Nutzt du Gmail?
      """
    And the flow continues with connector setup as above
