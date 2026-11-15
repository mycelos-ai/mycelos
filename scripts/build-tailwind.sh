#!/usr/bin/env bash
# Compile the Web UI's Tailwind stylesheet into
# src/mycelos/frontend/shared/vendor/tailwind.css.
#
# Run this after adding or changing Tailwind classes in
# src/mycelos/frontend (pages/*.html, shared/*.js, shared/*.html) and commit
# the regenerated CSS. CI rebuilds it and fails if the committed file differs.
# Needs Node.js 18+; versions are pinned in tools/tailwind/package-lock.json.
set -euo pipefail
cd "$(dirname "$0")/../tools/tailwind"
npm ci --no-audit --no-fund --silent
npm run --silent build
