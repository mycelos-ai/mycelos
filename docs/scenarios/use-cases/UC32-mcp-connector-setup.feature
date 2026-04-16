Feature: MCP Connector Setup — One-Click Integration
  Als Mycelos-Benutzer moechte ich externe Services (GitHub, Slack, etc.)
  mit einem einzigen Befehl anbinden koennen, ohne technische Details
  zu kennen.

  Background:
    Given Mycelos ist initialisiert
    And Node.js (npx) ist verfuegbar

  # --- Rezept-basiertes Setup ---

  Scenario: User listet verfuegbare Connectors
    When der User "/connector list" eingibt
    Then soll eine Liste aller verfuegbaren Connectors erscheinen
    And jeder Eintrag soll Name, Kategorie und Status zeigen
    And bereits konfigurierte sollen als "active" markiert sein

  Scenario: User richtet GitHub via Slash-Command ein
    When der User "/connector add github" eingibt
    Then soll das System erklaeren was GitHub kann
    And soll nach dem "GitHub Personal Access Token" fragen
    And soll erklaeren wie man den Token erstellt:
      "Erstelle einen Token auf https://github.com/settings/tokens
       Benoetigte Scopes: repo, issues"
    When der User den Token eingibt
    Then soll der Token verschluesselt im Credential Proxy gespeichert werden
    And der MCP Server soll gestartet und getestet werden
    And die verfuegbaren Tools sollen entdeckt werden
    And der Connector soll als "active" registriert werden
    And eine neue Config Generation soll erstellt werden

  Scenario: User richtet Brave Search ein (mit API Key)
    When der User "/connector add brave-search" eingibt
    Then soll erklaert werden wie man einen API Key bekommt:
      "Hole einen kostenlosen Key auf https://brave.com/search/api/
       (2000 Abfragen/Monat kostenlos)"
    When der User den Key eingibt
    Then soll der Connector konfiguriert und getestet werden

  Scenario: User richtet Fetch ein (kein Key noetig)
    When der User "/connector add fetch" eingibt
    Then soll der Connector sofort aktiviert werden ohne Key-Abfrage
    And die Meldung "HTTP Fetch ist bereit — kein API Key noetig" erscheinen

  # --- LLM-gestuetztes Setup im Chat ---

  Scenario: User fragt im Chat nach Integration
    Given der User schreibt "Ich moechte mein GitHub verbinden"
    When Mycelos die Anfrage verarbeitet
    Then soll Mycelos antworten:
      "Klar! Richte GitHub ein mit: /connector add github"
    And soll erklaeren was GitHub-Integration ermoeglicht

  Scenario: User fragt nach unbekanntem Service
    Given der User schreibt "Ich will Notion anbinden"
    When Mycelos die Anfrage verarbeitet
    And kein vordefiniertes Rezept fuer "Notion" existiert
    Then soll Mycelos vorschlagen nach einem MCP Server zu suchen
    And soll erklaeren: "Fuer Notion gibt es einen Community MCP Server.
      Installiere ihn mit: /connector add-custom notion npx @sirodrigo/mcp-notion"

  # --- Security ---

  Scenario: Credentials sind isoliert pro Connector
    Given GitHub und Brave Search sind konfiguriert
    When der GitHub MCP Server gestartet wird
    Then soll er NUR den GITHUB_TOKEN sehen
    And NICHT den BRAVE_API_KEY

  Scenario: Tools sind scoped pro Agent
    Given GitHub ist konfiguriert mit 15 Tools
    And der news-agent hat NUR "brave_search" als Capability
    When der news-agent ausgefuehrt wird
    Then sollen die GitHub Tools NICHT im LLM-Prompt erscheinen

  # --- Fehlerbehandlung ---

  Scenario: Node.js ist nicht installiert
    Given Node.js (npx) ist NICHT verfuegbar
    When der User "/connector add github" eingibt
    Then soll die Meldung erscheinen:
      "Node.js wird benoetigt fuer MCP Connectors.
       Installiere es mit: brew install node (macOS)
       oder: https://nodejs.org/en/download"

  Scenario: MCP Server startet nicht
    Given der User hat einen ungueltigen GitHub Token eingegeben
    When der MCP Server getestet wird
    Then soll die Meldung erscheinen:
      "Verbindung fehlgeschlagen. Pruefe deinen Token."
    And der Connector soll NICHT als active registriert werden

  Scenario: Connector entfernen
    Given GitHub ist konfiguriert
    When der User "/connector remove github" eingibt
    Then soll der Connector deaktiviert werden
    And die Capabilities sollen entfernt werden
    And eine neue Config Generation soll erstellt werden
    And der Token soll im Credential Proxy bleiben (fuer spaetere Reaktivierung)
