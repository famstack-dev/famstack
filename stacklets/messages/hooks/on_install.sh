#!/usr/bin/env bash
# stacklets/messages/setup.sh
#
# First-run setup for the messages stacklet. This script:
#   1. Creates data directories for Synapse and Postgres
#   2. Generates homeserver.yaml with signing keys
#   3. Patches it for LAN-only family use (no federation, no public registration)
#   4. Generates the Element Web config pointing at the Synapse API
#
# The server_name comes from FAMSTACK_MESSAGES_SERVER_NAME, set by the CLI
# after prompting the user. It's permanent — appears in every user ID
# (@user:name) and cannot be changed without starting fresh.
#
# 'stack up messages' calls this automatically on first run.

set -euo pipefail

DATA_DIR="${FAMSTACK_DATA_DIR:-$HOME/famstack-data}"
SYNAPSE_DIR="$DATA_DIR/messages/synapse"
DB_DIR="$DATA_DIR/messages/postgres"
SERVER_NAME="${FAMSTACK_MESSAGES_SERVER_NAME:-home}"
DB_PASSWORD="${DB_PASSWORD:-}"
REGISTRATION_SECRET="${REGISTRATION_SECRET:-}"
MACAROON_SECRET="${MACAROON_SECRET:-}"

echo "messages: creating data directories..."
mkdir -p "$SYNAPSE_DIR" "$DB_DIR"
chmod 700 "$DB_DIR"

# ── Generate Synapse config ──────────────────────────────────────────────────
#
# Only runs if homeserver.yaml doesn't exist yet — a fresh install. If the
# file exists (e.g. migrated from another server), we leave it alone.
#
# The generate command creates three files:
#   - homeserver.yaml (main config — we patch this below)
#   - <server_name>.signing.key (identity key — never regenerate)
#   - <server_name>.log.config (logging format)

if [ ! -f "$SYNAPSE_DIR/homeserver.yaml" ]; then
    echo "messages: generating Synapse configuration for '$SERVER_NAME'..."
    docker run --rm \
        -v "$SYNAPSE_DIR:/data" \
        -e SYNAPSE_SERVER_NAME="$SERVER_NAME" \
        -e SYNAPSE_REPORT_STATS=no \
        matrixdotorg/synapse:latest generate

    # ── Patch homeserver.yaml ────────────────────────────────────────────
    #
    # The generated config uses SQLite and allows federation. We rewrite it
    # for our Postgres database and lock down everything a LAN-only family
    # server doesn't need.

    CONF="$SYNAPSE_DIR/homeserver.yaml"

    echo "messages: configuring for LAN-only family use..."

    # Rewrite homeserver.yaml as JSON. Synapse parses config with
    # yaml.safe_load() which handles JSON natively — no PyYAML needed
    # on the host, just stdlib json.
    python3 - "$CONF" "$SERVER_NAME" "$DB_USER" "$DB_PASSWORD" "$DB_NAME" "$REGISTRATION_SECRET" "$MACAROON_SECRET" <<'PYEOF'
import json, sys

conf_path, server_name, db_user, db_password, db_name, reg_secret, mac_secret = sys.argv[1:8]

config = {
    "server_name": server_name,
    "signing_key_path": f"/data/{server_name}.signing.key",
    "pid_file": "/data/homeserver.pid",
    "media_store_path": "/data/media_store",
    "log_config": f"/data/{server_name}.log.config",

    "database": {
        "name": "psycopg2",
        "args": {
            "user": db_user,
            "password": db_password,
            "database": db_name,
            "host": "stack-messages-db",
            "port": 5432,
            "cp_min": 5,
            "cp_max": 10,
        }
    },

    "listeners": [{
        "port": 8008,
        "tls": False,
        "bind_addresses": ["::"],
        "type": "http",
        "x_forwarded": True,
        "resources": [{"names": ["client"], "compress": True}],
    }],

    "federation_domain_whitelist": [],
    "allow_public_rooms_over_federation": False,
    "trusted_key_servers": [],

    "enable_registration": False,
    "registration_shared_secret": reg_secret,

    "encryption_enabled_by_default_for_room_type": "off",

    "macaroon_secret_key": mac_secret,
    "report_stats": False,
    "suppress_key_server_warning": True,

    # Media — families share lots of photos and videos
    "max_upload_size": "100M",

    # URL previews — show link thumbnails in Element
    "url_preview_enabled": True,
    "url_preview_ip_range_blacklist": [],

    # Keep everything forever — it's your own server
    "retention": {"enabled": False},

    # Voice/video calls — STUN helps devices find each other.
    # Works for LAN calls out of the box. For calls across networks,
    # a TURN server (coturn) would be needed as a future addition.
    "turn_uris": [
        "stun:stun.l.google.com:19302",
        "stun:stun1.l.google.com:19302",
    ],
    "turn_allow_guests": False,

    # Relaxed rate limits for a LAN-only family server.
    "rc_login": {
        "address": {"per_second": 1, "burst_count": 20},
        "account": {"per_second": 1, "burst_count": 20},
        "failed_attempts": {"per_second": 0.5, "burst_count": 20},
    },
    "rc_message": {"per_second": 5, "burst_count": 30},
    "rc_admin_redaction": {"per_second": 5, "burst_count": 30},
}

with open(conf_path, 'w') as f:
    json.dump(config, f, indent=2)
PYEOF
    echo "messages: Synapse configured — server_name: $SERVER_NAME"
else
    echo "messages: homeserver.yaml already exists, skipping generation"
fi

# ── Generate Element Web config ──────────────────────────────────────────────
#
# Element Web is a static single-page app. It needs to know where the
# Synapse API lives so it can connect. This config is mounted read-only
# into the Element container.

ELEMENT_CONF="$SYNAPSE_DIR/element-config.json"

if [ ! -f "$ELEMENT_CONF" ]; then
    # SYNAPSE_PUBLIC_URL is rendered by the stack CLI from the stacklet's
    # port config — it uses the LAN IP in port mode and the domain in
    # domain mode. No hardcoded URLs here.
    SYNAPSE_URL="${SYNAPSE_PUBLIC_URL:-http://localhost:42031}"

    echo "messages: writing Element Web config..."
    python3 - "$SYNAPSE_URL" "$SERVER_NAME" "$ELEMENT_CONF" <<'PYEOF'
import json
import sys

synapse_url, server_name, element_conf = sys.argv[1:4]

config = {
    'default_server_config': {
        'm.homeserver': {
            'base_url': synapse_url,
            'server_name': server_name
        }
    },
    'brand': (server_name.capitalize() if server_name.lower().endswith('s') else server_name.capitalize() + 's') + ' Chat',
    'disable_guests': True,
    'disable_3pid_login': True,
    'default_theme': 'system',
    'room_directory': {
        'servers': [server_name]
    },
    'force_disable_encryption': True,
    'setting_defaults': {
        'UIFeature.identityServer': False,
        'UIFeature.roomDirectoryButton': True,
        'UIFeature.deactivate': False,
        'e2ee.manuallyVerifyAllSessions': False,
    }
}
with open(element_conf, 'w') as f:
    json.dump(config, f, indent=2)
PYEOF
    echo "messages: Element Web configured — connecting to $SYNAPSE_URL"
else
    echo "messages: element-config.json already exists, skipping"
fi

echo "messages: done"
echo "  synapse:  $SYNAPSE_DIR"
echo "  postgres: $DB_DIR"
