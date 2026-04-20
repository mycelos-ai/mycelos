#!/usr/bin/env bash
# E2E: bring the stack up, verify gateway is keyless, proxy port stays internal,
# proxy rejects unauthenticated calls.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'cd "$TMP" && docker compose down -v 2>/dev/null || true; cd /tmp && rm -rf "$TMP"' EXIT

cd "$TMP"
MYCELOS_COMPOSE_SRC="$ROOT/docker-compose.yml" \
    "$ROOT/scripts/install.sh" --data-dir "$TMP/data"

# 1. Gateway healthy
curl -fsSL http://localhost:9100/api/health > /dev/null
echo "OK: gateway healthy"

# 2. Gateway has no master key in its container filesystem
if docker compose exec -T gateway test -f /data/.master_key; then
    echo "FAIL: gateway can see .master_key"
    exit 1
fi
echo "OK: gateway has no master_key"

# 3. Proxy port not reachable from the host
if curl -fsSL -m 2 http://localhost:9110/health 2>/dev/null; then
    echo "FAIL: proxy port 9110 reachable from host"
    exit 1
fi
echo "OK: proxy port not host-reachable"

# 4. Unauth call from gateway to proxy must 401/403
status=$(docker compose exec -T gateway \
    curl -s -o /dev/null -w '%{http_code}' http://proxy:9110/llm/complete -XPOST -d '{}' \
    || echo "curl-failed")
if [ "$status" != "401" ] && [ "$status" != "403" ]; then
    echo "FAIL: expected 401/403 from unauthenticated proxy call, got $status"
    exit 1
fi
echo "OK: proxy rejects unauthenticated calls ($status)"

# 5. Gateway cannot reach the public internet (Phase 1b lockdown)
if docker compose exec -T gateway curl -fsSL -m 3 https://example.com >/dev/null 2>&1; then
    echo "FAIL: gateway has an internet route (Phase 1b expects it to have none)"
    exit 1
fi
echo "OK: gateway has no direct internet route"

echo "PASS: two-container deployment e2e"
