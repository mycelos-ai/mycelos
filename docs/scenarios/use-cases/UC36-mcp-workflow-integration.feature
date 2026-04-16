@mcp @workflow @security @policy
Feature: MCP in Workflows — Planner schlaegt MCPs vor, User bestaetigt
  Als Mycelos-Benutzer moechte ich, dass der Planner automatisch passende
  MCP-Server vorschlaegt statt Eigenentwicklungen, und dass ich bei jedem
  Schritt gefragt werde bevor etwas installiert oder freigeschaltet wird.

  Background:
    Given Mycelos ist initialisiert
    And der Planner-Agent ist aktiv
    And der Creator-Agent ist aktiv
    And die PolicyEngine ist verdrahtet

  # --- Planner bevorzugt MCPs ---

  Scenario: Planner erkennt fehlenden Connector und schlaegt MCP vor
    Given der User sagt "Ich brauche einen Agent der meine GitHub Issues ueberwacht"
    And GitHub ist NICHT als Connector konfiguriert
    When der Planner die Aufgabe analysiert
    Then soll der Plan enthalten:
      | step | action                    | requires_confirm |
      | 1    | connector.add github      | yes              |
      | 2    | agent.register issue-watcher | yes           |
      | 3    | workflow.create daily-check | no              |
      | 4    | schedule.add daily 09:00   | no              |
    And der Planner soll begruenden warum GitHub MCP statt Eigenentwicklung

  Scenario: Planner nutzt existierenden Connector
    Given GitHub ist bereits als Connector konfiguriert
    And der User sagt "Erstell einen Agent der meine Issues zusammenfasst"
    When der Planner die Aufgabe analysiert
    Then soll der Plan KEINEN connector.add Schritt enthalten
    And der neue Agent soll die existierenden GitHub Capabilities nutzen

  Scenario: Planner sucht MCP Registry fuer unbekannten Service
    Given der User sagt "Ich will mein Notion anbinden"
    And kein Notion-Rezept existiert
    When der Planner die Aufgabe analysiert
    Then soll der Planner die MCP Registry durchsuchen
    And einen passenden Community MCP Server vorschlagen
    And den User warnen dass es ein Community-Server ist (kein offizielles Rezept)

  # --- Policy Enforcement bei Workflow-Ausfuehrung ---

  Scenario: Workflow-Step fragt User bei connector.add
    Given ein Plan mit Step "connector.add github" wird ausgefuehrt
    And die Policy fuer connector.add ist "confirm"
    When der WorkflowRunner Step 1 erreicht
    Then soll der User gefragt werden: "GitHub MCP hinzufuegen?"
    And der User soll die Moeglichkeit haben Ja/Nein zu sagen
    And bei "Ja" soll der Connector eingerichtet werden
    And bei "Nein" soll der Workflow pausieren

  Scenario: User-Entscheidung wird als Policy gespeichert
    Given der User hat "connector.add github" mit "Ja" bestaetigt
    When die Entscheidung gespeichert wird
    Then soll eine Policy erstellt werden:
      | user_id | agent_id | resource              | decision |
      | default | null     | connector.use:github  | always   |
    And diese Policy soll Teil der NixOS State sein
    And bei Config Rollback soll sie mitgehen

  Scenario: Agent Registration braucht IMMER Bestaetigung
    Given ein Workflow will einen neuen Agent registrieren
    And der User hat vorher schon 5 Agents bestaetigt
    When der WorkflowRunner den agent.register Step erreicht
    Then soll der User TROTZDEM gefragt werden
    And die Policy fuer agent.register ist NICHT lernbar
    And sie bleibt immer auf "confirm"

  # --- Capability Scoping ---

  Scenario: Agent sieht nur seine MCP Tools
    Given der issue-watcher Agent hat Capabilities:
      | capability           |
      | github.issues.read   |
      | github.issues.write  |
    And GitHub MCP hat 15 Tools (issues, repos, PRs, actions, etc.)
    When der LLM-Prompt fuer issue-watcher gebaut wird
    Then sollen NUR 2-3 Tools im Prompt erscheinen (issues.read, issues.write)
    And github.repos.delete soll NICHT im Prompt sein
    And github.actions.* soll NICHT im Prompt sein

  Scenario: Workflow-Step kann nur Tools seiner Capabilities nutzen
    Given ein Workflow-Step laeuft als issue-watcher Agent
    And der Step versucht github.repos.delete aufzurufen
    When die PolicyEngine den Tool-Call prueft
    Then soll der Call blockiert werden
    And ein Audit Event "capability.denied" soll geloggt werden
    And der Workflow soll mit einem Fehler pausieren

  # --- Credential Isolation ---

  Scenario: MCP Server sieht nur seinen eigenen Token
    Given GitHub und Brave Search sind konfiguriert
    When der GitHub MCP Server fuer einen Workflow-Step gestartet wird
    Then soll er NUR den GITHUB_TOKEN als Environment Variable haben
    And NICHT den BRAVE_API_KEY
    And NICHT den ANTHROPIC_API_KEY
    And NICHT den MAICEL_MASTER_KEY

  # --- End-to-End ---

  Scenario: Kompletter Flow — von User-Wunsch bis laufendem Schedule
    Given der User sagt "Ueberwache meine GitHub Issues und schick mir taeglich eine Zusammenfassung"
    When der Planner einen Plan erstellt
    And der User den Plan bestaetigt
    Then passiert folgendes in Reihenfolge:
      | step | was passiert                              | user_action  |
      | 1    | Planner schlaegt GitHub MCP vor            | bestaetigt   |
      | 2    | User gibt GitHub Token ein via /connector  | Token eingabe|
      | 3    | Creator erstellt issue-watcher Agent       | bestaetigt   |
      | 4    | Creator Pipeline: Interview→Gherkin→TDD    | automatisch  |
      | 5    | Agent bekommt Capabilities zugewiesen      | automatisch  |
      | 6    | Workflow "daily-issue-check" erstellt       | automatisch  |
      | 7    | Schedule: taeglich 09:00                   | automatisch  |
    And ab dem naechsten Tag laeuft der Workflow automatisch
    And jeder Tool-Call wird gegen PolicyEngine geprueft
    And jedes Ergebnis wird durch ResponseSanitizer gefiltert
    And der User bekommt die Zusammenfassung via Telegram/Chat
