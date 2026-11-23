"""The host API's callers and their tokens, for tests that drive the API.

The host API (`stacklets/core/famstack-api.py`) answers only a caller that
sends its own token: core's opens the JSON surface, the agent's the
plaintext one. The API reads them from the instance's secret store on
every request, where `stack up core` and `stack up agent` generate them.
A test that drives the API gives its instance both tokens with
`give_tokens`, and sends one the way the caller does:

    give_tokens(instance)                     # instance = STACK_DIR
    monkeypatch.setenv("STACK_API_TOKEN", CORE_TOKEN)   # as core's containers have it
    ask(api, {"cmd": "list", "token": CORE_TOKEN})

The agent's memory reads also carry the people in its room, signed with
the agent's audience key, which `give_tokens` stores as well. `signed`
signs them as the agent's runtime does:

    send(api, f"token {AGENT_TOKEN} audience {signed('marge')} memory find gift")
"""

from __future__ import annotations

import time
from pathlib import Path

from stack.agent_audience import AUDIENCE_KEY, sign
from stack.secrets import TomlSecretStore

__all__ = ["AGENT_TOKEN", "AUDIENCE_SECRET", "CORE_TOKEN", "TOKEN_NAME", "give_tokens", "signed"]

# The secret's name in each calling stacklet's namespace, and the variable
# its containers read it from.
TOKEN_NAME = "STACK_API_TOKEN"
CORE_TOKEN = "core-token-0f3a9c"
AGENT_TOKEN = "agent-token-7b21e4"
AUDIENCE_SECRET = "agent-audience-key-3d90a1"


def give_tokens(instance: Path) -> None:
    """Store core's and the agent's token in `instance`'s secret store,
    as their first `stack up` does."""
    store = TomlSecretStore(Path(instance) / ".stack" / "secrets.toml")
    store.set("core", TOKEN_NAME, CORE_TOKEN)
    store.set("agent", TOKEN_NAME, AGENT_TOKEN)
    store.set("agent", AUDIENCE_KEY, AUDIENCE_SECRET)


def signed(*people: str) -> str:
    """`people` as the agent's runtime signs them for a command it starts now."""
    return sign(people, AUDIENCE_SECRET, now=time.time())
