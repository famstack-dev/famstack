#!/usr/bin/env bash
# stacklets/photos/setup.sh
#
# Run once on first enable. Creates the data directories Immich needs before
# the containers start — Postgres refuses to initialise if its directory
# doesn't exist or has wrong permissions.
#
# Paths follow the famstack convention: {data_dir}/{stacklet_id}/{service}
# data_dir comes from stack.toml and is passed in as FAMSTACK_DATA_DIR.
# 'stack enable' calls this automatically; safe to run again (idempotent).

set -euo pipefail

DATA_DIR="${FAMSTACK_DATA_DIR:-$HOME/famstack-data}"
UPLOAD_DIR="$DATA_DIR/photos/library"
DB_DIR="$DATA_DIR/photos/postgres"

echo "photos: creating data directories..."
mkdir -p "$UPLOAD_DIR" "$DB_DIR"

# Postgres requires 700 on its data directory — it will refuse to start otherwise
chmod 700 "$DB_DIR"

echo "photos: done"
echo "  library:  $UPLOAD_DIR"
echo "  postgres: $DB_DIR"
