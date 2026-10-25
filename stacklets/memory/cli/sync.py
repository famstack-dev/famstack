"""stack memory sync - mirror memory source into the brain projection now.

Every write through the curator is answered once the projection has it, or
after about five seconds with "not yet". This command asks for the mirror
with an operator's patience instead of an agent's, and says out loud how
far the projection got.

It stops at the mirror. `stack memory wiki update` goes on to the pages.

The curator is the only writer of the brain projection (ADR-011), so this
mirrors nothing itself: it asks the curator and waits (`_curator.py`). If
the curator is down or busy past the wait, this times out with a nonzero
exit, and the curator still takes the request on its next free tick.
"""

from __future__ import annotations


from ._curator import Vault, mirrored, synced_head

HELP = "Mirror memory source into the brain projection now"


def run(args, stacklet, config):
    vault = Vault.of(config)
    if vault.error:
        return {"error": vault.error}
    target, err = synced_head(config)
    if err:
        return {"error": err}
    sha, err = mirrored(vault, target)
    if err:
        return {"error": err}
    return {"mirrored": sha, "target": target}
