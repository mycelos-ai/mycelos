# Code-Audit: Top 10 Probleme + 10 Vereinfachungen (DRY)

Datum: 2026-03-24

## Top 10 Probleme im aktuellen Code

1. **Fehlende SSRF-Schutzlogik in `http_get`/`http_post`**  
   Die HTTP-Tools erlauben beliebige URLs inklusive Redirects ohne Host/Schema-Allowlist. In einem Agent-System ist das ein Sicherheitsrisiko (interne Metadaten-Endpoints, lokale Dienste, etc.).

2. **Inkonsequente Fehlerbehandlung (stilles Schlucken von Exceptions)**  
   Mehrere Module verwenden breite `except Exception`-Blöcke ohne strukturiertes Error-Mapping oder Re-Raise. Das erschwert Debugging und kann Sicherheits-/Stabilitätsprobleme maskieren.

3. **SQLite-Verbindung wird thread-übergreifend geteilt (`check_same_thread=False`) ohne Locking**  
   Das kann bei parallelen Zugriffen zu Race Conditions oder schwer reproduzierbaren Datenbankfehlern führen.

4. **Jeder `execute`-Aufruf macht sofort `commit`**  
   Das verhindert sinnvolle Transaktionsgrenzen, kostet Performance und erschwert atomare Mehrschritt-Operationen.

5. **MCP Event-Loop-Thread wird gestartet, aber nicht sauber beendet**  
   `MCPConnectorManager` startet eine dedizierte Event-Loop, bietet aber kein kontrolliertes `shutdown` für Loop + Thread-Lifecycle.

6. **Globaler Timeout für alle MCP-Calls (`future.result(timeout=30)`)**  
   Ein fixer 30s-Timeout ist für Discovery, große Tool-Operationen oder langsame Netzwerke zu starr und führt zu unnötigen Fehlern.

7. **`ToolRegistry.register` überschreibt bestehende Tools stillschweigend**  
   Namenskollisionen werden nicht erkannt. Das kann ungewollt Handler austauschen und macht Fehler spät sichtbar.

8. **Inkonsistente Fehler-Semantik in Tool-Layern**  
   Teilweise werden Exceptions geworfen (`ToolRegistry.call`), teilweise Fehlerobjekte zurückgegeben (`MCPConnectorManager.call_tool`, Search-Tools). Das macht Calling-Code unnötig komplex.

9. **Starke Kopplung an interne/private Attribute**  
   Telegram-Channel greift auf `_chat_service._app` und `_chat_service._app.session_store` zu. Das bricht Kapselung und macht Refactoring fragil.

10. **O(n)-Session-Recovery pro neuem Telegram-User**  
    `_get_session` lädt alle Sessions und sucht linear. Das skaliert bei vielen Sessions schlecht und gehört in eine gezielte Store-Abfrage.

## Top 10 Vereinfachungen / DRY-Verletzungen

1. **Google-Tool Funktionen: duplizierte Account-Argument-Logik**  
   Viele Funktionen bauen `args` und prefixen optional `--account` identisch. Das kann in einen kleinen Builder extrahiert werden.

2. **HTTP-Connector: duplizierte Response-/Error-Formate in GET/POST**  
   Response-Normalisierung und Exception-Mapping sind nahezu gleich und sollten in Hilfsfunktionen zusammengeführt werden.

3. **Search-Tools: gleiches Muster für Import, Execute, Normalize, Error**  
   `search_web` und `search_news` haben fast identische Struktur; ein gemeinsamer Helper reduziert Redundanz.

4. **Tool-Registrierung: repetitive `registry.register(ToolDefinition(...))`-Blöcke**  
   Statt vieler nahezu gleicher Blöcke kann eine deklarative Liste iteriert werden.

5. **Logging-Setup in Gateway: doppelte Initialisierung für debug/non-debug**  
   FileHandler/Formatter-Konfiguration ist doppelt und kann in einen gemeinsamen Pfad mit Parametern.

6. **Telegram `_render_events`: lange if/elif-Kette**  
   Event→Renderer-Mapping kann als Dispatch-Tabelle implementiert werden (klarer, testbarer, erweiterbar).

7. **Telegram `_safe_answer`: lineare Retry-Kaskade mit wiederholtem `message.answer`**  
   Strategien als Liste von Render-/Send-Funktionen implementieren, dann generisch iterieren.

8. **ConnectorRegistry: Capability-Lookup in `get` und `list_connectors` dupliziert**  
   Gemeinsamen Helper `_load_capabilities(connector_id)` extrahieren.

9. **Schema-Migrationslogik als Inline-Liste in `_ensure_schema`**  
   Migrationen sollten zentral als Datenstruktur/Runner organisiert sein (bessere Wartbarkeit, weniger Copy/Paste bei neuen Spalten).

10. **MCP `connect`/`discover`/tool-Metadaten-Normalisierung verteilt**  
    Die Normalisierung der Tool-Metadaten sollte in eine dedizierte Funktion, um doppelte Feldbehandlung zu vermeiden.

## Referenzdateien (Basis des Audits)

- `src/mycelos/connectors/http_tools.py`
- `src/mycelos/connectors/search_tools.py`
- `src/mycelos/connectors/google_tools.py`
- `src/mycelos/connectors/registry.py`
- `src/mycelos/connectors/connector_registry.py`
- `src/mycelos/connectors/mcp_manager.py`
- `src/mycelos/execution/tools.py`
- `src/mycelos/storage/database.py`
- `src/mycelos/gateway/server.py`
- `src/mycelos/channels/telegram.py`
