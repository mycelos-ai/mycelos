#!/bin/bash
set -e

DATA_DIR="${MYCELOS_DATA_DIR:-/data}"

# ── Fix volume permissions (Docker named volumes are owned by root) ──
# Entrypoint runs as root, fixes ownership, then drops to mycelos user.
if [ "$(id -u)" = "0" ]; then
    chown -R mycelos:mycelos "$DATA_DIR"
    # Re-exec this script as mycelos user (preserving all args + env)
    exec gosu mycelos "$0" "$@"
fi

# ── From here on we run as mycelos ──────────────────────────────────

# ── First start: auto-initialize ──────────────────────────────────
if [ ! -f "$DATA_DIR/mycelos.db" ]; then
    echo "=== Mycelos: First start — auto-initializing ==="

    # Generate master key if not provided
    if [ -z "$MYCELOS_MASTER_KEY" ]; then
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
os.environ.setdefault('MYCELOS_MASTER_KEY', open('$DATA_DIR/.master_key').read().strip())
from mycelos.app import App
app = App(Path('$DATA_DIR'))
app.initialize()

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
