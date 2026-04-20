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
[ -f data/.master_key ] || { echo "FAIL: data/.master_key missing"; exit 1; }
[ -f data/mycelos.db ] || { echo "FAIL: data/mycelos.db missing"; exit 1; }

# Token is non-empty and long enough
TOKEN="$(grep '^MYCELOS_PROXY_TOKEN=' .env | cut -d= -f2)"
if [ -z "$TOKEN" ] || [ "${#TOKEN}" -lt 20 ]; then
    echo "FAIL: MYCELOS_PROXY_TOKEN missing or too short"
    exit 1
fi

# Permissions on sensitive files
perms="$(stat -c '%a' data/.master_key 2>/dev/null || stat -f '%A' data/.master_key)"
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

echo "PASS: install.sh smoke test"
