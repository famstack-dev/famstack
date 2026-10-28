"""The curator's loop as memory's test instance runs it: its own code, a process of its own.

In production the curator is a container whose `main` sleeps and turns
(`curator.Cycle`). The instance (`RunningMemory.curators_loop`) starts this
script in place of the sleeping, with the environment the curator's
container has, and it takes one step of the loop:

    python -s curators_loop.py <repo> boot     # `Cycle.start`
    python -s curators_loop.py <repo> turns    # turn after turn until quiet
    python -s curators_loop.py <repo> night    # ask for the night, then turns;
                                               # fails unless the night worked

"Quiet" is what the loop reaches when nothing more happens: the memory
mirrored, the views compiled to its head with the model, nothing running.
Between turns it waits, as `main` sleeps, for a compile with the model to
end. What a turn remembers across processes is in the curator's state
files, as across a restart.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

# A loop that is not quiet after this many turns is stuck, not slow.
_MOST_TURNS = 20
# How often the waiting looks whether the compile with the model has ended.
_WAKE_SECS = 0.05


async def quiet(cycle) -> None:
    """Turn until the loop is quiet; raise when it does not get there."""
    for _ in range(_MOST_TURNS):
        await cycle.turn(forced=False)
        while cycle.recompile.busy and not cycle.recompile.ended:
            await asyncio.sleep(_WAKE_SECS)
        if not cycle.recompile.busy and await cycle.vault.head() == cycle.last:
            return
    raise SystemExit(f"the curator's loop is not quiet after {_MOST_TURNS} turns")


async def main(step: str) -> None:
    from stacklets.memory.bot.curator import Cycle
    from stacklets.memory.lib import NIGHTLY_RUN_NAME, request_nightly

    cycle = Cycle.from_environment(threading.Event())
    await cycle.start()
    if step == "night":
        asked = request_nightly(cycle.state_dir)
    if step in ("turns", "night"):
        await quiet(cycle)
    if step == "night":
        night_ran(cycle.state_dir / NIGHTLY_RUN_NAME, asked)


def night_ran(record: Path, asked: float) -> None:
    """Fail unless the night asked for at `asked` ran and rebuilt the wiki:
    a loop that is quiet after a failed night is not a night that worked."""
    try:
        night = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit("the night was asked for and never ran") from None
    if night.get("at", 0) < asked:
        raise SystemExit("the night was asked for and never ran")
    if not night.get("ok"):
        raise SystemExit(f"the night ran and failed: {night}")


if __name__ == "__main__":
    repo, step = Path(sys.argv[1]), sys.argv[2]
    from stack.stacklet_code import import_stacklets

    # As every entry point does: memory's code is the package `stacklets.memory`.
    import_stacklets([repo / "stacklets"])
    asyncio.run(main(step))
