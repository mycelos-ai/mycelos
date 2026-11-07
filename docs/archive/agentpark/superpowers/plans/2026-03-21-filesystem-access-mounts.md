# Filesystem Access: Scoped Mounts fuer Agents und Workflows

> Konzeptioneller Plan — klaert wie Agents und Workflows auf externe Ordner zugreifen.

---

## Das Problem

Agents brauchen manchmal Zugriff auf User-Ordner ausserhalb der Mycelos-Infrastruktur:
- `~/Downloads` lesen (neue Dateien erkennen)
- `~/Documents/Summaries` schreiben (Ergebnisse ablegen)
- `/Volumes/NAS/Photos` lesen (Fotos verarbeiten)

Aber: Kein Agent darf **alles** sehen. Principle of Least Privilege.

---

## Multi-Agent Workflows: Wer hat was?

### Beispiel: PDF Email Summarizer

```
Step 1: email-reader     Capabilities: [google.gmail.read]
                         Mounts: keine
                         Output: Email-Metadata + PDF-URLs

Step 2: pdf-fetcher      Capabilities: [http.get]
                         Mounts: keine
                         Output: PDF-Dateien (in Workflow-Artifacts)

Step 3: summarizer       Capabilities: [llm.complete]
                         Mounts: keine
                         Output: Zusammenfassung (Text)

Step 4: file-writer      Capabilities: [filesystem.write]
                         Mounts: [~/Documents/Summaries (write)]
                         Output: Datei geschrieben
```

Jeder Agent sieht NUR was er braucht. Der Summarizer hat keinen Ordner-Zugriff. Der File-Writer hat keinen Gmail-Zugriff.

### Wie Daten zwischen Steps fliessen

```
Step 1 Output → Workflow Artifacts (Object Store, intern)
Step 2 Input  ← Workflow Artifacts
Step 2 Output → Workflow Artifacts
...
Step 4 Input  ← Workflow Artifacts
Step 4         → Schreibt in gemounteten Ordner
```

**Daten fliessen NICHT direkt zwischen Agents.** Sie gehen immer ueber die Workflow Artifacts (Object Store). Das ist sicher — kein Agent kann heimlich Daten an einen anderen schicken.

---

## Mount-Konzept

### Was ist ein Mount?

Ein Mount ist eine **explizite Freigabe** eines User-Ordners fuer einen bestimmten Agent oder Workflow:

```python
@dataclass
class Mount:
    path: str              # Absoluter Pfad: "~/Documents/Summaries"
    access: str            # "read" | "write" | "read_write"
    purpose: str           # Menschenlesbar: "Zusammenfassungen speichern"
    agent_id: str | None   # Spezifischer Agent, oder None fuer alle im Workflow
    workflow_id: str | None # Spezifischer Workflow
```

### Wer darf Mounts vergeben?

**NUR der User.** Kein Agent kann sich selbst Zugriff geben.

Moeglich ueber:
1. **Chat:** "Der Agent braucht Zugriff auf deinen Summaries-Ordner. Erlaubst du das?"
2. **CLI:** `mycelos mount add ~/Documents/Summaries --write --workflow pdf-summarizer`
3. **Config:** In der Workflow-Definition

### Mount-Hierarchie

```
Globale Mounts (gelten fuer alle Agents/Workflows)
    ↓
Workflow-Mounts (gelten fuer alle Steps in einem Workflow)
    ↓
Agent-Mounts (gelten nur fuer einen spezifischen Agent)
```

Resolution: Agent-spezifisch > Workflow > Global. Ein Agent sieht nur die Mounts die fuer ihn freigegenben sind.

---

## Datenbank-Schema

### `mounts` Tabelle (Teil des NixOS State)

```sql
CREATE TABLE IF NOT EXISTS mounts (
    id          TEXT PRIMARY KEY,
    path        TEXT NOT NULL,              -- Absoluter Pfad
    access      TEXT NOT NULL DEFAULT 'read', -- read, write, read_write
    purpose     TEXT,                       -- Menschenlesbar
    agent_id    TEXT REFERENCES agents(id), -- NULL = alle
    workflow_id TEXT REFERENCES workflows(id), -- NULL = alle
    user_id     TEXT NOT NULL DEFAULT 'default',
    status      TEXT NOT NULL DEFAULT 'active', -- active, revoked
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
```

### Beispiel-Daten

```sql
-- Globaler Read-Zugriff auf Downloads
INSERT INTO mounts (id, path, access, purpose)
VALUES ('m1', '~/Downloads', 'read', 'Neue Dateien erkennen');

-- Workflow-spezifisch: nur pdf-summarizer darf in Summaries schreiben
INSERT INTO mounts (id, path, access, purpose, workflow_id)
VALUES ('m2', '~/Documents/Summaries', 'write', 'Zusammenfassungen', 'pdf-summarizer');

-- Agent-spezifisch: nur file-writer darf schreiben
INSERT INTO mounts (id, path, access, purpose, agent_id)
VALUES ('m3', '~/Documents/Archive', 'write', 'Archivieren', 'file-writer');
```

---

## Sandbox-Integration

### Bei Agent-Ausfuehrung: Mounts werden in die Sandbox eingebunden

```python
def prepare_sandbox(agent_id, workflow_id, sandbox):
    """Resolve mounts for this agent in this workflow and mount them."""
    mounts = resolve_mounts(agent_id, workflow_id)

    for mount in mounts:
        expanded = Path(mount.path).expanduser().resolve()

        if not expanded.exists():
            continue  # Skip non-existent paths

        if mount.access == "read":
            sandbox.mount_readonly(expanded, f"/mnt/{mount.id}")
        elif mount.access in ("write", "read_write"):
            sandbox.mount_readwrite(expanded, f"/mnt/{mount.id}")

    # Agent sees: /mnt/m1, /mnt/m2, etc.
    # Environment: MYCELOS_MOUNT_m1=/mnt/m1, MYCELOS_MOUNT_m2=/mnt/m2
```

### Fuer LocalSandbox (Phase 1): Symlinks statt echte Mounts

Da LocalSandbox kein Docker ist, nutzen wir Symlinks:

```python
# In der Sandbox temp dir:
sandbox_dir/
  input/
  workspace/
  output/
  mounts/
    summaries -> ~/Documents/Summaries  (symlink)
    downloads -> ~/Downloads            (readonly via Permissions? Nein, nur Convention)
```

**Ehrlich:** Ohne Docker koennen wir Read-Only nicht erzwingen. Wir koennen nur:
1. Dem Agent sagen welche Mounts er hat (Environment Variables)
2. Policy-Check bei Tool-Calls (`filesystem.read`, `filesystem.write`)
3. Audit-Log jedes Dateizugriffs

Echte Isolation kommt mit DockerSandbox (Phase 2).

### Fuer DockerSandbox (Phase 2): Echte Volume Mounts

```python
docker run \
  -v ~/Documents/Summaries:/mnt/summaries:rw \
  -v ~/Downloads:/mnt/downloads:ro \
  --read-only \
  mycelos-sandbox agent_code.py
```

---

## Filesystem-Tools

Agents greifen ueber Tools auf Dateien zu, nicht direkt:

```python
FILESYSTEM_TOOLS = [
    {
        "name": "filesystem.read",
        "description": "Read a file from a mounted directory",
        "parameters": {
            "mount_id": "str — which mount to read from",
            "path": "str — relative path within the mount",
        },
    },
    {
        "name": "filesystem.write",
        "description": "Write a file to a mounted directory",
        "parameters": {
            "mount_id": "str — which mount to write to",
            "path": "str — relative path",
            "content": "str — file content",
        },
    },
    {
        "name": "filesystem.list",
        "description": "List files in a mounted directory",
        "parameters": {
            "mount_id": "str",
            "path": "str — relative path (default: root)",
        },
    },
]
```

**Jeder Tool-Call geht durch:**
1. Mount-Check: Hat der Agent diesen Mount?
2. Access-Check: Darf er lesen/schreiben?
3. Policy-Check: Policy Engine Entscheidung
4. Ausfuehrung: Tatsaechlicher Dateizugriff
5. Audit-Log: Was wurde gelesen/geschrieben

---

## NixOS State Integration

Mounts sind Teil des deklarativen State:
- Snapshot enthaelt alle aktiven Mounts
- Rollback stellt Mount-Konfiguration wieder her
- Neue Mounts → neue Config Generation

---

## User-Flow: Mount erstellen

### Im Chat (Interview-Style)

```
User: "Ich will dass neue PDFs in meinem Downloads-Ordner
       zusammengefasst werden."

Mycelos: "Dafuer brauche ich Zugriff auf deinen Downloads-Ordner
         (nur lesen) und einen Ordner zum Speichern.

         Zugriff auf:
           ~/Downloads (lesen) — neue PDFs erkennen
           ~/Documents/Summaries (schreiben) — Zusammenfassungen

         Einverstanden? [Y/n]"

User: "Ja"

→ Mounts werden erstellt
→ Config Generation mit Mounts
→ Workflow kann starten
```

### Via CLI

```bash
mycelos mount add ~/Downloads --read --purpose "Neue Dateien erkennen"
mycelos mount add ~/Documents/Summaries --write --workflow pdf-summarizer
mycelos mount list
mycelos mount revoke m2
```

---

## Offene Fragen

1. **Verschachtelte Pfade:** Wenn Mount auf `~/Documents` (read) existiert,
   darf ein Agent dann auch `~/Documents/Private` lesen?
   → Vorschlag: Nein, nur exakte Pfade. Kein impliziter Zugriff auf Unterordner
   ausser explizit freigegenben.

2. **Relative vs. absolute Pfade:** Sollen Mounts immer absolut sein?
   → Vorschlag: Ja, immer absolut. `~` wird expanded.

3. **Mount-Lifetime:** Sollen Mounts dauerhaft sein oder per-Session?
   → Vorschlag: Dauerhaft (in DB), aber revocable. Session-Mounts als spaetere
   Erweiterung.

4. **Benachrichtigung bei Zugriff:** Soll der User informiert werden wenn
   ein Agent auf einen Mount zugreift?
   → Vorschlag: Audit-Log immer. Push-Benachrichtigung konfigurierbar per Mount.

5. **Write-Limits:** Soll es Limits fuer Schreibzugriffe geben?
   (z.B. max 100 Dateien, max 1GB pro Workflow)
   → Vorschlag: Spaeter. Erstmal nur Policy-Check.

---

## Implementierungs-Reihenfolge

1. **`mounts`-Tabelle + MountRegistry** (CRUD, Teil des NixOS State)
2. **`filesystem.*` Tools** (read, write, list mit Mount+Policy Check)
3. **LocalSandbox Mount-Support** (Symlinks + Environment Variables)
4. **Chat-Integration** (Mount-Anfrage im Interview-Flow)
5. **CLI** (`mycelos mount add/list/revoke`)
6. **DockerSandbox** (echte Volume Mounts, Phase 2)
