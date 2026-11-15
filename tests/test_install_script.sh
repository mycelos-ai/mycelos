#!/usr/bin/env bash
# Black-box smoke test for scripts/install.sh.
# Uses --dry-run to stop before `docker compose up`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/mycelos-install-test.XXXXXX")"
trap "rm -rf $TMP" EXIT

# Point install.sh at the local compose file to avoid GitHub 404 during tests
export MYCELOS_COMPOSE_SRC="$SCRIPT_DIR/docker-compose.yml"

cd "$TMP"
"$SCRIPT_DIR/scripts/install.sh" --dry-run

# Files created
[ -f .env ] || { echo "FAIL: .env missing"; exit 1; }
[ -f docker-compose.yml ] || { echo "FAIL: docker-compose.yml missing"; exit 1; }
[ -d data ] || { echo "FAIL: data/ missing"; exit 1; }
[ -f secrets/.master_key ] || { echo "FAIL: secrets/.master_key missing"; exit 1; }
[ ! -e data/.master_key ] || { echo "FAIL: master key must not be in data/ (gateway mount)"; exit 1; }
grep -q '^MYCELOS_SECRETS_DIR=' .env || { echo "FAIL: MYCELOS_SECRETS_DIR missing in .env"; exit 1; }
[ -f data/mycelos.db ] || { echo "FAIL: data/mycelos.db missing"; exit 1; }

# Token is non-empty and long enough
TOKEN="$(grep '^MYCELOS_PROXY_TOKEN=' .env | cut -d= -f2)"
if [ -z "$TOKEN" ] || [ "${#TOKEN}" -lt 20 ]; then
    echo "FAIL: MYCELOS_PROXY_TOKEN missing or too short"
    exit 1
fi

# Permissions on sensitive files
perms="$(stat -c '%a' secrets/.master_key 2>/dev/null || stat -f '%A' secrets/.master_key)"
if [ "$perms" != "600" ]; then
    echo "FAIL: .master_key should be mode 600, got $perms"
    exit 1
fi

env_perms="$(stat -c '%a' .env 2>/dev/null || stat -f '%A' .env)"
if [ "$env_perms" != "600" ]; then
    echo "FAIL: .env should be mode 600, got $env_perms"
    exit 1
fi

# Idempotency: second run preserves the token
"$SCRIPT_DIR/scripts/install.sh" --dry-run
TOKEN2="$(grep '^MYCELOS_PROXY_TOKEN=' .env | cut -d= -f2)"
[ "$TOKEN" = "$TOKEN2" ] || { echo "FAIL: second run overwrote the token"; exit 1; }

# Migration: an older install kept the key in data/. The installer moves
# it to secrets/ without changing it, and never overwrites a stored key.
KEY="$(cat secrets/.master_key)"
mkdir -p "$TMP/legacy" && cd "$TMP/legacy"
mkdir -p data && echo "legacy-key-123" > data/.master_key && chmod 600 data/.master_key
"$SCRIPT_DIR/scripts/install.sh" --dry-run
[ ! -e data/.master_key ] || { echo "FAIL: legacy key left in data/"; exit 1; }
[ "$(cat secrets/.master_key)" = "legacy-key-123" ] || { echo "FAIL: legacy key not moved verbatim"; exit 1; }

# Two different keys: refuse instead of guessing.
echo "other-key" > data/.master_key
if "$SCRIPT_DIR/scripts/install.sh" --dry-run 2>/dev/null; then
    echo "FAIL: installer accepted two different master keys"; exit 1
fi
[ "$(cat secrets/.master_key)" = "legacy-key-123" ] || { echo "FAIL: stored key changed"; exit 1; }
cd "$TMP"

echo "PASS: install.sh smoke test"
