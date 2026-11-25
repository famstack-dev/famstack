"""What the snapshot commands ask of the stack's command line.

`stack snapshot create` stops its stacklets, takes the snapshot and
starts them again. It also records the version that runs.
`stack rollback` stops the stacklets it replaces. The stop, the start
and the version belong to the command line (`stack.cli`). Because
`stack.cli` imports this package, no module here imports `stack.cli`.
The dispatcher hands them in instead:

    # in stack.cli, the dispatcher
    cli = CLI(stck)
    snapshot_cli.run(stck, args, Lifecycle(down=cli.down_many, up=cli.up_many,
                                           version=lambda: running_version_of(stck)))

    # in stack.snapshot
    stopped = lifecycle.down(["docs"])   # {"ok": True, ...}, dependents first
    lifecycle.version()                  # "0.4.0-rc.2-3-g9a5bdc9"

The imports go one way: `stack.cli` imports `stack.snapshot`, and the
snapshot package imports only the modules below the command line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

__all__ = ["Lifecycle"]


@dataclass(frozen=True)
class Lifecycle:
    """The stop, the start and the version a snapshot command needs.

    `down(ids)` and `up(ids)` are `CLI.down_many` and `CLI.up_many`:
    they return `{"ok": ..., "errors": [...]}`, and `up` also
    `"started"`. `version()` is the version a person reads,
    `stack.cli.running_version_of`.

        lifecycle.up(["docs"])   # {"ok": True, "started": ["docs"]}
    """

    down: Callable[[list[str]], dict]
    up: Callable[[list[str]], dict]
    version: Callable[[], str]
