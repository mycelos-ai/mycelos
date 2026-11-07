# Agent Exchange Format (AXF) — Portable Agent Import/Export

**Status:** Specification Draft
**Date:** 2026-03-22

---

## Vision

Ein standardisiertes, verschluesseltes Austauschformat fuer Agent-Konfigurationen.
Ermoeglicht:
- **Export** aus Mycelos (Agents, Workflows, Config — ohne Klartext-Credentials)
- **Import** in Mycelos (von OpenClaw, anderen Systemen, oder eigenem Backup)
- **Portabilitaet** — User kann sein Setup zwischen Maschinen migrieren
- **Sicherheit** — Credentials verschluesselt oder ausgelassen, nie im Klartext

## Use Cases

1. **Backup/Restore:** User exportiert sein komplettes Setup, installiert Mycelos neu, importiert
2. **Migration:** User wechselt von OpenClaw zu Mycelos — Agents, Prompts, Config uebernehmen
3. **Sharing:** User teilt einen Agent (ohne Credentials) mit jemand anderem
4. **Multi-Device:** User hat Mycelos auf Laptop + Server, synchronisiert Agents

## Format: `.axf` (Agent Exchange Format)

Ein `.axf` File ist ein ZIP-Archiv mit definierter Struktur:

```
my-setup.axf (ZIP)
├── manifest.json              — Metadata, Version, Quelle
├── agents/
│   ├── news-agent.json        — Agent-Definition
│   ├── news-agent.code.py     — Agent-Code (Klartext)
│   ├── news-agent.tests.py    — Agent-Tests
│   └── news-agent.prompt.md   — Agent-Prompt
├── workflows/
│   ├── news-summary.yaml      — Workflow-Definition
│   └── create-agent.yaml
├── config/
│   ├── models.json            — LLM-Konfiguration (Modelle, Tiers, Zuweisungen)
│   ├── connectors.json        — Connector-Definitionen (OHNE Keys)
│   └── policies.json          — Policy-Entscheidungen
├── credentials/
│   └── encrypted.bin          — Optional: verschluesselte Credentials
│                                 (Passwort-geschuetzt, nicht Master-Key-abhaengig)
└── memory/
    └── entries.json            — Optional: Memory-Eintraege
```

### manifest.json

```json
{
  "format": "axf",
  "version": "1.0",
  "source": "mycelos",
  "source_version": "0.1.0",
  "created_at": "2026-03-22T10:00:00Z",
  "description": "Stefan's Mycelos Setup",
  "includes": {
    "agents": 3,
    "workflows": 2,
    "config": true,
    "credentials": false,
    "memory": true
  },
  "checksum": "sha256:..."
}
```

## Security

### Credentials

Drei Modi:
1. **Ohne Credentials** (Default) — `.axf` enthaelt keine Secrets. User muss nach Import Keys neu eingeben.
2. **Passwort-verschluesselt** — User setzt ein Export-Passwort. Credentials werden mit AES-256-GCM verschluesselt (PBKDF2 aus Passwort, nicht Master-Key). Empfaenger braucht das Passwort.
3. **Nur fuer mich** — Verschluesselt mit dem eigenen Master-Key. Nur auf der gleichen Mycelos-Instanz importierbar (Backup-Szenario).

### Code-Sicherheit

Importierter Agent-Code durchlaeuft den **Auditor** vor Aktivierung — auch beim Import.
Kein automatisches Vertrauen fuer fremden Code.

## Import von OpenClaw

### OpenClaw Export-Format (Analyse noetig)

OpenClaw hat:
- Characters (≈ unsere Agents)
- Character Files (≈ Agent-Code + Prompt)
- Settings (≈ Config)
- Plugins (≈ Connectors)

### Converter: OpenClaw → AXF

```python
def convert_openclaw_to_axf(openclaw_dir: Path) -> Path:
    """Convert an OpenClaw installation to AXF format.

    Reads:
    - characters/*.json → agents/
    - settings.json → config/
    - plugins/ → connectors.json

    Returns path to generated .axf file.
    """
```

### Import: AXF → Mycelos

```python
def import_axf(app: App, axf_path: Path, password: str | None = None) -> ImportResult:
    """Import an AXF file into Mycelos.

    1. Validate manifest + checksum
    2. Import agents (Code → Object Store, Audit vor Aktivierung)
    3. Import workflows (→ WorkflowRegistry)
    4. Import config (merge mit bestehendem State)
    5. Import credentials (wenn verschluesselt + Passwort)
    6. Import memory (optional)
    7. Neue Config Generation erstellen
    """
```

## CLI

```bash
mycelos export --output my-setup.axf              # Alles exportieren (ohne Credentials)
mycelos export --output my-setup.axf --encrypt    # Mit Passwort-verschluesselten Credentials
mycelos export --output my-setup.axf --agent news-agent  # Nur einen Agent
mycelos import my-setup.axf                       # Importieren
mycelos import openclaw-export/                    # OpenClaw-Verzeichnis importieren
```

## Implementierungs-Reihenfolge

1. **AXF Format-Spezifikation** finalisieren
2. **Mycelos Export** (`mycelos export`) — einfacher, wir kennen unser eigenes Format
3. **Mycelos Import** (`mycelos import`) — AXF lesen + in DB schreiben
4. **OpenClaw Converter** — OpenClaw → AXF → Mycelos Import
5. **Verschluesselte Credentials** im AXF (Passwort-basiert)

## Abgrenzung

- AXF ist KEIN Sync-Protokoll (kein Merge, kein Conflict Resolution)
- AXF ist KEIN Backup-Format (dafuer ist NixOS State + DB Backup besser)
- AXF ist ein **Portabilitaets-Format** fuer Agent-Definitionen + Config
