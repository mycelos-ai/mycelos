@use-case @config @generations @rollback @nixos-style @milestone-2
Feature: Living with Config Generations — The NixOS Experience
  As a Mycelos user
  I want to understand and control how my system evolves over time
  So that I can experiment fearlessly, recover from mistakes, and
  always know what changed and why

  Config Generations are Mycelos's version of "undo for everything".
  Every change — new agent, new connector, policy change, model switch —
  creates an immutable snapshot. You can go back to any previous state
  at any time. This feature describes how that feels in daily use.

  Background:
    Given Mycelos has been running for 2 weeks
    And the current config generation is 12
    And 3 agents are active: email-summary, github-pr-review, invoice-processor

  # ── Exploring Your History ──────────────────────────────────────

  @config-list @daily-use
  Scenario: User explores what has changed over time
    When the user runs "mycelos config list"
    Then the system shows a readable timeline:
      """
      Config Generations:

        Gen 12  (active, confirmed)    2 Tage her
                + scheduled_tasks.daily-email    Cron auf 07:30 geaendert
                  Trigger: manuell (du)

        Gen 11  (confirmed)            5 Tage her
                ~ agents.email-summary v3        Agent verbessert
                  Trigger: Creator-Agent (Reputation < 0.5)

        Gen 10  (confirmed)            1 Woche her
                + agents.invoice-processor       Neuer Agent
                + policies.invoice-processor     Neue Policy
                  Trigger: manuell (du)

        Gen 9   (confirmed)            1 Woche her
                ~ llm.default_model              haiku → sonnet
                  Trigger: manuell (du)

        ...weitere mit "mycelos config list --all"
      """
    And each entry shows WHAT changed, WHO triggered it, and WHEN

  @config-diff @inspection
  Scenario: User compares two generations to understand a change
    When the user runs "mycelos config diff 9 10"
    Then the system shows a human-readable diff:
      """
      Vergleich Gen 9 → Gen 10:

        Neuer Agent:
          + invoice-processor (deterministisch + Haiku)
            Kann: Rechnungs-PDFs lesen, Felder extrahieren
            Braucht: artifacts.read, artifacts.create

        Neue Policy:
          + invoice-processor.artifacts.read = always
          + invoice-processor.artifacts.create = always

        Unveraendert:
          email-summary, github-pr-review, alle Connectors,
          Scheduled Tasks, LLM-Konfiguration
      """
    And the diff is semantic (not raw JSON), grouped by topic

  # ── Fearless Experimentation ────────────────────────────────────

  @experiment @rollback
  Scenario: User tries a risky change knowing they can always go back
    Given the user wants to switch from Anthropic to OpenAI as LLM provider
    When the user says in chat "Wechsle mal auf OpenAI, ich will testen ob das guenstiger ist"
    Then the Creator-Agent prepares the change:
      """
      Ich wuerde den LLM Provider auf OpenAI umstellen.
      Das betrifft alle Agents die ein LLM nutzen (2 von 3).

      Falls etwas nicht funktioniert, kann ich jederzeit
      zurueck auf Anthropic wechseln — ein Befehl genuegt.

      Soll ich das machen?
      """
    When the user confirms
    Then a new generation (Gen 13) is created
    And the guard period starts (5 Min, MEDIUM risk)
    And the Creator-Agent reports:
      """
      Umgestellt! Ich beobachte jetzt 5 Minuten ob alles laeuft.
      Du kannst ganz normal weiterarbeiten.
      """

  @experiment @auto-rollback @user-notification
  Scenario: System auto-repairs and explains what happened
    Given the user switched to OpenAI (Gen 13)
    And the guard period is running
    When the email-summary agent fails 3 times (OpenAI rejects the prompt format)
    And the error rate exceeds 40%
    Then the system automatically rolls back to Gen 12 (Anthropic)
    And the user sees a calm, clear notification:
      """
      Automatischer Rollback: Gen 13 → Gen 12

      Was passiert ist: Der Email-Agent hat mit OpenAI nicht
      funktioniert (3 Fehler in 5 Minuten). Das Prompt-Format
      war nicht kompatibel.

      Was ich getan habe: Automatisch zurueck auf Anthropic
      gewechselt. Alle Agents laufen wieder normal.

      Keine Daten verloren. Keine Aktion noetig.

      Willst du die Details sehen? → mycelos config diff 12 13
      """
    And the user's workflow was NOT interrupted
    And all scheduled tasks continue with the working config

  @experiment @manual-rollback
  Scenario: User manually goes back after trying something
    Given the user has been on Gen 13 for 2 days
    And everything works but OpenAI is actually more expensive
    When the user says "Geh zurueck auf Anthropic"
    Then the Creator-Agent understands this as a rollback request:
      """
      Du warst vorher auf Anthropic (Gen 12). Soll ich
      dahin zurueckwechseln? Deine Agents und Einstellungen
      von Gen 12 werden wiederhergestellt.

      Aenderungen seit Gen 12:
        - LLM Provider: OpenAI (wird rueckgaengig gemacht)
        - 4 Task-Runs unter OpenAI (Ergebnisse bleiben erhalten)
      """
    When the user confirms
    Then the rollback happens instantly (atomic pointer swap)
    And the user sees: "Fertig! Zurueck auf Anthropic (Gen 12)."

  # ── Understanding What the System Did Automatically ─────────────

  @auto-change @creator-agent @transparency
  Scenario: System explains an automatic change it made
    Given the email-summary agent has been failing frequently
    And the Creator-Agent has improved the agent automatically
    When the user opens "mycelos chat"
    Then the inbox briefing includes:
      """
      System-Aenderung: Email-Agent verbessert (Gen 11)

      Was war das Problem: HTML-Emails wurden nicht korrekt
      geparst (6 von 15 Laeufen fehlgeschlagen).

      Was ich geaendert habe: Email-Agent v2 → v3, besserer
      HTML-Parser, neue Tests fuer HTML-Format.

      Alle bestehenden Tests bestanden. Auditor hat geprueft.
      Seit dem Update: 0 Fehler in 8 Laeufen.

      Du kannst die Aenderung rueckgaengig machen:
        mycelos config rollback 10
      """

  @auto-change @review
  Scenario: User reviews what the Creator-Agent changed in detail
    When the user runs "mycelos config diff 10 11"
    Then the diff shows exactly what the Creator-Agent changed:
      """
      Agent-Update: email-summary v2 → v3

        Code-Aenderungen:
          ~ email_summary.py    +15 Zeilen, -3 Zeilen
            Neuer HTML-Parser (beautifulsoup4 statt Regex)

        Neue Tests:
          + test: "Agent handles HTML-only emails"
          + test: "Agent handles mixed text/HTML emails"

        Auditor-Ergebnis: APPROVED
          - Keine unauthorisierten Netzwerk-Calls
          - Keine Dateisystem-Zugriffe ausserhalb Sandbox
          - Keine neuen Dependencies

        Performance:
          Vorher: 60% Erfolgsrate, $0.003/Run
          Nachher: 100% Erfolgsrate, $0.003/Run
      """

  # ── Safety Net in Action ────────────────────────────────────────

  @safety-net @accidental-change
  Scenario: User accidentally breaks something and recovers instantly
    Given the user is experimenting in chat
    When the user says "Loesche den Email-Agent"
    Then the Creator-Agent asks for confirmation:
      """
      Den Email-Agent loeschen? Damit wuerde auch der taegliche
      Morgen-Summary-Job aufhoeren.

      Bist du sicher? Du kannst ihn spaeter wiederherstellen
      mit "mycelos config rollback".
      """
    When the user confirms (accidentally or intentionally)
    Then the agent is removed and a new generation created
    When the user realizes the mistake 2 hours later
    And says "Oh nein, ich brauch den Email-Agent zurueck"
    Then the Creator-Agent responds:
      """
      Kein Problem! Der Email-Agent war in Gen 12 aktiv.
      Ich stelle ihn wieder her.
      """
    And the rollback restores the agent, its tests, its policies, and its schedule
    And the 2 hours of other changes (if any) are preserved via a new generation
    And the user sees: "Email-Agent ist zurueck. Naechster Lauf: morgen 07:30."

  @safety-net @concurrent-change
  Scenario: System detects conflicting changes
    Given the user is changing a policy in terminal
    And the Creator-Agent is simultaneously improving an agent
    When both changes try to create a new generation at the same time
    Then the verify phase detects the race condition:
      """
      Hinweis: Waehrend deiner Aenderung hat der Creator-Agent
      ebenfalls eine Aenderung gemacht (Email-Agent Update).

      Ich fuehre beide Aenderungen zusammen. Einen Moment...
      """
    And the system re-resolves against the latest state
    And both changes are merged into a single new generation
    And the user sees the combined plan for approval

  # ── Long-Term Benefits ──────────────────────────────────────────

  @long-term @history
  Scenario: User looks back at their system's evolution after months
    Given Mycelos has been running for 3 months
    And the current generation is 47
    When the user runs "mycelos config list --summary"
    Then the system shows a high-level evolution:
      """
      Dein Mycelos-System: 3 Monate, 47 Generationen

        Monat 1:  Setup, Email-Agent, erster Scheduled Task
        Monat 2:  GitHub-Agent, Invoice-Agent, 2 Auto-Verbesserungen
        Monat 3:  Slack-Kanal, 3 Workflows, 1 Auto-Rollback

        Aktuell aktiv: 5 Agents, 3 Scheduled Tasks, 2 Channels
        Erfolgsrate: 94% | Kosten: ~$12/Monat | 127 Tasks erledigt

        Groesste Verbesserung: Email-Agent (v1 → v4, Erfolgsrate 60% → 99%)
        Letzter Rollback: vor 3 Wochen (OpenAI-Experiment)
      """
    And the user sees the complete history of their system growing

  @long-term @audit
  Scenario: User needs to understand why something was the way it was
    Given a client asks "Warum hat dein System am 15. Maerz diese Email geschickt?"
    When the user runs "mycelos config list --date 2026-03-15"
    Then the system shows which generation was active on that date
    And "mycelos audit log --date 2026-03-15 --agent email-summary" shows:
      """
      15.03.2026 08:00:12  task.started     email-morning-summary (Gen 10)
      15.03.2026 08:00:14  capability.issued email.read (TTL: 30min)
      15.03.2026 08:00:18  agent.execution   17 emails processed
      15.03.2026 08:00:19  capability.issued email.send (prepare mode)
      15.03.2026 08:00:19  task.paused       Antwort-Entwurf wartet auf Bestaetigung
      15.03.2026 09:15:33  user.approved     Antwort-Entwurf gesendet
      """
    And every action is traceable to a specific generation, agent version, and user decision
