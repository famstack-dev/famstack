#!/bin/sh
# entrypoint.sh — bring up the agent (nanobot) from stack.toml-derived env.
#
# 1. Refresh config.json from the image template so stack.toml changes (injected
#    as env) always take effect. nanobot resolves the ${VARS} against env on load.
# 2. Seed the personality on first run only, substituting the configurable name;
#    the family (and Dream) may edit it afterwards.
# 3. Read @stacky-bot's Matrix password from the mounted secrets store, the same
#    secret the bot-runner provisions the account with.
set -e

NB="$HOME/.nanobot"
mkdir -p "$NB/workspace"

cp -f /app/config.json "$NB/config.json"

NAME="${AGENT_NAME:-Stacky}"
for f in SOUL.md AGENTS.md; do
    [ -f "$NB/workspace/$f" ] || sed "s/__AGENT_NAME__/$NAME/g" "/app/workspace-seed/$f" > "$NB/workspace/$f"
done

# Product-defined skills always refresh from the image seed (not user-editable).
if [ -d /app/workspace-seed/skills ]; then
    mkdir -p "$NB/workspace/skills"
    cp -r /app/workspace-seed/skills/. "$NB/workspace/skills/"
fi

SECRETS="/setup-state/secrets.toml"
if [ -f "$SECRETS" ]; then
    AGENT_MATRIX_PASSWORD="$(python3 -c "import tomllib; print(tomllib.load(open('$SECRETS','rb')).get('agent__STACKY_BOT_PASSWORD',''))" 2>/dev/null || true)"
    export AGENT_MATRIX_PASSWORD
fi

# Verbose runtime logs (tool calls, agent decisions) when AGENT_VERBOSE=1.
if [ "${AGENT_VERBOSE:-0}" = "1" ]; then
    exec nanobot gateway --verbose
fi
exec nanobot gateway
