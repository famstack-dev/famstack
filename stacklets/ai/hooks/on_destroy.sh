#!/usr/bin/env bash
# stacklets/ai/destroy.sh — Remove native AI services entirely
#
# Unlike down.sh, this always removes services regardless of who started them.

set -euo pipefail

DATA_DIR="${FAMSTACK_DATA_DIR:-$HOME/famstack-data}"
STACKLET_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ── oMLX ──────────────────────────────────────────────────────────────────
if brew list omlx &>/dev/null; then
    brew services stop omlx 2>/dev/null || true
    echo "    ✓ oMLX stopped"
    # Don't uninstall the brew package — that's the user's call
fi

# ── LM Studio ────────────────────────────────────────────────────────────
LMS="$HOME/.lmstudio/bin/lms"
if [ -x "$LMS" ]; then
    "$LMS" server stop 2>/dev/null || true
    "$LMS" daemon down 2>/dev/null || true
    echo "    ✓ LM Studio stopped"
fi
# Remove LM Studio if we installed it (managed marker exists)
if [ -f "$STACKLET_DIR/.state/lmstudio-managed" ] && [ -d "$HOME/.lmstudio" ]; then
    rm -rf "$HOME/.lmstudio"
    echo "    ✓ LM Studio uninstalled"
fi

# ── Whisper ───────────────────────────────────────────────────────────────
PLIST="$HOME/Library/LaunchAgents/dev.famstack.whisper.plist"
if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "    ✓ Whisper launchd service removed"
fi
rm -f "$DATA_DIR/ai/famstack-whisper"

# ── Clean up state ────────────────────────────────────────────────────────
rm -rf "$STACKLET_DIR/.state"
