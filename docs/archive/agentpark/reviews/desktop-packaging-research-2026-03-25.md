# Windows Packaging Research: Next.js + Python Backend for Non-Technical Users

Date: 2026-03-25

## Goal
Ship a **single installer experience** for non-technical Windows users with:
- Next.js UI
- Python backend
- no separate Python/Node installation required for end users

## Current ecosystem reality (official docs)

### Tauri + Next.js + Python sidecar
- Tauri supports bundling external binaries via `bundle.externalBin` ("sidecar").
- Sidecars are a documented fit for packaging Python services/binaries.
- Tauri + Next.js is documented, but Next.js must be used in static export mode (`output: 'export'`) because there is no Node runtime inside Tauri WebView.

Implication: this is clean for installer UX, but requires re-architecting SSR/API-route assumptions in Next.js.

### Electron + Python backend
- Electron Forge can produce Windows installers (Squirrel maker).
- Squirrel output includes installer + update metadata artifacts.
- Electron has first-class auto-update docs (`autoUpdater`, Squirrel caveats such as first-run lock timing).

Implication: often fastest route if team is JS-heavy and accepts larger app/runtime footprint.

### Python-first (Briefcase / PyInstaller + WebView)
- Briefcase supports Windows MSI packaging as a first-class output.
- PyInstaller supports bundling to one-file/one-dir app bundles and requires per-OS builds.
- pywebview is lightweight but web-engine behavior depends on platform backend.

Implication: simpler if Python owns architecture; less natural if you rely on full Next.js server/runtime features.

## Recommendation order for your stack
1. **Long-term clean architecture**: Tauri + static Next.js UI + Python sidecar.
2. **Fastest delivery to stable installer**: Electron + bundled Python executable.
3. **Only if simplifying scope**: Python-first WebView app.

## Non-technical installability checklist

### Must-have (regardless of framework)
- One-click installer (`.msi` or setup exe).
- App starts from desktop/start-menu shortcut.
- App auto-starts backend process internally (no manual terminal steps).
- Logs written to app-specific directory.
- Built-in health check + retry when backend fails to boot.
- Signed installer/executables to reduce SmartScreen friction.
- Auto-update channel with rollback strategy.

### Tauri-specific implementation notes
- Build Next.js as static export (`output: 'export'`) and point Tauri to exported assets.
- Convert Python backend into a native executable (typically PyInstaller/Nuitka), then register as `externalBin` sidecar.
- Start sidecar from Rust app lifecycle, wait for ready signal/port probe, then load UI actions that depend on backend.
- Use Tauri updater plugin for in-app updates.

### Electron-specific implementation notes
- Bundle frontend in Electron renderer.
- Launch Python executable as child process from main process.
- Use Electron Forge makers (Squirrel) for Windows installer packaging.
- Wire autoUpdater flow, handling first-run Squirrel lock timing.

## Practical migration blueprint (suggested)

### Phase 1 (1–2 weeks): installer MVP
- Freeze Python backend to executable.
- Keep frontend static-compatible where possible.
- Implement desktop shell process manager (start/stop/restart backend).
- Produce signed internal installer for pilot users.

### Phase 2 (1–2 weeks): resilience + supportability
- Structured logging + diagnostics export button.
- Crash restart policy + startup timeout UX.
- Background update checks + controlled restart prompt.

### Phase 3: polish for non-technical rollout
- Friendly first-run setup wizard.
- Silent dependency checks/migrations.
- Troubleshooting screen with copy-to-clipboard diagnostic bundle.

## Key constraints to validate early
- Are any critical Next.js pages dependent on SSR/API routes?
- Do you need local-only mode (offline) or cloud API dependencies?
- Expected update cadence and rollback guarantees.
- Corporate endpoint constraints (proxy, restricted install permissions, certificate trust).

## Primary sources used
- Tauri sidecar (`externalBin`): https://v2.tauri.app/develop/sidecar/
- Tauri + Next.js frontend setup: https://v2.tauri.app/start/frontend/nextjs/
- Tauri updater plugin: https://v2.tauri.app/plugin/updater/
- Next.js static export guide: https://nextjs.org/docs/pages/guides/static-exports
- Electron Forge Squirrel maker: https://www.electronforge.io/config/makers/squirrel.windows
- Electron autoUpdater: https://www.electronjs.org/docs/latest/api/auto-updater
- Briefcase Windows packaging: https://briefcase.beeware.org/en/latest/reference/platforms/windows/
- PyInstaller usage: https://www.pyinstaller.org/en/stable/usage.html
- pywebview web engine notes: https://pywebview.flowrl.com/guide/web_engine.html
