"""Generate the agent's Matrix login password on first install.

The core bot-runner reads `agent__AGENT_PASSWORD` to create the @agent Synapse
account; the agent container reads the same secret (via the mounted secrets
store) to log nanobot in. Generating it once here gives both sides a single
source of truth. Idempotent — leaves an existing secret untouched.
"""

import secrets as _secrets

from stack.prompt import done


def run(ctx):
    if ctx.secret("AGENT_PASSWORD"):
        return
    ctx.secret("AGENT_PASSWORD", _secrets.token_urlsafe(12))
    done("Generated @agent login secret")
