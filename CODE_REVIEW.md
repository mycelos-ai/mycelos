# Code-Review mycelos

Kompromissloser Architektur- und Security-Review des „security-first agent
operating system" (Python 3.12, FastAPI + LiteLLM + Huey + aiogram, SQLite +
sqlite-vec, zwei Frontends). Alle Funde sind am Code verifiziert (Datei:Zeile),
sortiert nach Kritikalität.

> Stand: 2026-07-01, Branch `claude/source-code-review-doq7sj`.

## Executive Summary

Das Projekt beansprucht „security-first". Gemessen an diesem Anspruch gibt es
mehrere gravierende Lücken, die genau die versprochenen Garantien aushebeln:

1. **Container-Isolation durchbrochen.** Der privilegierte Proxy (hält
   `MYCELOS_MASTER_KEY`) führt beliebige Kommandos aus, die das Gateway ihm
   schickt — ohne eigene Allowlist (H1). Das Gateway ist per Default
   unauthentifiziert (H2). Zusammen: ein prompt-injiziertes/kompromittiertes
   Gateway → RCE im Master-Key-Container → Klartext aller Credentials.
2. **LLM-Code läuft vor dem Security-Audit** (KRITISCH-1): Die Creator-Pipeline
   führt generierten Agent-Code in einer Nicht-Sandbox aus, **bevor** der
   Auditor ihn je sieht → beliebige Codeausführung auf dem Host beim Anlegen
   eines Agents.
3. **Stored/Reflected XSS im Haupt-Frontend** über unsanitisiertes
   `marked.parse()` mit LLM-Inhalten (Frontend-K1..K3) — im authentifizierten
   Kontext, der Credentials-/Admin-APIs erreicht.
4. **GDPR-Fail-open:** Der EU-Residency-Zwang fehlt im Streaming-Pfad (H-LLM-2),
   Nutzerdaten können trotz EU-Mode an US-Provider gehen.
5. **Cross-Request-State-Races** in `ChatService` und `LiteLLMBroker` (beides
   prozessweite Singletons mit mutablem Instanzzustand) → Tool-Calls/Kosten/
   Purpose gegen die falsche Session/User.

---

## KRITISCH

### C1 — LLM-generierter Code wird VOR dem Security-Audit ausgeführt
`src/mycelos/agents/creator_pipeline.py:207-317`, `src/mycelos/agents/test_runner.py:143-233`, `src/mycelos/execution/agent_runner.py`

Pipeline-Reihenfolge: `generate_code` (Z. 211) → `run_agent_tests` (Z. 233,
importiert & führt `agent_code.py` via pytest aus) → `audit` (Z. 304-306).
Damit läuft ungeprüfter, LLM-generierter (und über Prompt-Injection aus
importierten Inhalten beeinflussbarer) Code, **bevor** der AuditorAgent ihn
sieht. Der „Sandbox"-Subprozess ist explizit kein Sicherheitsboundary
(`test_runner._safe_env` behält `PATH`/`PYTHONPATH`, voller Netzwerkzugriff —
im Kommentar eingeräumt). → Beliebige Codeausführung auf dem Gateway-Host beim
Anlegen eines Agents.

**Fix:** Audit **vor** jeder Codeausführung; Tests erst nach bestandenem Audit;
echte Sandbox (Subprozess ohne Netz, minimales Env, Ressourcenlimits) für die
Testausführung.

### C2 — Stored/Reflected XSS: unsanitisiertes `marked.parse()` in `x-html`
`src/mycelos/frontend/pages/chat.html:486,496,937-1028`, `knowledge.html:1232,1369-1409`, `workflows.html:628,892-900`, `docs.html:246,263-269`

`marked.parse()` sanitisiert seit v4 nicht mehr; die Ausgabe wird per Alpine
`x-html` (=`innerHTML`) injiziert. Ein LLM, das rohes HTML ausgibt (z. B. via
Prompt-Injection aus einer gelesenen Mail/Webseite:
`<img src=x onerror=fetch('/api/credentials')>`), führt beliebiges JS im
authentifizierten, same-origin Kontext aus — inkl. Zugriff auf Credentials-/
Admin-APIs. Verstärkt durch **K2** (Mermaid-Pfad `chat.html:1016-1023`,
`knowledge.html:1396-1403`: escapter Code-Fence-Inhalt wird zurück-dekodiert und
roh gesetzt) und **K3** (`chat.html:946-974`: `before`/`after` im
`[action]`-Fallback unescaped).

**Fix:** `DOMPurify.sanitize(marked.parse(text))` an allen Stellen (eine
gemeinsame Shared-Funktion); Mermaid-Inhalt via `textContent` statt
String-Templating.

### C3 — Cross-Request-Race in ChatService (geteilter Zustand über alle Sessions/User)
`src/mycelos/chat/service.py:326-327,624,1851,1795-1797,1904-1912,2277`

Genau eine `ChatService`-Instanz (`gateway/server.py:601`). `handle_message()`
läuft pro Request im Executor-Thread, setzt aber Instanz-Attribute
(`self._current_session_id`, `self._current_user_id`), die während der
Tool-Ausführung gelesen werden. Bei zwei gleichzeitigen Requests (Web +
Telegram + Test-Runner) laufen Tool-Aufrufe gegen die **falsche
Session/User**: Agent-Allowlist-Prüfung, `request_action`-Bestätigungen,
`connector_call` mit fremder `user_id`. `_pending_permission` ist ein einzelner
globaler Slot → ein zweiter Prompt überschreibt den ersten kommentarlos.

**Fix:** Request-Kontext (session_id, user_id, pending-Strukturen) als
Kontextobjekt durchreichen statt auf `self`; `_pending_permission` als
`dict[session_id, …]`.

---

## HOCH

### H1 — Proxy führt beliebige Kommandos vom Gateway aus → RCE in den Master-Key-Container
`src/mycelos/security/proxy_server.py:498-544`, `gateway/routers/connectors.py:379-624`, `chat/slash_commands.py:743-784`, `cli/serve_cmd.py:83`

Die Architektur begründet sich damit, dass nur der Proxy privilegiert ist
(`MYCELOS_MASTER_KEY` in seiner Umgebung). `/mcp/start` nimmt aber
`command: list[str]` und reicht es **ungeprüft** an `mcp.connect(command=...)`
(verifiziert: `command=req.command`, keine Allowlist auf Proxy-Seite). Die
einzige Validierung läuft im *Gateway*. Ein kompromittiertes/prompt-injiziertes
Gateway (oder ein authentifizierter `POST /api/connectors`) startet beliebige
argv mit `MYCELOS_MASTER_KEY` im Env + Zugriff auf die entschlüsselte DB →
sofortige Kompromittierung aller Klartext-Credentials. Zudem ist die
gateway-seitige Allowlist schwach: `python`/`node`/`deno`/`docker`/`bun` erlaubt
→ `python -c "…"`, `node -e "…"`, `docker run -v /:/host` = trivialer
Codeausführung trotz Metazeichen-Filter.

**Fix:** Kommando-Allowlist **im Proxy** erzwingen (Proxy darf dem Gateway nicht
vertrauen); erlaubte Executables auf echte MCP-Launcher ohne `-c`/`-e`-Inline-Code
beschränken bzw. auf feste Recipe-Kommandos festnageln.

### H2 — Gateway standardmäßig ohne Authentifizierung
`gateway/server.py:369-482`, `gateway/auth.py:128`, `docker-compose.yml:81`, `Dockerfile:70`

Auth-Middleware wird nur installiert, wenn ein Passwort gesetzt ist. Im
Default-Docker-Setup ist `MYCELOS_PASSWORD` leer, das Gateway bindet auf
`0.0.0.0` mit `--allow-insecure-bind`. `LocalhostMiddleware` greift nur bei
`127.0.0.1`/`::1`, bei `0.0.0.0` also **gar nicht**. Einziger Schutz ist das
Port-Mapping. Jeder lokale Prozess erreicht die vollständige mutierende API ohne
Auth — inkl. Connector-Registrierung (H1), Credential-Anlage, Config-Rollback.

**Fix:** Default fail-closed: kein Passwort → Betrieb nur mit Localhost-Bind und
sichtbarer Warnung; bei Nicht-Localhost-Bind Passwort/TLS erzwingen.

### H3 — EU-Mode-Enforcement fehlt im Streaming-Pfad (GDPR fail-open)
`src/mycelos/llm/broker.py:374-476`

`complete()` erzwingt EU-Residency (`_eu_mode_check` + `filter_eu_models`,
fail-closed). `complete_stream()` enthält diese Prüfung **nicht** → geht direkt
an `litellm.completion`/Proxy. Jeder gestreamte Chat-Request umgeht die
„fail-closed"-Garantie und kann Nutzerdaten an US-Provider senden, obwohl
EU-Mode aktiv ist.

**Fix:** Dieselbe `filter_eu_models`/`EUResidencyError`-Logik am Anfang von
`complete_stream`.

### H4 — `GET /api/config` gibt verschlüsselte Credential-Blobs + Nonces preis
`gateway/routers/config.py:15-19`, `config/state_manager.py:344-362`

`/api/config` liefert `snapshot()` direkt zurück, inkl. `credentials`-Sektion
mit base64-Ciphertext **und Nonce** je Service. Jeder API-Aufrufer (Default:
unauthentifiziert über localhost) erhält das komplette verschlüsselte
Schlüsselmaterial samt Nonces — unnötige Angriffsfläche (Offline-Angriffe bei
schwachem Master-Key, Exfiltration vor späterem Key-Leak).

**Fix:** `credentials` aus dem über die API zurückgegebenen Snapshot filtern.

### H5 — Race Condition: doppelte Ausführung geplanter Workflows
`src/mycelos/scheduler/jobs.py:284-361`, `src/mycelos/scheduler/schedule_manager.py:146-167`

Periodischer Task (alle 5 min, `workers=2`). `get_due_tasks()` liest fällige
Tasks; `mark_executed()` wird erst **nach** vollständiger Workflow-Ausführung
(bis 20 LLM-Runden) aufgerufen. Läuft ein langer Workflow noch, während die
nächste Instanz startet, liefert `get_due_tasks()` denselben Task erneut →
doppelte, gleichzeitige Ausführung. Kein atomarer Claim.

**Fix:** Task atomar reservieren (bedingtes `UPDATE … WHERE next_run<=now AND
status='pending'`) vor der Ausführung. Gleiches Muster im
Reminder-Tick prüfen (`jobs.py:17-37`).

### H6 — Geteilter, ungeschützter Zustand im LLM-Broker (Kosten/Purpose/Stream-Races)
`src/mycelos/llm/broker.py:139-146,257-258,322-337,390-393,423-458`

`LiteLLMBroker` ist ein prozessweiter Singleton (`app.llm`), genutzt von
Huey-Workern, Hintergrund-Threads und Chat. Ohne Lock mutiert: `self.total_tokens`
/`total_cost` (`+=` → verlorene Updates); `self._current_purpose` (vom Aufrufer
gesetzt, in `_complete_single` gelesen → falsche Purpose-Zuordnung);
`self._last_stream_tokens/_model/_tool_calls` (auf geteilter Instanz gesetzt und
nach Iteration gelesen → parallele Streams überschreiben sich).

**Fix:** Per-Call-State statt Instanz-Attribute; Kosten über Lock/atomar.

### H7 — Fehlerhafte Vektorsuche-Query → semantische Suche faktisch tot
`src/mycelos/knowledge/service.py:1096-1118`

`WHERE embedding MATCH ? AND k = ?` steht in einem JOIN
(`knowledge_vec v JOIN knowledge_notes kn`); die sqlite-vec-KNN-Syntax gehört auf
die reine vec0-Tabelle bzw. eine KNN-Subquery. In einem Join wirft das je nach
Version `OperationalError`, der von `except Exception` (Z. 1118) verschluckt
wird → **jede** Vektorsuche liefert stumm `[]` und fällt auf FTS zurück.
`find_duplicates` (das FTS-Fallback verbietet) findet damit **nie** Duplikate.

**Fix:** KNN als Subquery (`rowid, distance` aus `knowledge_vec`, dann Join);
gegen die installierte sqlite-vec-Version verifizieren; Fehler nicht
verschlucken (mind. `logger.warning`).

### H8 — Priority-Boost verfälscht Cosine-Ranking, hebelt Duplikat-Threshold aus
`src/mycelos/knowledge/service.py:1110-1113`

```python
score = 1.0 - r.get("distance", 1.0)
score += r.get("priority", 0) * 0.05   # VOR dem Threshold
if score >= threshold: ...
```

Eine Notiz mit `priority=4` bekommt +0.20 → passiert die Duplikat-Schwelle
(0.92) schon bei echter Ähnlichkeit 0.72 → False-Positive-Merges semantisch nur
mäßig ähnlicher Notizen (Datenverlust — genau das, was der Kommentar vermeiden
will).

**Fix:** Boost nur in `find_relevant`, nie im geteilten
`_find_relevant_by_vector`, und nie vor der Threshold-Prüfung bei Duplikaten.

### H9 — Dimensionswechsel dropt Vektor-Tabelle ohne Re-Embedding → dauerhafter Verlust
`src/mycelos/knowledge/service.py:162-179`

Wechselt der Embedding-Provider die Dimension (lokal 384 ↔ OpenAI 1536, je nach
Import-Erfolg/EU-Mode/Proxy-Präsenz), wird `knowledge_vec` gedroppt und leer
neu erzeugt — **ohne** Re-Embedding-Lauf. Alle Notizen verlieren ihre Vektoren,
bis sie zufällig einzeln editiert werden; da die Provider-Wahl laufzeitabhängig
oszillieren kann, bleibt die Vektorsuche praktisch dauerhaft leer.

**Fix:** Nach Dimensionswechsel Reindex-Trigger (alle Notizen neu embedden).

### H10 — Klartext-Credentials landen als Chatnachricht in der Session-Historie
`src/mycelos/frontend/pages/chat.html:2299-2305,2162-2171`

`submitConnectorForm`/`executeCommandDialog` senden Secrets (E-Mail-Passwort/
Bot-Token/API-Key) als normale User-Message `/connector add …` / `/credential
store …` durch die Chat-Pipeline → gespeichert in der Session, abrufbar über
`/api/sessions/{id}/messages` und Session-Download, ggf. im LLM-Kontext. Das
widerspricht direkt der UI-Zusage „Encrypted locally — never sent to the AI".

**Fix:** Direkt `POST /api/credentials`/`POST /api/connectors` aufrufen (wie
`connectors.html` es korrekt tut), nicht den Chat-Kanal.

### H11 — CDN-Skripte ohne Version-Pinning und ohne SRI
Alle `src/mycelos/frontend/pages/*.html` (z. B. `chat.html:11,17,167`)

`cdn.tailwindcss.com`, `cdn.jsdelivr.net/npm/marked/marked.min.js` (**latest!**),
`alpinejs@3.x.x` ohne `integrity`/`crossorigin`. Ein kompromittiertes/geändertes
CDN-Paket = volle Codeausführung in einer Credentials-verwaltenden, teils
netzwerk-exponierten App. `marked` ohne Version kann sich zudem inkompatibel
ändern.

**Fix:** Versionen pinnen + SRI; besser Assets bundlen/vendoring (wie bei
`mermaid.min.js` bereits geschehen).

### H12 — Widgets/Streaming im eingebetteten Chat tot; React-Approval-Widgets melden Fake-Erfolg
`src/mycelos/frontend/pages/chat.html:1684-1692,416-524`, `frontend/components/widgets/action-confirm.tsx:11-32`, `widget-renderer.tsx:30`, `choice-box.tsx`, `confirm-dialog.tsx`

Das eingebettete Frontend pusht Widget-Events, rendert sie aber nie (Loop kennt
nur `user/assistant/system/step/actions`) → interaktive Rückfragen des Agenten
unsichtbar; kein `text-delta`-Handler → Token-Streaming verworfen. Im
Next.js-Frontend hat `action-confirm` kein `onAction` verdrahtet: Klick auf
„Execute" zeigt „Executed: <command>" ohne Backend-Call — **sicherheitsrelevant
irreführend** für ein Approval-Widget. `choice-box`/`confirm-dialog`-Buttons
ohne `onClick`.

---

## MITTEL

- **M1 — TOCTOU/DNS-Rebinding umgeht SSRF-Schutz.** `security/ssrf.py:52-71`,
  `proxy_server.py:435,470`, `connectors/http_tools.py:50-53,88`: `validate_url()`
  löst DNS auf und prüft IPs, danach löst `httpx` **erneut** auf → Rebinding auf
  `169.254.169.254`/intern. Fix: an aufgelöste IP pinnen.
- **M2 — AES-GCM ohne Associated Data.** `security/credentials.py:43-53`: AAD
  `None` → Blobs zwischen Services vertauschbar (DB-Schreibzugriff vorausgesetzt).
  Fix: `f"{user_id}:{service}:{label}"` als AAD.
- **M3 — Zip-Bomb beim Knowledge-Import.** `gateway/routers/knowledge.py:601-615`:
  `zf.read(name)` über alle Einträge ohne Größen-/Anzahllimit, kein Upload-Limit.
  Fix: kumulative entpackte Größe + `ZipInfo.file_size` deckeln.
- **M4 — `X-User-Id`-Header ungeprüft als Identität.** `gateway/routers/_helpers.py:92-97`:
  Aufrufer kann beliebige `user_id` vorgeben (Credentials/Memory/Notes scoped) →
  untergräbt jede Mandantentrennung.
- **M5 — Config-Rollback macht „never"-Policies rückgängig.** `config.py:72-87`,
  `state_manager.py:56-204`: nur `security_rotated`-Credentials geschützt, Policies
  nicht → Rollback hebt Sicherheits-Policy auf. Fix: „never"-Policies rollback-fest.
- **M6 — `_validate_mcp_command` prüft nur `parts[0]`.** `slash_commands.py:743-784`:
  `python -c`, `node -e`, `docker run --privileged -v /:/host` frei über Argumente
  (siehe H1). „Secure commands" ist damit große Restfläche.
- **M7 — Verwaiste Kindprozesse bei Subprozess-Timeout.** `execution/sandbox.py:107-129`,
  `test_runner.py:184-224`, `agent_runner.py:101-134`: `subprocess.run(timeout=…)`
  killt nur den direkten Prozess. Fix: `start_new_session=True` + `os.killpg`.
- **M8 — `run_agent_code` nutzt Denylist-Env statt Allowlist.** `agent_runner.py:137-150`:
  Env ohne Denylist-Wort (`GH_PAT`, `OPENAI`, `DATABASE_URL`, `STRIPE_SK`) leakt in
  den Subprozess. Fix: Allowlist wie im Test-Runner.
- **M9 — `filesystem_write` prüft unaufgelösten Pfad.** `tools/filesystem.py:242-288`:
  `_is_sensitive_path(args["path"])` vor `.resolve()` → Symlink
  (`notes.txt → ~/.ssh/authorized_keys`) umgeht den Filter. Fix: erst auflösen,
  dann prüfen (wie im Read-Pfad).
- **M10 — Path-Traversal-Restfläche in `store_document`.** `knowledge/service.py:389-408`:
  kein `relative_to`-Containment nach `resolve()` (nur `sanitize_filename`).
  Fix: konsistent über `_safe_path`-Logik.
- **M11 — `store()`/`register()`/`write()` nicht atomar.** `connector_registry.py:24-34`,
  `knowledge/service.write`, `object_store.py:26-28`: Multi-Schritt-Schreibpfade
  ohne Transaktion → Teilschreibvorgänge (Connector ohne Capabilities; `.md` ohne
  DB-Zeile; korrupte Objektdatei unter Hash-Namen, die `exists()` künftig blockt).
  Fix: `storage.transaction()`; `object_store` per tmp-Datei + `os.replace`.
- **M12 — Budget wird nie durchgesetzt.** `workflows/agent.py:199-386`,
  `jobs.py:290-332`: `check_budget` existiert (`run_manager.py:233`), wird aber nie
  aufgerufen; `execute()` nimmt gar keinen Budget-Parameter → `budget_per_run` ist
  Deko, ein Workflow verbrennt bis `max_rounds=20` volle Kosten.
- **M13 — Model-Sync setzt Nutzer-Status zurück.** `model_registry.py:42-67,268-276`:
  `sync_from_litellm` ruft `add_model` ohne `status` → `INSERT OR REPLACE` mit
  Default `available` reaktiviert täglich deaktivierte Modelle. Fix: `COALESCE`/`UPDATE`.
- **M14 — Cron-Parser falsch.** `schedule_manager.py:36-72`: day-of-month/weekday
  mit AND statt Standard-OR; `7`=Sonntag matcht nie (`isoweekday()%7`); 48h-Fallback
  behält Sekunden. Fix: Standard-Cron-Semantik, `7→0` normalisieren.
- **M15 — Memory-Review re-prüft alles.** `session_summary.py:238-289`: Filter
  `not e.get("_reviewed")` immer wahr (Marker liegt separat unter
  `memory.reviewed.{key}`) → jede stale Session re-prüft alle Einträge per LLM.
- **M16 — KB-Kontext immer leer.** `chat/service.py:777-785` liest `note["content"]`,
  aber Vektor-/FTS-Suche selektiert kein `content` (`service.py:1096-1099`) →
  `content_preview` immer `""`, KB-Enrichment wirkungslos.
- **M17 — Reminder-Dispatch markiert Kanäle falsch.** `knowledge/reminder.py:245-286`:
  Kanäle über alle Tasks aggregiert; Erfolg *irgendeines* Kanals markiert **alle**
  Tasks als `fired` → Task mit nur `telegram` gilt als erledigt, Erinnerung feuert
  nie. Fix: pro Task gegen dessen eigene Kanäle prüfen.
- **M18 — Performance: N+1 & Vollscans.** `knowledge/service.py:990-1017,862-936`:
  `sync_relations` liest bis 5000 Notizen + je Notiz `read()` (Datei-I/O) +
  `get_backlinks`; synchron/blockierend. `find_relevant` mit 5s-Timeout kann beim
  ersten Chat-Turn (Modell-Load) garantiert reißen (`indexer`/`embeddings`).
- **M19 — `due`-Klassifikation inkonsistent.** `knowledge/indexer.py:213-217`:
  lexikografischer Vergleich `due < today` mischt Datetime- und Datums-Granularität
  → heute-fällige Datetimes nicht als overdue.
- **M20 — Kein `schema_version`/Migrations-Framework.** `storage/database.py:44-123`:
  `_ensure_schema` nur beim ersten Connect; ALTER-Migrationen mit `except
  OperationalError: pass` maskieren echte Fehler; keine Reihenfolge-/Idempotenz-
  Garantie über Prozessgrenzen, O(#Migrationen) Fehlversuche pro Connect.

---

## NIEDRIG (Auswahl)

- **N1 — Proxy erlaubt leeren Token.** `proxy_server.py:153,329-348`: `proxy_token=""`
  + `compare_digest(token, "")` → Auth-Bypass bei Fehlkonfiguration. Fail-closed machen.
- **N2 — Session-Cookie ohne `Secure`, kein TLS.** `gateway/auth.py:155-166`: bei
  `MYCELOS_BIND=0.0.0.0`+Passwort laufen Token/Basic-Auth im Klartext durchs LAN.
- **N3 — CORS/CSRF erlauben localhost:3000 in Prod.** `gateway/server.py:457-468`,
  `routes.py:63,139`: `allow_credentials=True` für jede lokale Seite → dev-only machen.
- **N4 — Broker behandelt 401 als retriable.** `broker.py:126,160-163`: Auth-Fehler
  löst Modell-Fallback aus (verschleiert Fehlkonfiguration); Substring-Matching fragil.
- **N5 — Stream-Kosten gemittelt.** `broker.py:469`: `(input+output)/2` mit
  `input=output=0` in der DB-Zeile → grob ungenaues Kostentracking.
- **N6 — LLM-Streaming-Content nicht saniert.** `proxy_server.py:740-770`: nur
  Fehlermeldungen laufen durch den `ResponseSanitizer`, Content-Chunks nicht.
- **N7 — Login-`next`-Redirect nur client-seitig.** `frontend/.../login.html:140,150`:
  ungeprüftes `location.href = d.next` (Open Redirect / `javascript:`). Auf
  same-origin whitelisten.
- **N8 — Fehler im Next.js-Chat verschluckt, kein Abort.** `frontend/lib/use-chat-stream.ts:140-153`:
  leere Bubble ohne Fehlermeldung; `abortRef` bei Unmount nicht abgebrochen →
  `setMessages` nach Unmount. Auch `shared/api.js:152-215`: kein Timeout/Abort.
- **N9 — Performance-Frontend:** ganze Historie ohne Virtualisierung/Paging,
  `marked`/`ReactMarkdown` re-parst alle Nachrichten pro Token
  (`chat.html:486,1618`, `use-chat-stream.ts:123-137`); `MutationObserver` auf
  `document.body`; synchroner XHR in `i18n.js:16-27`; Tailwind-Play-CDN in Prod.
- **N10 — Duplizierte/tote Codepfade.** Zwei Frontends (`frontend/` vs.
  `src/mycelos/frontend/`) mit divergierendem Feature-Support; `renderMarkdown` 3×
  kopiert; Force-Graph 2×; `background_pipeline.py` dupliziert `tasks/pipeline_task.py`;
  `_llm_complete_with_failover`, `_execute_merge`, doppeltes `_split_message`
  (`channels/telegram.py:1050,1324`), dreifaches `_guess_provider` — alle tot/redundant.
- **N11 — Unbegrenzte Pending-Maps.** `channels/telegram.py:44,887`
  (`_pending_permissions` ohne TTL), `chat/service.py` pending-Slots — kleiner
  DoS-/Leak-Vektor.
- **N12 — Weitere Datenpfade:** `_dispatch_chat` einziger `pending_reminder`-Slot
  (`reminder.py:168-178`); `deserialize_embedding` ohne Dimensionsvalidierung
  (`embeddings.py:90-97`); `search_fts` bei `"` im Wort still `[]`
  (`indexer.py:150-171`); `db sql` „read-only" nur per Prefix (`cli/db_cmd.py:283-301`);
  `_try_save_name` zu aggressiv (`chat/service.py:2603-2610`); `parse_frontmatter`
  Split fragil (`note.py:112-117`).

---

## Abhängigkeiten (npm audit, Frontend)

- **next 9.3.4 – 16.3.0-canary (hoch):** zahlreiche DoS/Cache-Poisoning/SSRF/
  XSS-Advisories. **postcss < 8.5.10 (moderate):** XSS via unescaptes `</style>`.
  `npm audit fix --force` (breaking: next@16.2.10).

---

## Positiv verifiziert (keine Findings)

- Timing-sichere Vergleiche durchgehend (`hmac.compare_digest`/`secrets.compare_digest`).
- Path-Traversal beim Attachment-Serving robust (`sessions.py`, `session_attachments.py`:
  `sanitize_filename` + `resolve()`+`relative_to`).
- Capability-Token-Limit atomar gegen TOCTOU (`capabilities.py:94-100`).
- OAuth-Flow mit PKCE + serverseitigem, TTL-begrenztem `state`; Open-Redirect-Schutz
  in `_safe_next` (`auth.py:109-113`).
- Telegram-Allowlist & Webhook-Secret fail-closed.
- AES-GCM-Nonce je Verschlüsselung frisch via `os.urandom(12)` — keine
  Nonce-Wiederverwendung.
- Config-Tamper-Erkennung per Hash-Verifikation (`generations.py:357-383`).
- Aktiver SQL-Code durchgängig parametrisiert — **kein SQL-Injection-Vektor**
  (f-String-Interpolationen nur mit hartkodierten internen Bezeichnern).
- Next.js-Frontend rendert Markdown via `react-markdown` (HTML escaped) — dort
  kein XSS; `oauth_setup.js` räumt OAuth-Query-Params per `replaceState` ab.
