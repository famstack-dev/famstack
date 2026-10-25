"""Asking the curator for its work now, and waiting until it is done.

The curator is the only writer of the brain projection (ADR-011), so no
command mirrors or regenerates anything itself. Each drops a trigger file
the curator's tick loop watches and waits for the record the curator
leaves when it has finished. A timeout is not a lost request: the trigger
stays until the curator is free to take it.

Three depths, each including the one before:

    mirrored     the source vault's HEAD is in the brain projection
    pages        the wiki pages that change touched are regenerated
    everything   the nightly rebuild: diary, source reconcile, every page

While it waits, a command prints what the curator reports doing: each
step, and each page it writes or skips.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path

from ..memory_api import CuratorDown, MemoryAPI, Refused
from ..lib import (
    curator_state_dir_for,
    failed_rebuild_since,
    progress_since,
    request_mirror,
    request_nightly,
    vault_path_for,
    wait_for_mirror,
    wait_for_nightly,
    wait_for_rebuilt,
)

# Longer than a write's own few seconds: a person at a terminal asked for
# this and would rather wait than re-run it.
MIRROR_WAIT_SECS = 40.0
# Regenerating the touched pages is a few LLM calls per member.
PAGES_WAIT_SECS = 600.0
# Everything is an LLM call per page plus the diary; half an hour covers
# a family's worth of pages on a slow model.
EVERYTHING_WAIT_SECS = 1800.0
POLL_INTERVAL = 0.75


# What the curator's sync may answer while its clone has everything there
# is: pulled, or Forgejo down, or Forgejo refusing only the push.
_CURRENT = ("ok", "unreachable", "refused")


def pull_now(config: Mapping) -> str:
    """Have the curator pull Forgejo's latest into its clone before a
    command reads it; "" when it has, else a line saying the reads may be
    behind and why.

    The clone is the curator's: it is the one process that changes it
    (ADR-019), so a command asks instead of pulling it from the host. A
    curator that cannot be asked is no reason to refuse a read: what it
    has is read, and the line says so. Forgejo itself being down is no news:
    every write goes through the curator, so its clone has them all, and
    saying so on every call would only be noise.
    """
    try:
        synced = MemoryAPI.from_host(config).sync()
    except (CuratorDown, Refused) as e:
        return f"[memory] could not ask the curator for Forgejo's latest ({e}); reading what it has"
    if synced.state in _CURRENT:
        return ""
    return (f"[memory] the curator could not pull Forgejo's latest ({synced.state}: "
            f"{synced.detail}); reading what it has")


def synced_head(config: Mapping) -> tuple[str, str]:
    """The commit the curator's clone stands at once it has pulled Forgejo's
    latest, and "", or "" and why it cannot be had.

    It is what the projection has to reach: a hand edit pushed from
    Obsidian is the change an operator runs `stack memory sync` to bring
    through, and every other write went through the curator. With Forgejo
    down the clone still has every write, so its head is the target then
    too.
    """
    try:
        synced = MemoryAPI.from_host(config).sync()
    except (CuratorDown, Refused) as e:
        return "", f"could not ask the curator for the memory's latest commit: {e}"
    if not synced.head:
        return "", f"the curator's clone has no commit yet ({synced.state}: {synced.detail})"
    return synced.head, ""


class Vault:
    """The source vault and the curator's state dir of one instance.

    Resolved from the plugin config; `error` says why it cannot be used.

        vault = Vault.of(config)
        if vault.error:
            return {"error": vault.error}
    """

    def __init__(self, memory: Path, state_dir: Path, error: str = ""):
        self.memory, self.state_dir, self.error = memory, state_dir, error

    @classmethod
    def of(cls, config: dict) -> "Vault":
        data_dir = config.get("data_dir")
        if not data_dir:
            return cls(Path(), Path(), "stack data_dir not configured")
        memory = vault_path_for(Path(data_dir))
        state_dir = curator_state_dir_for(Path(data_dir))
        if not (memory / ".git").exists():
            return cls(memory, state_dir, f"memory vault not cloned at {memory}")
        return cls(memory, state_dir)



class Echo:
    """Prints the curator's progress lines recorded since a moment, once each.

    Handed to a wait as its `on_poll`, so the lines appear while it waits.

        echo = Echo(vault.state_dir, time.time())
        wait_for_rebuilt(..., on_poll=echo)
    """

    def __init__(self, state_dir: Path, since: float):
        self.state_dir, self.since, self.shown = state_dir, since, 0

    def __call__(self) -> None:
        steps = progress_since(self.state_dir, self.since)
        if len(steps) < self.shown:
            self.shown = 0  # the curator started a new piece of work
        for step in steps[self.shown:]:
            print(f"  {step}", flush=True)
        self.shown = len(steps)


def mirrored(vault: Vault, target: str) -> tuple[str | None, str]:
    request_mirror(vault.state_dir)
    sha = wait_for_mirror(vault.state_dir, vault.memory, target,
                          timeout=MIRROR_WAIT_SECS, interval=POLL_INTERVAL)
    if sha is None:
        return None, (f"curator did not mirror {target[:10]} within {int(MIRROR_WAIT_SECS)}s "
                      "- is the memory stacklet running (or busy rebuilding the wiki)?")
    print(f"Brain projection current at {sha[:10]} (source {target[:10]})")
    return sha, ""


def pages(vault: Vault, target: str, since: float) -> tuple[str | None, str]:
    """Wait for the touched pages, printing what the curator did since `since`."""
    echo = Echo(vault.state_dir, since)
    sha = wait_for_rebuilt(vault.state_dir, vault.memory, target,
                           timeout=PAGES_WAIT_SECS, interval=POLL_INTERVAL, on_poll=echo,
                           asked=since)
    echo()
    if sha is None and (failure := failed_rebuild_since(vault.state_dir, since)):
        minutes = max(1, round((failure["retry_at"] - time.time()) / 60))
        return None, ("regenerating the wiki pages failed; the curator tries again in "
                      f"{minutes} minutes. See the lines above, or ./stack logs memory")
    if sha is None:
        return None, (f"curator did not regenerate the wiki pages for {target[:10]} within "
                      f"{int(PAGES_WAIT_SECS)}s - is [memory] wiki_auto_rebuild off, or the AI down?")
    print(f"Wiki pages current at {sha[:10]}")
    return sha, ""


def everything(vault: Vault) -> tuple[int | None, str]:
    """The nightly rebuild, now; how many seconds it took."""
    asked = request_nightly(vault.state_dir)
    print("Asked the curator to rebuild the whole wiki; waiting until it has finished...")
    echo = Echo(vault.state_dir, asked)
    record = wait_for_nightly(vault.state_dir, asked, timeout=EVERYTHING_WAIT_SECS, on_poll=echo)
    echo()
    if record is None:
        return None, (f"the curator did not finish a rebuild within {int(EVERYTHING_WAIT_SECS)}s "
                      "- is the memory stacklet running, and [memory] wiki_auto_rebuild on?")
    took = round(record["at"] - asked)
    if not record.get("ok"):
        return None, (f"the rebuild ran ({took}s) but regenerating the wiki pages failed; "
                      "see ./stack logs memory")
    print(f"Wiki rebuilt in {took}s")
    return took, ""
