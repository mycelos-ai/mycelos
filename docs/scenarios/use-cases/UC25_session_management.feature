@use-case @session @conversation @channels @milestone-3
Feature: Session Management — Conversations That Remember
  As a Mycelos user
  I want my conversations to be persistent, resumable, and linked to tasks
  So that I can pick up where I left off and track what happened

  Sessions are the container for conversations. A session persists across
  channels (start on Terminal, continue on Telegram), survives restarts,
  and links messages to the tasks they triggered. The conversation history
  is stored as JSONL files (source of truth) with a SQLite index for search.

  Background:
    Given Mycelos is running with the gateway active
    And the user has terminal and Telegram channels configured

  # ── Session Basics ──────────────────────────────────────────────

  @session-start @happy-path
  Scenario: A new session starts automatically with mycelos chat
    When the user runs "mycelos chat"
    Then a new session is created with a unique session_id
    And a JSONL file is created at conversations/<session_id>.jsonl
    And the session is linked to the user (user_id)
    And the inbox briefing is shown (pending tasks, notifications)
    And every message in this chat is appended to the JSONL file

  @session-resume @persistence
  Scenario: User resumes a previous conversation
    Given the user had a conversation yesterday about invoice processing
    And the session was saved as conversations/sess-abc123.jsonl
    When the user runs "mycelos chat --continue"
    Then the system loads the most recent session
    And shows the last few messages as context:
      """
      Letzte Sitzung (gestern, 17:30):
        Du: "Ich moechte Rechnungen automatisch verarbeiten"
        Mycelos: "Dafuer brauchen wir einen OCR-Agent..."

      Weitermachen oder neue Sitzung? [Weiter/Neu]
      """
    When the user chooses "Weiter"
    Then the conversation continues in the same session
    And the same session_id is used
    And the JSONL file is appended to (not overwritten)

  @session-resume @named
  Scenario: User resumes a specific named conversation
    Given the user has multiple past sessions:
      | session_id  | title                    | last_activity |
      | sess-abc    | Email-Agent Setup        | gestern       |
      | sess-def    | Rechnungsverarbeitung    | vor 3 Tagen   |
      | sess-ghi    | GitHub PR Review Config  | letzte Woche  |
    When the user runs "mycelos chat --session sess-def"
    Then the Rechnungsverarbeitung session is loaded
    And the conversation continues where it left off

  @session-list
  Scenario: User browses their conversation history
    When the user runs "mycelos sessions"
    Then the system shows recent sessions:
      """
      Deine Sitzungen:

        sess-abc  Email-Agent Setup          gestern 17:30   12 Nachrichten
                  → Agent erstellt, Schedule eingerichtet

        sess-def  Rechnungsverarbeitung      vor 3 Tagen     8 Nachrichten
                  → OCR-Agent in Arbeit, OAuth noch offen

        sess-ghi  GitHub PR Review Config    letzte Woche    5 Nachrichten
                  → Abgeschlossen, Agent aktiv

      Oeffnen: mycelos chat --session <id>
      Suchen:  mycelos sessions --search "Rechnung"
      """

  # ── Cross-Channel Sessions ─────────────────────────────────────

  @cross-channel @session-identity
  Scenario: Same session seamlessly across Terminal and Telegram
    Given the user starts a session in Terminal
    And says "Erstelle mir einen Kalender-Agent"
    And the Creator-Agent begins the setup
    When the user leaves the Terminal and switches to Telegram
    And sends a message to the Mycelos bot
    Then the same session is active (matched by user_id)
    And the conversation history is available
    And the Creator-Agent continues where it left off:
      """
      Ich sehe, wir waren beim Kalender-Agent. Du brauchst noch
      den Google Calendar Connector. Soll ich den jetzt einrichten?
      """

  @cross-channel @message-sync
  Scenario: Actions from one channel are visible in another
    Given the user approved an email draft via Telegram at 10:00
    When the user opens Terminal at 11:00
    Then the session shows the Telegram action:
      """
      [10:00, Telegram] Du hast den Email-Entwurf genehmigt.
      Email wurde gesendet an: chef@firma.de
      """
    And the inbox briefing does NOT re-show the approved item

  # ── Session ↔ Task Linking ─────────────────────────────────────

  @task-linking
  Scenario: Messages are linked to the tasks they trigger
    Given the user says "Fasse meine Emails zusammen"
    And this triggers task-456
    When the task completes
    Then the session message is linked to task-456
    And the user can later ask "Was war das Ergebnis von vorhin?"
    And the system finds the linked task and shows the result
    And "mycelos debug task task-456" shows which session message triggered it

  @task-linking @multiple
  Scenario: One conversation triggers multiple tasks
    Given the user says "Fasse Emails zusammen und check meine GitHub PRs"
    Then the PlannerAgent creates two tasks:
      | task     | workflow           |
      | task-457 | email-summary      |
      | task-458 | github-pr-review   |
    And both tasks are linked to the same session message
    And results appear in the chat as they complete:
      """
      [task-457] Email-Zusammenfassung: 8 ungelesene Emails...

      [task-458] GitHub PRs: 2 offene PRs, 1 wartet auf dein Review...
      """

  # ── Context Window Management ──────────────────────────────────

  @context-management @compression
  Scenario: Long conversations are automatically compressed
    Given the session has 200+ messages over several days
    When the conversation approaches the LLM context limit
    Then the system automatically compresses older messages:
      """
      (Aeltere Nachrichten wurden zusammengefasst)

      Zusammenfassung der bisherigen Sitzung:
      - Email-Agent eingerichtet (Mo)
      - Rechnungs-Pipeline besprochen (Di)
      - GitHub-Integration getestet (Mi)
      - Heute: Kalender-Agent geplant
      """
    And a compact_summary record is appended to the JSONL file
    And the original messages are NOT deleted (JSONL is append-only)
    And the user can still search old messages via "mycelos sessions --search"
    And the LLM sees: compact_summary + recent messages (within budget)

  @context-management @explicit
  Scenario: User explicitly starts fresh within same session
    Given a long running session with lots of context
    When the user says "Neues Thema" or "Vergiss den bisherigen Kontext"
    Then the system creates a context boundary:
      """
      Alles klar, frischer Start! Deine bisherige Konversation
      bleibt gespeichert, aber ich starte den Kontext neu.

      Worum geht's?
      """
    And a compact_summary of everything before is saved
    And the LLM context starts fresh (only system prompt + new messages)
    And the session_id stays the same (it's the same session, just fresh context)

  # ── Session Lifecycle ───────────────────────────────────────────

  @session-end @archiving
  Scenario: Sessions are archived after inactivity
    Given a session has been inactive for 30 days
    Then the session status is set to "archived"
    And the JSONL file remains on disk (never deleted automatically)
    And the SQLite index entry is retained (searchable)
    And the session can be re-opened at any time:
      """
      mycelos chat --session sess-old123
      """
    And the system warns: "Diese Sitzung war 30 Tage inaktiv. Kontext wird neu geladen."

  @session-search
  Scenario: User searches across all conversations
    When the user runs "mycelos sessions --search 'Rechnung Meyer'"
    Then the system searches the message index (FTS5):
      """
      Gefunden in 2 Sitzungen:

        sess-def  Rechnungsverarbeitung (vor 3 Tagen)
                  "...die Rechnung von Meyer GmbH ueber 1.234 EUR..."

        sess-abc  Email-Agent Setup (gestern)
                  "...Email von Meyer mit Rechnungsanhang..."
      """
    And results show the matching message with context
    And the user can open either session to see the full conversation
