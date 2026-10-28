"""stack memory sync - mirror memory source into the brain projection now.

Every write already asks for the mirror on its own way out
(`propagate_write`, called from the write seam) and waits about five
seconds. This command asks with an operator's patience instead of an
agent's, and it says out loud how far the projection got. Unlike a write,
it also has the curator regenerate the pages the change touched at once,
instead of after the quiet window.

It stops waiting at the mirror. `stack memory wiki update` waits for the
pages too.

The curator is the only writer of the brain projection (ADR-011), so this
mirrors nothing itself: it asks the curator and waits (`_curator.py`). If
the curator is down or busy past the wait, this times out with a nonzero
exit, and the curator still takes the request on its next free tick.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _curator import Vault, mirrored  # noqa: E402

HELP = "Mirror memory source into the brain projection now"


def run(args, stacklet, config):
    vault = Vault.of(config)
    if vault.error:
        return {"error": vault.error}
    target = vault.target_head()
    if not target:
        return {"error": "cannot resolve memory HEAD"}
    sha, err = mirrored(vault, target)
    if err:
        return {"error": err}
    return {"mirrored": sha, "target": target}
