#!/bin/sh
# entrypoint.sh — bring up the agent (nanobot) from stack.toml-derived env.
#
# 1. Refresh config.json from the image template so stack.toml changes (injected
#    as env) always take effect. nanobot resolves the ${VARS} against env on load.
# 2. Seed the personality on first run only, substituting the configurable name;
#    the family may edit it afterwards. The rules for answering (AGENTS.md)
#    refresh on every start, like the skills.
# 3. Hold nanobot back until the agent's chat account accepts a login with
#    AGENT_MATRIX_PASSWORD, which the stack puts into the environment: the
#    same secret the bot-runner provisions the account with.
set -e

# `[agent] runtime` names another stacklet: the bot runner answers as the
# agent's account with that stacklet's code. nanobot must not sign in as
# well, so the container waits instead.
if [ "${AGENT_RUNTIME:-nanobot}" != "nanobot" ]; then
    echo "[agent] runtime = ${AGENT_RUNTIME}: the bot runner answers as ${AGENT_NAME:-Stacky}; nanobot waits"
    exec sleep infinity
fi

NB="$HOME/.nanobot"
mkdir -p "$NB/workspace"

cp -f /app/config.json "$NB/config.json"

# The household language goes into the config as a value, not a ${VAR}:
# nanobot checks transcription.language against ^[a-z]{2,3}$ before it
# resolves variables, and refuses to start. Two letters, as the archivist
# sends it; none at all lets Whisper detect the language.
python3 - "$NB/config.json" "${AGENT_LANGUAGE:-}" <<'PY'
import json, sys
path, language = sys.argv[1], sys.argv[2].strip().lower()[:2]
config = json.load(open(path))
if language.isalpha():
    config.setdefault("transcription", {})["language"] = language
json.dump(config, open(path, "w"), indent=2)
PY

NAME="${AGENT_NAME:-Stacky}"

# Dream is off (config.json). What it wrote before is set aside once, so the
# seeding below puts the shipped profile back.
python3 /app/runtime/dream_aside.py "$NB" /app/workspace-seed "$NAME"

# USER.md is seeded so nanobot does not install its default profile
# template. The template costs ~1K prompt characters per request and holds
# no data: the runtime brief carries the per-turn speaker instead.
for f in SOUL.md USER.md; do
    [ -f "$NB/workspace/$f" ] || sed "s/__AGENT_NAME__/$NAME/g" "/app/workspace-seed/$f" > "$NB/workspace/$f"
done

# AGENTS.md is the product's rules for answering, not the family's: seeded
# once, a release that changes them would never reach an existing install.
sed "s/__AGENT_NAME__/$NAME/g" /app/workspace-seed/AGENTS.md > "$NB/workspace/AGENTS.md"

# Product-defined skills always refresh from the image seed (not user-editable).
if [ -d /app/workspace-seed/skills ]; then
    mkdir -p "$NB/workspace/skills"
    cp -r /app/workspace-seed/skills/. "$NB/workspace/skills/"
fi

# nanobot transcribes only with a key set; a Whisper on this Mac needs none and
# ignores whatever it is sent.
export AGENT_WHISPER_KEY="${AGENT_WHISPER_KEY:-none}"

# Validate any saved Matrix session before nanobot trusts it. nanobot reloads a
# stale token and then goes deaf on M_UNKNOWN_TOKEN - it never re-auths. Our
# MicroBots avoid this by validating the restored session with /whoami and
# clearing it on failure (stacklets/core/bot-runner/microbot.py; the same stdlib
# check is in stacklets/messages/cli/_matrix.py). nanobot exposes no seam to reuse
# that code from in here, so we mirror the one HTTP call: if whoami rejects the
# saved token, drop the session and let nanobot do a fresh password login. A valid
# session is kept, so there is no device churn.
SESS="$NB/matrix-store/session.json"
if [ -f "$SESS" ] && ! python3 - "$SESS" "${AGENT_MATRIX_HOMESERVER:-}" <<'PY'
import json, sys, urllib.request
tok = json.load(open(sys.argv[1])).get("access_token") or ""
req = urllib.request.Request(
    sys.argv[2].rstrip("/") + "/_matrix/client/v3/account/whoami",
    headers={"Authorization": "Bearer " + tok})
with urllib.request.urlopen(req, timeout=10):  # raises on 401; closes the response
    pass
PY
then
    echo "stale Matrix session -> clearing for a fresh login"
    rm -rf "$NB/matrix-store"
fi

# Without a saved session nanobot logs in with the password, once. On a first
# start the account does not exist yet: core's bot runner creates it when core
# refreshes at the end of `./stack up`, so this wait must stay in the container,
# where nothing in `up` waits for it. A saved session needs no login and skips it.
# -I keeps the runtime folder off the path: its sitecustomize.py loads and patches
# nanobot at every interpreter start, and an attempt needs none of that.
LOGIN_RETRY_SECONDS=10
ATTEMPTS_PER_LOG_LINE=6
if [ ! -f "$SESS" ]; then
    # No number of attempts can succeed without a password. Only the agent's
    # on_install writes it, and only a first run runs that hook, which the
    # setup-done marker's absence decides; `stack setup` runs another hook.
    if [ -z "${AGENT_MATRIX_PASSWORD:-}" ]; then
        echo "no chat password: the secret agent__AGENT_BOT_PASSWORD is missing from .stack/secrets.toml. Delete .stack/agent.setup-done, then run './stack up agent': its install writes the secret."
        exit 1
    fi
    # The wait has no limit: the container restarts unless stopped, so an exit
    # would only start it again. The server answers M_FORBIDDEN for a missing
    # account and for a wrong password alike, so the line names both causes.
    attempts=0
    until python3 -I /app/runtime/try_login.py; do
        if [ $((attempts % ATTEMPTS_PER_LOG_LINE)) -eq 0 ]; then
            echo "waiting for the chat account @${AGENT_BOT_ID}:${AGENT_MATRIX_SERVER_NAME}: the account does not exist yet (core's bot runner creates it), or the password does not match the account"
        fi
        attempts=$((attempts + 1))
        sleep "$LOGIN_RETRY_SECONDS"
    done
fi

# Verbose runtime logs (tool calls, agent decisions) when AGENT_VERBOSE=1.
if [ "${AGENT_VERBOSE:-0}" = "1" ]; then
    exec nanobot gateway --verbose
fi
exec nanobot gateway
