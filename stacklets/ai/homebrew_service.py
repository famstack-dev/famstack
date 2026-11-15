"""famstack's oMLX of an earlier release: a Homebrew service in `~/.omlx`, and the move off it.

    from . import homebrew_service

    if homebrew_service.was_famstacks():   # an instance set up by 0.4.x
        homebrew_service.move(ctx)         # once, in the preparation (`prepare.py`)

Up to 0.4.4 famstack ran its oMLX as Homebrew's service
(`brew services start omlx`), which runs oMLX in `~/.omlx`, the folder
the oMLX app uses too. The marker `.state/omlx-managed` says that
famstack started it. `STATE_DIR` is the AI stacklet's one folder of such
markers; the stop and destroy hooks read their own marker there too. Since 0.5 famstack runs oMLX in a folder of its own
under its own LaunchAgent (`omlx_service.py`), so the first start after
the update moves over, once, in the preparation, so the downloads after
it go to famstack's own oMLX:

    ~/.omlx/model_settings.json ──copy──▶ <data_dir>/ai/omlx/model_settings.json
    brew services stop omlx                launchd no longer has its job:
                                           port 42060 is free again
    famstack's oMLX starts and answers     fails, or Ctrl-C? brew services start
                                           omlx: the old AI runs again, the
                                           marker stays
    the marker goes                        the move is done

Why the model settings: oMLX keeps its per-model settings, such as
thinking off for a Qwen3 model, in `model_settings.json` of its base
folder. A new folder starts without them, and the stack sets only
thinking off again by itself. The copy is made only when famstack's
folder has none, so a second start or a later change in oMLX's admin
page is never overwritten.

Why a note about the app: the old service wrote famstack's port 42060
and famstack's key into `~/.omlx/settings.json`. famstack never writes
the app's folder, so they stay there, and an oMLX app installed later
reads them and listens on famstack's port. The note says how to set it
back.

What it does not do: uninstall the formula, or change `~/.omlx`.
"""

from __future__ import annotations

import json
import shlex
import shutil
from collections.abc import Callable
from pathlib import Path

from stack.prompt import warn

from . import host_services, omlx_service
from .omlx_start import running

__all__ = ["STATE_DIR", "MARKER", "was_famstacks", "move", "carry_model_settings", "stop", "start_again", "moved",
           "apps_port_note"]

# The AI stacklet's markers of what an earlier release set up on this Mac.
STATE_DIR = Path(__file__).resolve().parent / ".state"
# How long the move waits for famstack's oMLX to answer before it starts
# the old one again: the guard's wait before every start (10 s,
# `omlx_service.GuardTiming`), oMLX's own start, and a first model load
# from a cold disk (up to 60 s for the default chat model), with 20 s to
# spare.
MOVE_READY_SECONDS = 90
# The marker an earlier release wrote when it started Homebrew's service.
MARKER = "omlx-managed"
# The file of per-model settings in an oMLX base folder (oMLX's
# `model_settings.py`).
MODEL_SETTINGS = "model_settings.json"
# The launchd job of Homebrew's oMLX service.
JOB = "homebrew.mxcl.omlx"
# The oMLX app's own port, which its settings name unless famstack's old
# service wrote its own there.
APP_PORT = 8000

Runner = Callable[[str], str]


def was_famstacks() -> bool:
    """Whether famstack still runs its oMLX as Homebrew's service: the
    marker an earlier release wrote is in `STATE_DIR`.

        was_famstacks()    # True on an instance set up by 0.4.4
    """
    return (STATE_DIR / MARKER).exists()


def move(ctx) -> None:
    """Move from an earlier release's Homebrew service to famstack's own
    oMLX: carry the model settings and the key `[ai]` sends into
    famstack's folder, so apps that use the key keep working, stop that
    service, and start famstack's oMLX (`omlx_start.running`). Only once
    it answers, within `MOVE_READY_SECONDS`, is the move done. When it does
    not start or answer, or the run is interrupted, the old service starts
    again, the marker stays, and the error is raised. `ctx` is a hook
    context.

    Raises `omlx_service.UnreadableSettings` for famstack's settings file
    it cannot read, before it changes anything.

        move(ctx)    # "Moving oMLX from Homebrew's service to famstack's own folder"
    """
    data_dir = Path(ctx.stack.data)
    settings = omlx_service.settings_file(data_dir)
    omlx_service.read_settings(settings)   # refuses an unreadable file before any change
    ctx.step("Moving oMLX from Homebrew's service to famstack's own folder")
    try:
        carry_model_settings(data_dir)
        # The key the family's apps send to the old service goes into a new
        # settings file, where `omlx.own_key` keeps it.
        if (key := ctx.cfg("openai_key", default="")) and not settings.exists():
            omlx_service.configure(data_dir, host=ctx.env.get("PORT_BIND_IP") or "127.0.0.1",
                                   key=key)
        stop(ctx.shell)
        with running(ctx, answer_seconds=MOVE_READY_SECONDS):
            pass
    except BaseException:
        # Also on Ctrl-C, from the moment the old service's stop began.
        # `running` already took back the oMLX it started; the old service
        # takes port 42060 only once famstack's oMLX let go.
        if not host_services.wait_until_stopped(ctx.shell, omlx_service.LABEL):
            warn("famstack's oMLX still runs, so the old oMLX may not get port 42060.")
        start_again(ctx.shell)
        raise
    moved()
    if note := apps_port_note():
        warn(note)


def carry_model_settings(data_dir: str | Path) -> bool:
    """Copy the old service's per-model settings from `~/.omlx` into
    famstack's oMLX folder, when the folder has none yet. Returns whether
    it copied. Reads `~/.omlx`, never writes there.

        carry_model_settings("/Users/homer/famstack-data")    # True once, False after
    """
    source = Path.home() / ".omlx" / MODEL_SETTINGS
    target = omlx_service.folder(data_dir) / MODEL_SETTINGS
    if not source.is_file() or target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return True


def stop(run: Runner) -> None:
    """Stop Homebrew's oMLX service, and make sure launchd let go of it:
    raises `RuntimeError` while its job still runs, because it would keep
    port 42060 and answer in place of famstack's oMLX. A `brew` that
    fails on a service that is already stopped is not an error.

        stop(ctx.shell)    # "brew services stop omlx", then launchd is asked
    """
    try:
        run("brew services stop omlx")
    except RuntimeError:
        pass
    try:
        run(shlex.join(["launchctl", "list", JOB]))
    except RuntimeError:
        return
    raise RuntimeError("Homebrew's oMLX service did not stop, so famstack's oMLX does not "
                       "start beside it on port 42060. Stop it with 'brew services stop omlx', "
                       "then run './stack up ai' again.")


def start_again(run: Runner) -> None:
    """Start Homebrew's oMLX service again, after famstack's own oMLX did
    not start: the AI the instance had keeps running.

        start_again(ctx.shell)    # "brew services start omlx"
    """
    try:
        run("brew services start omlx")
    except RuntimeError:
        pass


def moved() -> None:
    """Record that the move is done: the marker goes, so the next start
    neither stops Homebrew's service nor copies anything again.

        moved()    # was_famstacks() is False after
    """
    (STATE_DIR / MARKER).unlink(missing_ok=True)


def apps_port_note() -> str:
    """What to tell the admin when the oMLX app's settings still name
    famstack's port, from the old service; "" when they do not, or cannot
    be read.

        apps_port_note()
        # "The oMLX app's settings (/Users/homer/.omlx/settings.json) still name port 42060, ..."
    """
    path = Path.home() / ".omlx" / "settings.json"
    try:
        port = json.loads(path.read_text()).get("server", {}).get("port")
    except (OSError, ValueError, AttributeError):
        return ""
    if port != omlx_service.PORT:
        return ""
    return (f"The oMLX app's settings ({path}) still name port {omlx_service.PORT}, famstack's "
            f"port, from the earlier release. If you use the oMLX app, set its port back to "
            f"{APP_PORT} in its settings, because famstack's oMLX uses {omlx_service.PORT}.")
