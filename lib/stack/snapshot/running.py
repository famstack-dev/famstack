"""Whether a stacklet has anything up, as the snapshot commands ask it.

    from stack.snapshot.running import anything_up

    up = anything_up(stck)
    up("docs")    # True while Paperless restarts and Postgres runs

A snapshot clones a stacklet's data folder, and a rollback replaces it.
Both need the stacklet stopped: a database server that writes while its
folder is cloned leaves a torn database. They ask a wider question
than `Stack.is_running`, because that one counts a "failing" stacklet as
not running. A failing stacklet can have a crashed web container and a
database server that still writes.

Three callers ask here: the restart (`stack.snapshot.release`),
`stack snapshot create` (`stack.snapshot.cli`) and the rollback
(`stack.snapshot.rollback_steps`). Because they ask here, the three
agree on what counts as up.
"""

from __future__ import annotations

from typing import Callable

from .. import docker

__all__ = ["anything_up"]


def anything_up(stck) -> Callable[[str], bool]:
    """Whether a stacklet has anything up: a container of any state but
    stopped, or the host services of a stacklet without containers.

    Lists the containers once, at the call. The answer is the state at
    that moment. Call it again after a stop.

        up = anything_up(stck)
        up("docs")    # True while Paperless restarts and Postgres runs
        up("photos")  # False: its containers are stopped
    """
    with_a_container = docker.ids_with_a_container_up()
    return lambda sid: sid in with_a_container or stck.is_running(sid)
