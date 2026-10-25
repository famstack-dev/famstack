"""stack memory pull — have the curator pull the latest memory from Forgejo now.

The curator holds the memory's clone and is the one process that changes it
(ADR-019). A hand edit made in Forgejo's web editor or an Obsidian clone
reaches the clone when Forgejo tells the curator of the push, or at its next
cycle, half a minute later at most. This command asks for it now, and says
how the clone stands against Forgejo:

- up to date;
- changes made while Forgejo was down wait in the clone, and go out once
  Forgejo is back (the curator tries every cycle; this command tries now);
- the clone and Forgejo diverged, and the changes wait for a person to
  decide; nothing is pushed or reset until then.

Exit 0 unless the curator cannot be asked, or the clone needs a person.
"""

from __future__ import annotations

from memory_api import CuratorDown, MemoryAPI, Refused

HELP = "Have the curator pull the latest memory from Forgejo now"

_NEEDS_A_PERSON = ("diverged", "missing", "failed")


def run(args, stacklet, config):
    try:
        synced = MemoryAPI.from_host(config).sync()
    except (CuratorDown, Refused) as e:
        return {"error": str(e)}
    waiting = [c["subject"] for c in synced.waiting]
    print(_told(synced.state, synced.detail, waiting))
    answer = {"state": synced.state, "detail": synced.detail, "waiting": waiting}
    if synced.state in _NEEDS_A_PERSON:
        return {"error": f"the memory's clone needs a person: {synced.detail}", **answer}
    return {"ok": True, **answer}


def _told(state: str, detail: str, waiting: list[str]) -> str:
    """What the admin is told of the clone, one sentence and the waiting changes."""
    count = f"{len(waiting)} change{' waits' if len(waiting) == 1 else 's wait'}"
    listed = "".join(f"\n  {subject}" for subject in waiting)
    if state == "ok":
        return "The memory is up to date with Forgejo."
    if state == "unreachable":
        return (f"Forgejo does not answer ({detail}). {count} in the curator's clone and "
                f"go out once Forgejo is back.{listed}")
    if state == "refused":
        return f"Forgejo did not take the push ({detail}). {count} in the curator's clone.{listed}"
    if state == "diverged":
        return (f"The curator's clone and Forgejo diverged ({detail}). {count} for a person to "
                f"decide; nothing is pushed or reset until then.{listed}")
    return f"The memory's clone cannot be synced: {detail}"
