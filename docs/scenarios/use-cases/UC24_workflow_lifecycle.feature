@use-case @workflow @planner @scoring @reuse @milestone-3
Feature: Workflow Lifecycle — Create, Reuse, Improve, Inspect
  As a Mycelos user
  I want the system to learn from successful workflows and reuse them
  So that repeated tasks get faster, cheaper, and more reliable over time

  Workflows are the reusable building blocks of Mycelos. When the Planner
  solves a task, the solution is saved as a workflow YAML. Next time a
  similar request comes in, the Planner reuses the proven workflow
  instead of creating a new one from scratch.

  Background:
    Given Mycelos has been running for 1 week
    And the email-summary workflow exists with score 0.85 (5 successful runs)
    And the user is in an active chat session

  # ── Planner Decision Process ────────────────────────────────────

  @planner-decision @reuse
  Scenario: Planner finds and reuses an existing workflow
    When the user says "Fasse meine Emails zusammen"
    Then the PlannerAgent searches for matching workflows:
      """
      Suche nach passendem Workflow...

      Stufe 1 (Kontext): email-summary gefunden (Score 0.85)
      → Exakter Match, verwende bestehenden Workflow.
      """
    And the email-summary workflow runs directly (Fast Path)
    And no new workflow or plan is created
    And the user sees results within seconds
    And after success the workflow score increases slightly

  @planner-decision @adapt
  Scenario: Planner adapts an existing workflow for a new twist
    When the user says "Fasse meine Emails zusammen, aber nur die von heute und sortiere nach Absender"
    Then the PlannerAgent finds email-summary as close match:
      """
      Aehnlicher Workflow gefunden: email-summary (Score 0.85)
      Anpassung noetig: Zeitfilter + Sortierung hinzufuegen.
      """
    And the PlannerAgent creates an adapted version:
      | change                    | type      |
      | + filter: today only      | step modification |
      | + sort: by sender         | step modification |
    And the adapted workflow gets a new version (email-summary v4)
    And the adapted version goes through dry-run
    And the user sees: "Ich verwende deinen Email-Workflow mit Anpassungen (nur heute, nach Absender sortiert)."

  @planner-decision @new
  Scenario: Planner creates a new workflow when nothing matches
    When the user says "Vergleiche die Preise meiner letzten 5 Amazon-Bestellungen mit aktuellen Preisen"
    Then the PlannerAgent searches all three stages:
      """
      Suche nach passendem Workflow...

      Stufe 1 (Kontext): Kein Match in Top-10
      Stufe 2 (Suche):   Kein Match in Registry (FTS5)
      Stufe 3 (LLM):     Nicht anwendbar (keine Kandidaten)

      → Neuer Workflow wird erstellt.
      """
    And the PlannerAgent designs a new workflow:
      """
      Ich erstelle einen neuen Ablauf:
      1. Deine letzten Bestellungen finden (braucht Email-Zugang)
      2. Aktuelle Preise abrufen (braucht Web-Zugang)
      3. Vergleich erstellen

      Dafuer brauche ich einen Web-Connector. Soll ich den einrichten?
      """
    And after execution the workflow is saved in the registry
    And available for future reuse

  # ── Workflow Scoring in Action ──────────────────────────────────

  @scoring @improvement
  Scenario: Workflow score drops and triggers improvement
    Given the invoice-processor workflow has run 20 times
    And the last 5 runs failed (PDF format changed)
    And the score dropped from 0.7 to 0.4
    Then the EvaluatorAgent analyzes the failures:
      """
      Diagnose: invoice-processor Score ist auf 0.4 gefallen.
      Ursache: Neue PDF-Formate werden nicht erkannt.
      Empfehlung: OCR-Step anpassen.
      """
    And the user sees in the inbox briefing:
      """
      ⚠ Workflow-Problem: Rechnungsverarbeitung

      Die letzten 5 Laeufe sind fehlgeschlagen. Das Problem:
      Neue Rechnungsformate werden nicht erkannt.

      Optionen:
      1. Ich verbessere den Workflow automatisch [Empfohlen]
      2. Du schaust dir das manuell an
      3. Workflow pausieren
      """
    When the user chooses option 1
    Then the PlannerAgent creates an improved version
    And the improvement goes through dry-run and Blueprint Lifecycle

  @scoring @transparent
  Scenario: User checks how well their workflows perform
    When the user says "Wie laufen meine Workflows?"
    Then the system shows a performance overview:
      """
      Deine Workflows:

        email-summary         ⭐ 0.92  (34 Runs, 0 Fehler letzte Woche)
                              Kosten: ~$0.003/Run | Dauer: ~3s

        github-pr-review      ⭐ 0.78  (12 Runs, 1 Fehler)
                              Kosten: ~$0.02/Run | Dauer: ~8s

        invoice-processor     ⚠ 0.45   (20 Runs, 5 Fehler zuletzt)
                              Kosten: ~$0.01/Run | Dauer: ~5s
                              → Verbesserung empfohlen

      Gesamt: 66 Runs, $0.38 Kosten, 3 Workflows
      """

  # ── Workflow Inspection ─────────────────────────────────────────

  @inspect @transparency
  Scenario: User wants to understand what a workflow does
    When the user says "Zeig mir den Email-Workflow"
    Then the system shows a readable summary (not raw YAML):
      """
      Workflow: email-summary (v3)
      Erstellt: vor 2 Wochen vom Creator-Agent
      Letzter Lauf: heute 08:00 (erfolgreich)
      Score: 0.92 (34 Runs)

      Ablauf:
        1. Ungelesene Emails abrufen        → email.read (immer erlaubt)
        2. Nach Wichtigkeit sortieren        → kein externer Zugriff
        3. Zusammenfassung erstellen         → LLM (Haiku, ~$0.003)
        4. Antwort-Entwurf vorbereiten       → email.send (du entscheidest)

      Schedule: Mo-Fr 07:30
      Berechtigungen: email.read (immer), email.send (mit Vorschau)
      """
    And the user can also see raw YAML with "mycelos workflow show email-summary --yaml"

  @inspect @history
  Scenario: User reviews the evolution of a workflow
    When the user runs "mycelos workflow history email-summary"
    Then the system shows the version history:
      """
      email-summary Versionen:

        v3 (aktiv)    vor 5 Tagen    Creator-Agent: besserer HTML-Parser
                      Score: 0.92    Fehlerrate: 0%

        v2            vor 2 Wochen   Creator-Agent: Truncation bei >50 Emails
                      Score: 0.71    Fehlerrate: 12%

        v1            vor 3 Wochen   Erstellt im Onboarding
                      Score: 0.60    Fehlerrate: 25%

      Trend: ↗ stetig besser (v1: 60% → v3: 92% Erfolgsrate)
      """

  # ── Workflow Storage ────────────────────────────────────────────

  @storage @yaml @filesystem
  Scenario: Workflows are stored as readable YAML files
    Given the email-summary workflow was just created
    Then the workflow YAML is saved at:
      """
      artifacts/workflows/email-summary.yaml
      """
    And the YAML is human-readable and editable
    And the workflow is indexed in SQLite (workflow_registry)
    And the YAML file is the source of truth
    And SQLite contains only metadata (score, run count, hash)

  @storage @versioning
  Scenario: Old workflow versions are preserved
    Given email-summary v2 is being replaced by v3
    Then v2 YAML is archived at:
      """
      artifacts/workflows/email-summary.v2.yaml
      """
    And v3 becomes the active version
    And both versions remain in the workflow_registry
    And the user can compare versions with "mycelos workflow diff email-summary v2 v3"
