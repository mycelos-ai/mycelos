Feature: Filesystem Mounts — Scoped Directory Access for Agents
  Als Mycelos-Benutzer moechte ich bestimmte Ordner fuer Agents freigeben,
  damit sie Dateien lesen und schreiben koennen — sicher und kontrolliert.

  Background:
    Given Mycelos ist initialisiert
    And der Ordner "~/Documents/Invoices" existiert
    And der Ordner "~/Documents/Summaries" existiert

  # --- Mount erstellen ---

  Scenario: User gibt Ordner zum Lesen frei
    When der User "/mount add ~/Documents/Invoices --read" eingibt
    Then soll ein Mount mit access "read" erstellt werden
    And eine neue Config Generation soll erstellt werden

  Scenario: User gibt Ordner zum Schreiben frei
    When der User "/mount add ~/Documents/Summaries --write" eingibt
    Then soll ein Mount mit access "write" erstellt werden

  Scenario: User gibt Ordner fuer bestimmten Agent frei
    When der User "/mount add ~/Downloads --read --agent invoice-scanner" eingibt
    Then soll der Mount NUR fuer agent "invoice-scanner" gelten

  Scenario: User gibt Ordner fuer bestimmten Workflow frei
    When der User "/mount add ~/Output --write --workflow pdf-summarizer" eingibt
    Then soll der Mount NUR fuer workflow "pdf-summarizer" gelten

  # --- Mount verwalten ---

  Scenario: User listet Mounts
    Given ein Mount auf "~/Documents" mit access "read" existiert
    When der User "/mount list" eingibt
    Then soll der Mount mit Pfad, Access und Scope angezeigt werden

  Scenario: User entfernt Mount
    Given ein Mount auf "~/Documents" existiert
    When der User "/mount revoke <mount-id>" eingibt
    Then soll der Mount entfernt werden
    And eine neue Config Generation soll erstellt werden

  # --- Agent nutzt Filesystem-Tools ---

  Scenario: Agent liest Datei aus gemountem Ordner
    Given ein Mount auf "~/Documents/Invoices" mit access "read" existiert
    And der Agent "invoice-scanner" hat capability "filesystem.read"
    When der Agent filesystem.read aufruft mit mount "~/Documents/Invoices"
    Then soll der Datei-Inhalt zurueckgegeben werden

  Scenario: Agent schreibt Datei in gemounten Ordner
    Given ein Mount auf "~/Documents/Summaries" mit access "write" existiert
    And der Agent hat capability "filesystem.write"
    When der Agent filesystem.write aufruft
    Then soll die Datei im Ordner erstellt werden

  Scenario: Agent versucht ausserhalb des Mounts zu lesen
    Given ein Mount auf "~/Documents/Invoices" existiert
    When der Agent versucht "~/Desktop/secret.txt" zu lesen
    Then soll der Zugriff verweigert werden
    And ein Audit-Event "filesystem.access_denied" soll geloggt werden

  Scenario: Agent versucht in Read-Only Mount zu schreiben
    Given ein Mount auf "~/Documents/Invoices" mit access "read" existiert
    When der Agent versucht eine Datei dort zu schreiben
    Then soll der Zugriff verweigert werden

  Scenario: Agent ohne Filesystem-Capability versucht Dateizugriff
    Given ein Mount existiert
    And der Agent hat NICHT die capability "filesystem.read"
    When der Agent filesystem.read aufruft
    Then soll der Zugriff verweigert werden

  # --- NixOS State ---

  Scenario: Mounts sind im NixOS State
    Given ein Mount existiert
    When ein Config Snapshot erstellt wird
    Then soll der Mount im Snapshot enthalten sein

  Scenario: Rollback stellt Mounts wieder her
    Given Mount A existiert (Generation 2)
    When Mount B hinzugefuegt wird (Generation 3)
    And ein Rollback auf Generation 2 ausgefuehrt wird
    Then soll Mount A existieren
    And Mount B soll NICHT existieren

  # --- Chat-Integration ---

  Scenario: LLM schlaegt Mount vor wenn noetig
    Given der User schreibt "Scanne meinen Rechnungsordner"
    When Mycelos erkennt dass Dateizugriff noetig ist
    Then soll Mycelos antworten:
      "Dafuer brauche ich Zugriff auf deinen Rechnungsordner.
       Gib ihn frei mit: /mount add ~/Documents/Invoices --read"
