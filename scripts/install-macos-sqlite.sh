#!/usr/bin/env bash
#
# Rebuild Python (via pyenv) with SQLite extension support on macOS.
#
# Why: macOS' system SQLite has loadable_extension support disabled.
# pyenv defaults to linking against system SQLite, which means sqlite-vec
# (vector search for the knowledge base) cannot be loaded.
#
# This script:
#   1. Detects your current pyenv Python version
#   2. Reinstalls it with the correct flags to link against brew's SQLite
#   3. Reinstalls Mycelos (pip install -e .) so it picks up the new Python
#
# Estimated time: 5-10 minutes (Python rebuild)

set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This script is for macOS only. On Linux, sqlite-vec works out of the box."
  exit 1
fi

if ! command -v pyenv >/dev/null 2>&1; then
  echo "pyenv not found. Install it first:"
  echo "  brew install pyenv"
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew not found. Install it first: https://brew.sh"
  exit 1
fi

# Ensure brew sqlite is installed
if ! brew list sqlite >/dev/null 2>&1; then
  echo "Installing brew sqlite..."
  brew install sqlite
fi

PY_VERSION="$(pyenv version-name)"
echo "Current pyenv Python: $PY_VERSION"
echo

read -r -p "Rebuild $PY_VERSION with SQLite extension support? [y/N] " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Aborted."
  exit 0
fi

SQLITE_PREFIX="$(brew --prefix sqlite)"
echo
echo "Building Python $PY_VERSION against $SQLITE_PREFIX ..."
echo "This will take a few minutes."
echo

LDFLAGS="-L${SQLITE_PREFIX}/lib" \
CPPFLAGS="-I${SQLITE_PREFIX}/include" \
PYTHON_CONFIGURE_OPTS="--enable-loadable-sqlite-extensions" \
  pyenv install --force "$PY_VERSION"

echo
echo "Verifying SQLite extension support..."
if python3 -c "import sqlite3; sqlite3.connect(':memory:').enable_load_extension(True)" 2>/dev/null; then
  echo "  ✓ enable_load_extension works"
else
  echo "  ✗ enable_load_extension still missing — check pyenv/Python build logs"
  exit 1
fi

echo
echo "Reinstalling Mycelos with new Python..."
pip install --quiet -e .

echo
echo "Done. Run: mycelos doctor --check sqlite_vec"
