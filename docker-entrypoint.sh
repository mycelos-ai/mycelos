#!/bin/bash
set -e

DATA_DIR="${MYCELOS_DATA_DIR:-/data}"

# ── Master key: owned by the proxy container ────────────────────────
# Two-container mode keeps the master key OUT of the shared data
# directory. Only the proxy container mounts the secrets directory
# (MYCELOS_MASTER_KEY_FILE, e.g. /secrets/.master_key); the gateway
# mounts /data and never sees the key.
#
# prepare_proxy_key (runs as root, proxy role only):
#   - moves a key left in /data by older installs into the secrets dir
#     (copy, verify, then delete the old copy),
#   - refuses to start if both copies exist and differ,
#   - generates a key on first start,
#   - hands the file to the mycelos user (Docker Desktop shows bind
#     mounts as root:root, and the proxy runs as mycelos).
prepare_proxy_key() {
    local key_file="${MYCELOS_MASTER_KEY_FILE:-}"
    local legacy="$DATA_DIR/.master_key"
    if [ -z "$key_file" ]; then
        # Old docker-compose.yml without a secrets mount: key stays in
        # /data, readable by the gateway too. Keep it working, but say so.
        if [ -f "$legacy" ]; then
            chown mycelos:mycelos "$legacy" 2>/dev/null || true
            chmod 600 "$legacy" 2>/dev/null || true
        fi
        echo "  ⚠ MYCELOS_MASTER_KEY_FILE not set — the master key is in the shared" >&2
        echo "    data directory and the gateway container can read it. Update" >&2
        echo "    docker-compose.yml (mycelos update) to give the proxy its own" >&2
        echo "    secrets directory." >&2
        return 0
    fi
    local key_dir
    key_dir="$(dirname "$key_file")"
    mkdir -p "$key_dir"
    if [ -f "$legacy" ]; then
        if [ -s "$key_file" ]; then
            if cmp -s "$legacy" "$key_file"; then
                rm -f "$legacy"
                echo "  Removed the duplicate master key from $DATA_DIR."
            else
                echo "" >&2
                echo "========================================================" >&2
                echo "  REFUSING TO START: two different master keys found:" >&2
                echo "    $legacy" >&2
                echo "    $key_file" >&2
                echo "  Keep the one your credentials were encrypted with" >&2
                echo "  (normally the older one in the data directory), move" >&2
                echo "  it to the secrets directory and delete the other." >&2
                echo "========================================================" >&2
                exit 1
            fi
        else
            cp -p "$legacy" "$key_file.tmp"
            if cmp -s "$legacy" "$key_file.tmp"; then
                mv "$key_file.tmp" "$key_file"
                rm -f "$legacy"
                echo "  Master key moved from $DATA_DIR to $key_dir (gateway no longer sees it)."
            else
                rm -f "$key_file.tmp"
                echo "  Could not copy the master key to $key_dir — keeping $legacy." >&2
                exit 1
            fi
        fi
    fi
    if [ ! -s "$key_file" ]; then
        python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$key_file"
        echo "  Master key generated in $key_dir"
    fi
    chown mycelos:mycelos "$key_dir" "$key_file" 2>/dev/null || true
    chmod 700 "$key_dir" 2>/dev/null || true
    chmod 600 "$key_file" 2>/dev/null || true
}

# ── Fix volume permissions (Docker named volumes are owned by root) ──
# Entrypoint runs as root, fixes ownership, then drops to mycelos user.
if [ "$(id -u)" = "0" ]; then
    ROLE="${MYCELOS_ROLE:-gateway}"
    if [ "$ROLE" = "proxy" ]; then
        prepare_proxy_key
    else
        chown -R mycelos:mycelos "$DATA_DIR" 2>/dev/null || true
    fi
    # Re-exec this script as mycelos user (preserving all args + env)
    exec gosu mycelos "$0" "$@"
fi

# ── From here on we run as mycelos ──────────────────────────────────

# ── First start: auto-initialize (gateway role only) ────────────
# Proxy role has /data/mycelos.db mounted read-only and performs no
# init — the gateway container owns the init path.
# Two-container gateway: never holds the key, warns if one is lying around.
if [ "${MYCELOS_ROLE:-gateway}" != "proxy" ] && [ -n "${MYCELOS_PROXY_URL:-}" ] && [ -f "$DATA_DIR/.master_key" ]; then
    echo "  ⚠ $DATA_DIR/.master_key is visible to the gateway. With the current" >&2
    echo "    docker-compose.yml the proxy moves it to its secrets directory on start." >&2
fi

if [ "${MYCELOS_ROLE:-gateway}" != "proxy" ] && [ ! -s "$DATA_DIR/mycelos.db" ]; then
    echo "=== Mycelos: First start — auto-initializing ==="

    # Generate master key if not provided. Not in the two-container
    # gateway: the proxy container owns the key and encrypts credentials.
    if [ -n "${MYCELOS_PROXY_URL:-}" ]; then
        :
    elif [ -z "$MYCELOS_MASTER_KEY" ]; then
        export MYCELOS_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
        echo "$MYCELOS_MASTER_KEY" > "$DATA_DIR/.master_key"
        chmod 600 "$DATA_DIR/.master_key"
        echo "  Master key generated (saved to $DATA_DIR/.master_key)"
    else
        echo "$MYCELOS_MASTER_KEY" > "$DATA_DIR/.master_key"
        chmod 600 "$DATA_DIR/.master_key"
    fi

    # Initialize database + system agents + store API key
    python3 -c "
import os, sys
from pathlib import Path
from mycelos.app import App
if not os.environ.get('MYCELOS_PROXY_URL'):
    os.environ.setdefault('MYCELOS_MASTER_KEY', open('$DATA_DIR/.master_key').read().strip())
app = App(Path('$DATA_DIR'))
app.initialize()
# Two-container mode: App.credentials talks to the proxy container
# (MYCELOS_PROXY_URL + MYCELOS_PROXY_TOKEN), which encrypts the key.

# Store LLM API key from env (generic — auto-detects provider)
api_key = os.environ.get('LLM_API_KEY', '')
if api_key:
    from mycelos.cli.detect_provider import detect_provider
    detection = detect_provider(api_key)
    if detection.provider:
        app.credentials.store_credential(detection.provider, {
            'api_key': api_key,
            'env_var': detection.env_var or 'LLM_API_KEY',
            'provider': detection.provider,
        })
        print(f'  LLM key stored (encrypted) — detected provider: {detection.provider}')
    else:
        print('  Warning: could not detect provider from LLM_API_KEY. Configure via Web UI.')
elif os.environ.get('ANTHROPIC_API_KEY'):
    # Backward compat: also accept ANTHROPIC_API_KEY
    app.credentials.store_credential('anthropic', {
        'api_key': os.environ['ANTHROPIC_API_KEY'],
        'env_var': 'ANTHROPIC_API_KEY',
        'provider': 'anthropic',
    })
    print('  Anthropic API key stored (encrypted)')

# Register system agents (use the same function as mycelos init)
from mycelos.cli.init_cmd import _register_system_agents
_register_system_agents(app)

app.config.apply_from_state(app.state_manager, 'Docker auto-init', 'docker')
app.audit.log('system.initialized', details={'method': 'docker-entrypoint'})
print('  Database initialized')
print('  System agents registered')
"

    echo "=== Initialization complete ==="
    echo ""

    # Security reminder: tell user to remove the key from env
    if [ -n "$LLM_API_KEY" ] || [ -n "$ANTHROPIC_API_KEY" ]; then
        echo "  ┌─────────────────────────────────────────────────────────┐"
        echo "  │  API key is now encrypted in the database.             │"
        echo "  │  For security, REMOVE it from your environment:        │"
        echo "  │                                                        │"
        echo "  │    - Delete LLM_API_KEY from .env / docker-compose.yml │"
        echo "  │    - Restart without the key: docker compose up        │"
        echo "  │                                                        │"
        echo "  │  The key is safely stored — it won't be needed again.  │"
        echo "  └─────────────────────────────────────────────────────────┘"
        echo ""
    fi

    if [ -z "$LLM_API_KEY" ] && [ -z "$ANTHROPIC_API_KEY" ]; then
        echo "  No LLM API key provided. Configure via Web UI:"
        echo "  http://localhost:9100 → chat → /credential store anthropic <key>"
        echo ""
    fi
fi

# ── Security warning: key still in env on subsequent starts ───────
if [ -f "$DATA_DIR/mycelos.db" ]; then
    if [ -n "$LLM_API_KEY" ] || [ -n "$ANTHROPIC_API_KEY" ]; then
        echo ""
        echo "  ⚠ WARNING: LLM API key is still in your environment."
        echo "  It's already encrypted in the database — remove it from .env for security."
        echo ""
    fi
fi

# Pre-flight: if the HOST exposes the gateway beyond localhost
# (MYCELOS_BIND in .env), a password is mandatory. Fail fast with a clear
# message instead of letting the gateway refuse later where only
# docker logs would reveal why.
BIND="${MYCELOS_BIND:-127.0.0.1}"
case "$BIND" in
    127.0.0.1|::1|localhost) ;;
    *)
        if [ -z "${MYCELOS_PASSWORD:-}" ]; then
            echo "" >&2
            echo "========================================================" >&2
            echo "  REFUSING TO START: MYCELOS_BIND=$BIND exposes the" >&2
            echo "  gateway beyond localhost, but MYCELOS_PASSWORD is" >&2
            echo "  not set. Anyone on the network could use Mycelos." >&2
            echo "" >&2
            echo "  Fix: add to your .env next to docker-compose.yml:" >&2
            echo "      MYCELOS_PASSWORD=<a strong password>" >&2
            echo "  or set MYCELOS_BIND=127.0.0.1 to stay local-only." >&2
            echo "========================================================" >&2
            exit 1
        fi
        ;;
esac

# Role selection: same image, two container modes.
ROLE="${MYCELOS_ROLE:-gateway}"

case "$ROLE" in
    proxy)
        # SecurityProxy on TCP. Master key comes from $DATA_DIR/.master_key
        # (bind-mounted read-only), token from MYCELOS_PROXY_TOKEN.
        exec mycelos serve \
            --role proxy \
            --proxy-host 0.0.0.0 \
            --proxy-port "${MYCELOS_PROXY_PORT:-9110}" \
            --data-dir "$DATA_DIR"
        ;;
    gateway|all)
        # Default: gateway (web UI + API). Passes through the compose CMD.
        exec "$@"
        ;;
    *)
        echo "Error: unknown MYCELOS_ROLE=$ROLE (expected: proxy, gateway, all)" >&2
        exit 1
        ;;
esac
