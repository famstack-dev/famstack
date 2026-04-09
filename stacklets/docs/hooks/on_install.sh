#!/usr/bin/env bash
# stacklets/docs/setup.sh
#
# Run once on first enable. Creates the data directories Paperless-ngx needs
# before the containers start — Postgres refuses to initialise if its
# directory doesn't exist or has wrong permissions.
#
# Paths follow the famstack convention: {data_dir}/{stacklet_id}/{service}
# 'stack up' calls this automatically; safe to run again (idempotent).

set -euo pipefail

DATA_DIR="${FAMSTACK_DATA_DIR:-$HOME/famstack-data}"
PAPERLESS_DIR="$DATA_DIR/docs/paperless"
DB_DIR="$DATA_DIR/docs/postgres"
CONSUME_DIR="$DATA_DIR/docs/consume"

echo "docs: creating data directories..."
mkdir -p "$PAPERLESS_DIR/media" "$PAPERLESS_DIR/data" "$PAPERLESS_DIR/export"
mkdir -p "$DB_DIR"
mkdir -p "$CONSUME_DIR"

# Postgres requires 700 on its data directory
chmod 700 "$DB_DIR"

echo "docs: done"
echo "  paperless: $PAPERLESS_DIR"
echo "  postgres:  $DB_DIR"
echo "  consume:   $CONSUME_DIR"
