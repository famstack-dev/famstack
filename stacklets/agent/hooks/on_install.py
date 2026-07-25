"""Generate the agent's Matrix login password on first install.

The core bot-runner reads `agent__AGENT_BOT_PASSWORD` to create the agent's
Synapse account; the agent container reads the same secret (via the mounted
secrets store) to log nanobot in. The key is fixed (not derived from the bot's
handle) so renaming the agent never touches its credential. Generating it once
here gives both sides a single source of truth. Idempotent.
"""

import secrets as _secrets

from stack.prompt import done


def run(ctx):
    if ctx.secret("AGENT_BOT_PASSWORD"):
        return
    ctx.secret("AGENT_BOT_PASSWORD", _secrets.token_urlsafe(12))
    done("Generated agent login secret")
