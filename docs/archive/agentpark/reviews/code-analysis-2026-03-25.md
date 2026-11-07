# Code-Analyse (Stand: 2026-03-25)

## 1) Wo wir stehen

- Das Projekt hat bereits eine **klare Security-/Architektur-Ausrichtung** (Security-first, Config-Generationen, Credential-Isolation) und ist laut README im Status **„Pre-production foundation“**.
- Die Testabdeckung ist breit angelegt (viele Unit-/Integrations-/Security-Tests), und ein selektiver Security-Testlauf war lokal grün (72 passed).
- Gleichzeitig ist die Codebasis bereits deutlich gewachsen (u. a. sehr große Module in Chat, CLI, Proxy), was die Wartbarkeit mittelfristig erschwert.

## 2) Kritische Security-Themen (priorisiert)

### Kritisch A — Sandbox ist explizit **keine** Security Boundary
- `LocalSandbox` ist subprocess-basiert und trägt selbst den Warnhinweis „NOT a security boundary“.
- Risiko: Bei untrusted Agent-Code ist die Isolierung nicht ausreichend (Host/Dateisystem/Netzwerk-Risiken je nach Laufzeitumgebung).
- Konsequenz: Für produktive Ausführung untrusted Code sollte die geplante Container/OS-Isolation priorisiert werden.

### Kritisch B — API-Key-Injektion über `os.environ` im Proxy
- In `/llm/complete` wird der Provider-Key in Prozess-Umgebungsvariablen gesetzt.
- Ich sehe im aktuellen Flow **kein explizites Cleanup** dieser Variablen nach dem Request.
- Risiko: Key-Leakage zwischen Requests/Threads oder unbeabsichtigte Weitergabe an andere Pfade im selben Prozess.

### Hoch C — Tokenvergleich im Proxy ohne `compare_digest`
- Proxy-Auth prüft Bearer-Token via direktem Stringvergleich.
- Risiko: Timing-Angriffe sind hier zwar wegen Unix-Socket/Local-Context kleiner, aber Security-Härtung sollte konsistent erfolgen.

### Hoch D — Credential Bootstrap gibt Raw API Key zurück
- `/credential/bootstrap` liefert `{ "api_key": ... }` an den Caller zurück.
- Zwar zeitlich eingeschränkt (Bootstrap-Fenster), aber der Endpunkt ist sensitiv und sollte enger eingeschränkt/gebunden werden (Scope, einmaliger nonce, optional service allowlist).

## 3) Was wir unbedingt fixen müssen (Must-Fix Backlog)

1. **LLM-Key-Injektion ohne Prozess-Env-Mutation**
   - Statt `os.environ[...]` pro Request lieber explizit Übergabe via Request-Scoped Config/Client-Instanz.
   - Falls technisch nicht vermeidbar: unmittelbares Cleanup + Locking + harte Tests auf Non-Leakage.

2. **Produktionsfähige Sandbox-Isolation priorisieren**
   - Der README- und Code-Warnhinweis muss in ein reales Delivery-Item (Container-Isolation, Syscall-/Netzwerk-/FS-Policy) übersetzt werden.

3. **Proxy-Tokenvergleich auf constant-time umstellen**
   - Einheitlich `hmac.compare_digest` nutzen (wie bereits an anderer Stelle im Projektansatz vorgesehen).

4. **Credential-Bootstrap enger härten**
   - Zusätzliche Prüfungen: bind an spezifischen Startup-Context, explizite Allowlist, One-Time Challenge/Nonce, detaillierteres Auditing.

## 4) Wo wir vereinfachen können

- **Monolithische Dateien aufteilen**
  - `chat/service.py` (~2454 Zeilen), `chat/slash_commands.py` (~1366), `security/proxy_server.py` (~800), mehrere große CLI-Commands.
  - Vorschlag: vertikale Schnitte nach Domänen (routing, permissions, tool-exec, streaming, telemetry).

- **Security/HTTP-Logik zentralisieren**
  - SSRF-Validierung liegt aktuell in zwei Modulen nahezu identisch vor (Proxy + HTTP-Tools).
  - Ein gemeinsames Security-Modul reduziert Drift-Risiko.

## 5) Wo wir Duplikate haben

1. **SSRF-Validation ist doppelt implementiert**
   - `src/mycelos/security/proxy_server.py`
   - `src/mycelos/connectors/http_tools.py`
   - Beide enthalten praktisch dieselbe `_validate_url`-Logik (Hosts, private IPs, DNS-Resolution).

2. **Parallele HTTP-Request-Pfade mit ähnlichem Verhalten**
   - Direkte `http_get/http_post`-Tools plus Proxy-`/http`-Pfad führen zu doppelter Request-/Error-/Response-Capping-Logik.
   - Empfehlung: Single Path (Tool -> Proxy) + gemeinsame Response-Normalisierung.

## 6) Kurzfazit

Die Grundlage ist stark (Architektur, Security-Intent, Testkultur), aber es gibt ein paar **echte Härtungslücken**, bevor man von produktionsreifer Security sprechen kann: insbesondere Sandbox-Isolation, request-scope secret handling im Proxy und konsistente Auth-Härtung.
