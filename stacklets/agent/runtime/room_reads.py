"""room_reads: what the brief reads of the memory, for the people in a room.

    reads = RoomReads(["marge"])
    reads.profile("marge")         # "Source: vault/marge/about.md\n\n# Marge ..." or ""
    reads.topics()                 # {"camping", "garden"}: topics with a page
    reads.lists_of("camping")      # [("packliste", 1)]: the topic's lists, open items counted

No part of the family memory is mounted into the agent's container: nanobot's file
tools and exec read every file in the container, around every audience
rule. So the runtime's brief (`brief.py`) reads the memory the way the
model's tools do: a `stack memory` command through the agent's `stack`
shim, with the room's people signed onto it (`room_people.audience_env`).
The host API then runs the read for those people alone, so Marge's own
page is in her own chat's brief and in no other room's.

Best-effort, like the brief: a command that fails, is refused or times out
answers "" or nothing, never raises. No people means no read at all: the
API would refuse it, and the admin's view is never the fallback.

Each call is one `./stack` run on the Mac, so the brief asks only what it
uses, and one `RoomReads` asks each command once: the brief asks for the
topics and a topic's lists twice (to find the room's topic, then to brief
it), and the second answer is the first.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable

from room_people import audience_env

__all__ = ["RoomReads"]

# A brief that waits longer than this goes without the line; the turn goes on.
READ_TIMEOUT_SECONDS = 20

# One line of `stack memory list show`: "packliste: Packliste, 1 open (...)".
_LIST_LINE = re.compile(r"^(?P<slug>[a-z0-9][a-z0-9-]*): .*?, (?P<open>\d+) open\b", re.M)


class RoomReads:
    """The memory as the people in one room may read it, through `stack memory`.

        RoomReads(["bart"]).profile("marge")    # "": Marge's page is hers alone
    """

    def __init__(self, people: Iterable[str]):
        self.people = tuple(people)
        self._answers: dict[tuple[str, ...], str] = {}

    def profile(self, person: str) -> str:
        """A person's page as `stack memory person show` prints it; "" when
        the room's people may not all read it, or there is none."""
        return self._read("memory", "person", "show", person, "--no-refresh")

    def topics(self) -> set[str]:
        """The topics with a page that the room's people may read."""
        return {line.strip() for line in self._read("memory", "topic").splitlines()
                if re.fullmatch(r"[a-z0-9][a-z0-9-]*", line.strip())}

    def lists_of(self, topic: str) -> list[tuple[str, int]]:
        """The lists whose `topics:` name `topic`, with their open items counted."""
        listed = self._read("memory", "list", "show", "--topic", topic)
        return [(m["slug"], int(m["open"])) for m in _LIST_LINE.finditer(listed)]

    def _read(self, *args: str) -> str:
        """What `stack <args>` printed, signed for the room's people; "" on any failure."""
        if args not in self._answers:
            self._answers[args] = self._run(args)
        return self._answers[args]

    def _run(self, args: tuple[str, ...]) -> str:
        # SAFETY: without people the read would be refused, and must never
        # be asked without them: no audience is the admin's view.
        if not self.people:
            return ""
        try:
            done = subprocess.run(["stack", *args], capture_output=True, text=True,
                                  timeout=READ_TIMEOUT_SECONDS,
                                  env=audience_env(dict(os.environ), self.people))
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout if done.returncode == 0 else ""
