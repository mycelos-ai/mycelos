# Plan: Package-Rename `mycelos` → `mycelos`

> **Week:** 14 (2026)
> **Status:** Planned (not started)
> **Priority:** Medium — branding alignment
> **Estimated effort:** 30-45 minutes

## Goal

Rename the Python package from `mycelos` to `mycelos` so the import path is stable and brand-aligned. The CLI command, env vars, UI strings, and domain stay unchanged for now — only the internal import path changes.

## What Changes

### Phase 1: Package rename (this plan)

| What | Before | After |
|------|--------|-------|
| Directory | `src/mycelos/` | `src/mycelos/` |
| Python imports | `from mycelos.chat import ...` | `from mycelos.chat import ...` |
| pyproject.toml packages | `["src/mycelos"]` | `["src/mycelos"]` |
| pyproject.toml entry_point | `mycelos = "mycelos.cli.main:cli"` | `mycelos = "mycelos.cli.main:cli"` |

### What stays the same (Phase 1)

- CLI command: `mycelos` (unchanged)
- Env vars: `MYCELOS_MASTER_KEY` etc. (unchanged)
- Data dir: `~/.mycelos/` (unchanged)
- Brand in UI/prompts: "Mycelos" (unchanged)
- Domain: mycelos.com (unchanged)
- Docker/CI: unchanged
- Database: no package references

### Phase 2: Full brand rename (future, separate plan)

- CLI: `mycelos` → `mycelos`
- Env vars: `MYCELOS_*` → `MYCELOS_*`
- Data dir: `~/.mycelos/` → `~/.mycelos/`
- UI/prompts: "Mycelos" → "Mycelos" (via BRAND_NAME constant)
- Domain: mycelos.com
- pyproject.toml name: `mycelos`
- Docker image tags

## Steps (Phase 1)

### 1. Rename directory
```bash
git mv src/mycelos src/mycelos
```

### 2. Replace all imports (sed)
```bash
# In src/mycelos/
find src/mycelos -name "*.py" -exec sed -i '' 's/from mycelos\./from mycelos./g' {} +
find src/mycelos -name "*.py" -exec sed -i '' 's/import mycelos/import mycelos/g' {} +

# In tests/
find tests -name "*.py" -exec sed -i '' 's/from mycelos\./from mycelos./g' {} +
find tests -name "*.py" -exec sed -i '' 's/import mycelos/import mycelos/g' {} +
```

### 3. Update pyproject.toml
```toml
[project]
name = "mycelos"  # Keep for now — Phase 2

[project.scripts]
mycelos = "mycelos.cli.main:cli"  # CLI stays "mycelos", package changes

[tool.hatch.build]
packages = ["src/mycelos"]
```

### 4. Update .gitignore
```
src/mycelos/knowledge  →  if referenced
```

### 5. Update conftest.py
```python
from mycelos.storage.database import SQLiteStorage  # etc.
```

### 6. Update internal self-references
- `src/mycelos/cli/chat_cmd.py`: `shutil.which("mycelos")` stays (CLI name)
- `src/mycelos/__init__.py`: version string
- Schema path: `Path(__file__).parent / "schema.sql"` — relative, no change needed
- Frontend mount: relative path, auto-resolves

### 7. Reinstall + test
```bash
pip install -e ".[dev]"
pytest tests/ --ignore=tests/integration --ignore=tests/e2e -x -q
```

### 8. Commit
```
refactor: rename package mycelos → mycelos (import path only)
```

## Risks

- **250 files affected** — all import statements
- `pip install -e .` must be re-run after rename
- Any hardcoded `"mycelos."` strings in non-Python files (HTML, YAML) might break
- Worktrees with old code will have stale imports

## Prerequisites

- All pending changes committed and pushed
- No open PRs with mycelos-specific changes
- Clean test suite
- User (Stefan) ready to re-run `pip install -e .`

## What does NOT need migration

- Database (no package references in tables)
- User data (`~/.mycelos/` stays)
- Credentials (encrypted, no package refs)
- Knowledge files (Markdown, no imports)
- Config generations (JSON snapshots, no import paths)
