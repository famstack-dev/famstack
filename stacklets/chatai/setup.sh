#!/usr/bin/env bash
# stacklets/chatai/setup.sh
#
# Run once on first enable. Creates the data directory Open WebUI needs
# for chat history, user settings, and uploaded files.
#
# 'stack up' calls this automatically; safe to run again (idempotent).

set -euo pipefail

DATA_DIR="${FAMSTACK_DATA_DIR:-$HOME/famstack-data}"
CHATAI_DIR="$DATA_DIR/chatai"

echo "chatai: creating data directory..."
mkdir -p "$CHATAI_DIR"

echo "chatai: done"
echo "  data: $CHATAI_DIR"
