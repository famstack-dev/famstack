#!/usr/bin/env bash
# stacklets/ai/down.sh — Stop native AI services
#
# Only stops services that famstack installed (*-managed marker exists).
# If the user installed a service themselves, we don't touch it.

set -euo pipefail

STACKLET_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$STACKLET_DIR/.state"

# ── oMLX ──────────────────────────────────────────────────────────────────
# famstack's own oMLX runs under its own LaunchAgent (omlx_service.py).
# launchd loads every agent in LaunchAgents at login, so the agent goes
# too and the oMLX stays down; `stack up ai` writes it again.
OMLX_PLIST="$HOME/Library/LaunchAgents/dev.famstack.omlx.plist"
if [ -f "$OMLX_PLIST" ]; then
    launchctl unload "$OMLX_PLIST" 2>/dev/null || true
    rm -f "$OMLX_PLIST"
    echo "    ✓ oMLX stopped"
fi
# An earlier release ran it as a Homebrew service, which the marker names.
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
