"""update_restart: the restart that applies a release after `stack update` moved the checkout.

    stack update                    move, then restart what the release changes
    stack update --with-snapshot    the same, with a snapshot before each start
    stack update --no-restart       move only; the output names the restarts

`stack.cli.handle_update` moves the checkout. This module says what the
update restarts, in its plan and after the move, and starts that restart:

    from stack import update_restart

    update_restart.refused(args)                          # None, or why the flags do not go together
    update_restart.print_plan(["docs"], changed, args)    # in the plan, before the question
    update_restart.apply(stck, ["docs"], changed, running, args)   # after the move: the exit code

Why it works this way:

- **The restart is `stack restart`, as a process of its own.** The
  update's own process imported the modules of the release it moved away
  from, and imports more of them lazily. A restart in that process would
  run old and new framework code side by side. A new process reads only
  the new release. It is the same restart the admin can run by hand, with
  its run locks, its snapshot and its refusals (`stack.cli.handle_restart`).
- **The update's question covers the restart, and only it.** The admin
  confirmed a plan that names what restarts, so the restart runs with
  `--yes` and with exactly those ids. The restart with no ids would pick
  its own list from the commit stamps: it adds a stacklet left stale by
  an earlier `--no-restart` and leaves out one without a stamp.
- **A snapshot only on request.** `--with-snapshot` passes on to the
  restart, and is refused together with `--no-restart`, which has no
  restart to pass it to. The restart then takes a snapshot of each
  stacklet that it moves to the new release (`stack.snapshot.release`).
  Nothing takes one on its own.
- **Only a release that changes what runs restarts anything.** A release
  that changes a stopped stacklet, or only the docs, restarts nothing.

`print_restart_call_to_action` is the line that `stack list` prints under
stale stacklets too, so an admin sees the same words in both places.
"""

from __future__ import annotations

import os
import subprocess
import sys

from .prompt import BOLD, DIM, ORANGE, RESET, TEAL
from .snapshot import store
from .updater import touched_stacklets, touches_framework

__all__ = ["apply", "print_plan", "print_restart_call_to_action", "refused"]


# ── The plan ─────────────────────────────────────────────────────────────

def refused(args) -> str | None:
    """Why the flags of `args` do not go together, or None.

        refused(Namespace(with_snapshot=True, no_restart=True))
        # "--with-snapshot needs the restart that --no-restart leaves out. ..."
    """
    if getattr(args, "with_snapshot", False) and getattr(args, "no_restart", False):
        return ("--with-snapshot needs the restart that --no-restart leaves out. "
                "Later, run: ./stack restart --with-snapshot")
    return None


def print_plan(targets: list[str], changed: list[str], args) -> None:
    """Print the plan's lines that name what the update restarts after
    the move, and whether a snapshot comes first. Prints nothing when
    nothing running changes.

        print_plan(["docs"], ["stacklets/docs/docker-compose.yml"], args)
        #   Restarts after the move: docs
        #   No snapshot first. To take one: stack update --with-snapshot
    """
    if not targets:
        return
    scope = "every running stacklet" if touches_framework(changed) else ", ".join(targets)
    if getattr(args, "no_restart", False):
        print(f"\n  Needs a restart afterwards: {scope}")
    elif getattr(args, "with_snapshot", False):
        print(f"\n  Restarts after the move: {scope}")
        print(f"  The restart takes a snapshot of each first, in {store.SNAPSHOTS_FOLDER}/ "
              f"of the data folder.")
    else:
        print(f"\n  Restarts after the move: {scope}")
        print(f"  {DIM}No snapshot first. To take one: stack update --with-snapshot{RESET}")


# ── After the move ───────────────────────────────────────────────────────

def apply(stck, targets: list[str], changed: list[str], running, args) -> int:
    """Apply the release that the checkout moved to: the exit code.

    Restarts `targets`, the plan's list, with `stack restart --yes
    <targets>`, and with `--with-snapshot` when `args` has it. When the
    framework changed, `targets` is every running stacklet
    (`stack.updater.restart_targets`). With `--no-restart`, prints the
    commands that apply the release later and returns 0. Restarts nothing
    and says so when no running stacklet changed (`targets` empty).
    """
    if not targets:
        idle = sorted(touched_stacklets(changed) - set(running))
        if idle:
            print(f"\n  {DIM}Nothing to restart. {', '.join(idle)} changed but "
                  f"is not running, and will pick this up on the next stack up.{RESET}\n")
        else:
            print(f"\n  {DIM}Nothing running was changed by this release.{RESET}\n")
        return 0
    if getattr(args, "no_restart", False):
        print_restart_call_to_action(targets, framework=touches_framework(changed))
        print(f"     {TEAL}./stack doctor{RESET}\n")
        return 0
    snapshot = ["--with-snapshot"] if getattr(args, "with_snapshot", False) else []
    restart = ["restart", *targets, "--yes", *snapshot]
    sys.stdout.flush()
    # The environment carries `PYTHONPATH` to the checkout's `lib/`, which
    # holds the new release now (the `stack` wrapper sets it).
    return subprocess.run([sys.executable, "-m", "stack", *restart],
                          cwd=stck.root, env=os.environ.copy()).returncode


def print_restart_call_to_action(targets, framework=False) -> None:
    """The one loud line that says the code moved and nothing applied it.

    Printed wherever the gap is visible: after `stack update --no-restart`,
    under a list that shows stale stacklets. Same wording every time,
    because an operator should recognise it rather than read it twice.
    """
    print(f"\n  {ORANGE}{BOLD}⚠  Code updated. A RESTART is required "
          f"to apply it.{RESET}")
    if framework:
        print(f"     {DIM}The framework changed, so this is everything running.{RESET}")
        print(f"     {TEAL}./stack restart{RESET}")
    else:
        print(f"     {TEAL}./stack restart{RESET}  "
              f"{DIM}or: {', '.join('./stack restart ' + t for t in targets[:3])}{RESET}")
    print(f"     {TEAL}./stack restart --with-snapshot{RESET}  "
          f"{DIM}takes a snapshot of each first{RESET}")
