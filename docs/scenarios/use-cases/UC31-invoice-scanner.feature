Feature: Invoice Scanner — Full End-to-End Agent Creation + Execution
  Als Mycelos-Benutzer moechte ich einen Agent der meinen Rechnungsordner
  scannt, PDFs liest und Rechnungsdaten in eine CSV-Datei extrahiert.

  Dieser Use Case testet den GESAMTEN Stack:
  - Planner erkennt fehlenden Agent
  - Creator Pipeline erstellt ihn (Gherkin → TDD → Code → Audit)
  - Agent wird registriert
  - Agent wird ausgefuehrt auf echten Test-PDFs
  - Ergebnis: CSV mit extrahierten Daten

  Background:
    Given Mycelos ist initialisiert mit Anthropic als Provider
    And der Rechnungsordner "~/test-invoices/" existiert mit 3 Test-PDFs
    And der Ausgabeordner "~/test-output/" existiert und ist leer

  # --- Phase 1: Planung ---

  Scenario: User beschreibt den Wunsch
    Given der User schreibt "Ich haette gern eine Anwendung die meinen
      Rechnungsordner scannt, alle PDFs extrahiert und Rechnungsnummer,
      Empfaenger, Datum und Betrag in eine CSV ablegt."
    When der Orchestrator die Nachricht klassifiziert
    Then soll der Intent "task_request" sein

  Scenario: Planner erkennt fehlenden Agent
    Given der Planner analysiert den Request mit vollem System-Kontext
    When kein passender Agent oder Workflow existiert
    Then soll der Plan "needs_new_agent" enthalten
    And missing_agents soll einen "invoice-scanner" Agent beschreiben
    And die benoetigten Capabilities sollen "filesystem.read" enthalten

  # --- Phase 2: User bestaetigt Agent-Erstellung ---

  Scenario: Mycelos erklaert was fehlt und bietet Erstellung an
    Given der Plan enthaelt missing_agents
    When Mycelos dem User den Plan erklaert
    Then soll Mycelos sagen dass ein neuer Agent erstellt werden muss
    And soll fragen ob der User das moechte

  Scenario: User bestaetigt — Creator Pipeline startet
    Given der User bestaetigt mit "Ja, erstell den Agent"
    When die Creator Pipeline startet
    Then soll Phase "feasibility" den Aufwand als "medium" klassifizieren

  # --- Phase 3: Gherkin Scenarios ---

  Scenario: Gherkin Scenarios werden generiert
    Given die Creator Pipeline laeuft
    When die Gherkin-Phase erreicht wird
    Then sollen Scenarios enthalten:
      | Scenario                          |
      | PDF-Dateien im Ordner erkennen    |
      | Rechnungsdaten aus PDF extrahieren |
      | Daten in CSV schreiben            |
      | Ordner ohne PDFs behandeln        |
      | Unlesbare PDF behandeln           |

  # --- Phase 4: TDD — Tests + Code ---

  Scenario: Tests werden generiert (TDD)
    Given die Gherkin Scenarios sind bestaetigt
    When die Test-Phase laeuft
    Then sollen pytest-Tests generiert werden die:
      | Test                              |
      | PDF-Erkennung testen              |
      | Daten-Extraktion testen           |
      | CSV-Output testen                 |
      | Leerer Ordner → leere CSV         |
      | Fehlerhafte PDF → Error-Handling   |

  Scenario: Code wird generiert und besteht Tests
    Given die Tests existieren
    When der Code generiert wird
    And die Tests in der Sandbox ausgefuehrt werden
    Then sollen alle Tests gruen sein

  # --- Phase 5: Audit + Registration ---

  Scenario: Audit besteht
    Given Code und Tests sind gruen
    When der Auditor den Code reviewed
    Then soll der Code keine gefaehrlichen Imports haben
    And soll der Code nur erlaubte Tools nutzen (run, progress)

  Scenario: Agent wird registriert
    Given das Audit bestanden ist
    When der Agent registriert wird
    Then soll er in der AgentRegistry mit Status "active" stehen
    And sein Code soll im Object Store gespeichert sein
    And eine neue Config Generation soll erstellt worden sein

  # --- Phase 6: Ausfuehrung ---

  Scenario: Agent wird auf Test-Daten ausgefuehrt
    Given der Agent "invoice-scanner" ist registriert
    And der Rechnungsordner enthaelt:
      | Datei               | Rechnungsnr | Empfaenger    | Datum      | Betrag  |
      | rechnung-001.pdf    | RE-2026-001 | Firma Alpha   | 2026-01-15 | 1250.00 |
      | rechnung-002.pdf    | RE-2026-042 | Beta GmbH     | 2026-02-20 | 890.50  |
      | rechnung-003.pdf    | RE-2026-099 | Gamma AG      | 2026-03-10 | 3400.00 |
    When der Agent ausgefuehrt wird
    Then soll eine CSV-Datei erstellt werden
    And die CSV soll 3 Zeilen enthalten (plus Header)
    And jede Zeile soll Rechnungsnummer, Empfaenger, Datum, Betrag enthalten

  # --- Rollback ---

  Scenario: Agent kann per Rollback entfernt werden
    Given der Agent "invoice-scanner" ist registriert (Generation N)
    When ein Rollback auf Generation N-1 ausgefuehrt wird
    Then soll der Agent "invoice-scanner" nicht mehr existieren
    And der Code soll noch im Object Store sein (immutable)
