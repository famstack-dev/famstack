"""stack memory sync - mirror memory source into the brain projection now.

Every write already asks for this on its own way out (`propagate_write`,
called from the write seam) and waits about five seconds. This command is
the same two steps with an operator's patience instead of an agent's: it
waits long enough to sit through a nightly sweep, and it says out loud
how far the projection got.

The curator is the ONLY writer of the brain projection (one-writer
invariant, ADR-011). This command therefore mirrors nothing itself: it
drops a `mirror-now` trigger file the curator's tick loop watches, then
waits until the curator records the current memory HEAD as mirrored.

Failure shape: if the curator is down (memory stacklet stopped) or busy
past the wait cap (a nightly LLM sweep can hold a cycle for minutes),
this times out with the lag printed and a nonzero exit. The trigger file
survives either way; the curator consumes it on its next free tick, so
the request is never lost.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    curator_state_dir_for,
    request_mirror,
    vault_local_head,
    vault_path_for,
    vault_remote_head,
    wait_for_mirror,
)

HELP = "Mirror memory source into the brain projection now"

# Wait cap per the test-loop rule: polls are cheap, caps are hard. Far
# longer than a write's own wait, because a person at a terminal asked
# for this by hand and would rather wait than re-run it.
WAIT_SECS = 40.0
POLL_INTERVAL = 0.75


def target_head(memory: Path) -> str:
    """The commit the mirror has to reach.

    Prefers the remote's HEAD: a hand edit pushed from Obsidian is
    exactly the change an operator runs this to bring through, and it is
    not in the local clone yet. Falls back to the local HEAD when Forgejo
    is unreachable - the curator pulls before mirroring, so a local-only
    target is still a valid best-effort floor.
    """
    return vault_remote_head(memory) or vault_local_head(memory) or ""


def run(args, stacklet, config):
    data_dir = config.get("data_dir")
    if not data_dir:
        return {"error": "stack data_dir not configured"}

    memory = vault_path_for(Path(data_dir))
    state_dir = curator_state_dir_for(Path(data_dir))

    if not (memory / ".git").exists():
        return {"error": f"memory vault not cloned at {memory}"}

    target = target_head(memory)
    if not target:
        return {"error": "cannot resolve memory HEAD"}

    request_mirror(state_dir)
    mirrored = wait_for_mirror(
        state_dir, memory, target, timeout=WAIT_SECS, interval=POLL_INTERVAL,
    )
    if mirrored is None:
        return {"error": (
            f"curator did not mirror {target[:10]} within {int(WAIT_SECS)}s "
            "- is the memory stacklet running (or busy in a nightly sweep)?"
        )}

    print(f"Brain projection current at {mirrored[:10]} (source {target[:10]})")
    return {"mirrored": mirrored, "target": target}
