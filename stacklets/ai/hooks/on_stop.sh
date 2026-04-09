#!/usr/bin/env bash
# stacklets/ai/down.sh — Stop native AI services
#
# Only stops services that famstack installed (*-managed marker exists).
# If the user installed a service themselves, we don't touch it.

set -euo pipefail

STACKLET_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$STACKLET_DIR/.state"

# ── oMLX ──────────────────────────────────────────────────────────────────
if [ -f "$STATE/omlx-managed" ]; then
    brew services stop omlx 2>/dev/null || true
    echo "    ✓ oMLX stopped"
fi

# ── LM Studio ────────────────────────────────────────────────────────────
if [ -f "$STATE/lmstudio-managed" ]; then
    LMS="$HOME/.lmstudio/bin/lms"
    if [ -x "$LMS" ]; then
        "$LMS" server stop 2>/dev/null || true
        "$LMS" daemon down 2>/dev/null || true
    fi
    echo "    ✓ LM Studio stopped"
fi

# ── Whisper ───────────────────────────────────────────────────────────────
PLIST="$HOME/Library/LaunchAgents/dev.famstack.whisper.plist"
if [ -f "$STATE/whisper-managed" ] && [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    echo "    ✓ Whisper stopped"
fi
