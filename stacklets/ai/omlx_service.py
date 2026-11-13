"""famstack's own oMLX: its folder, its settings and its LaunchAgent.

    from ..omlx_service import configure, settings_file, start

    configure(data_dir, host="0.0.0.0")       # port 42060, the shared models, the bind address
    start(ctx, data_dir, program, changed=True)   # the LaunchAgent written and (re)loaded
    settings_file(data_dir)                   # <data_dir>/ai/omlx/settings.json

The oMLX the stack installs (route 1, `[ai] provider = "managed"`) is
Homebrew's program, run by famstack in a folder of its own:

    <data_dir>/ai/omlx/              famstack's oMLX: settings.json (port 42060,
                                     bind address, models folder, key), its log
    ~/.omlx/                         the oMLX app's: famstack never writes here
    ~/.omlx/models/                  the models, shared: nothing is downloaded twice
    ~/Library/LaunchAgents/
      dev.famstack.omlx.plist        runs `omlx serve --base-path <data_dir>/ai/omlx`

Why its own folder: oMLX reads its settings, key and port from a base
folder, `~/.omlx` unless `--base-path` names another. The oMLX app uses
`~/.omlx` too, so a famstack oMLX there read the app's key as its own and
moved the app to famstack's port. Why its own LaunchAgent: the Homebrew
formula's service runs a bare `omlx serve`, and `brew services` passes no
arguments, so it cannot name another folder. The LaunchAgent follows the
whisper server's (`hooks/on_install.py`): written by content, loaded with
`launchctl`, kept alive by launchd.

What it does not do: install oMLX (`hooks/on_install.py`), make its key
(`omlx.give_new_key`), stop it (`hooks/on_stop.sh` unloads the agent and
removes it, so launchd does not start it again at login; `start` writes
it again), or touch the oMLX app's settings or any oMLX that famstack
does not run.
"""

from __future__ import annotations

import json
import plistlib
import shlex
from pathlib import Path

__all__ = ["LABEL", "PORT", "folder", "settings_file", "models_folder", "agent_file",
           "agent", "configure", "start", "restart", "restart_commands"]

# The LaunchAgent's label, beside the whisper server's `dev.famstack.whisper`.
LABEL = "dev.famstack.omlx"
# The port of the oMLX the stack manages (ADR-007).
PORT = 42060


# ── Where it lives ───────────────────────────────────────────────────────

def folder(data_dir: str | Path) -> Path:
    """famstack's oMLX folder, its `--base-path`.

        folder("/Users/homer/famstack-data")    # Path(".../famstack-data/ai/omlx")
    """
    return Path(data_dir) / "ai" / "omlx"


def settings_file(data_dir: str | Path) -> Path:
    """The settings file famstack's oMLX reads when it starts.

        settings_file("/Users/homer/famstack-data")
        # Path(".../famstack-data/ai/omlx/settings.json")
    """
    return folder(data_dir) / "settings.json"


def models_folder() -> Path:
    """The models folder famstack's oMLX shares with the oMLX app. Read from
    the home directory on each call, so a test's home is the one used.

        models_folder()    # Path("/Users/homer/.omlx/models")
    """
    return Path.home() / ".omlx" / "models"


def agent_file() -> Path:
    """The LaunchAgent that runs famstack's oMLX.

        agent_file()    # Path("/Users/homer/Library/LaunchAgents/dev.famstack.omlx.plist")
    """
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


# ── Its settings ─────────────────────────────────────────────────────────

def configure(data_dir: str | Path, *, host: str, key: str | None = None) -> bool:
    """Write what famstack decides into famstack's oMLX settings: `PORT`,
    the bind address `host`, the shared models folder and, when given,
    the key. Every other setting stays, so a setting changed in oMLX's
    admin page survives. Creates the folder and the file. Returns whether
    the file changed; oMLX reads it at its start only.

        configure(data_dir, host="0.0.0.0", key="7fk2-pq9w-m3xd-h8ra")   # True the first time
        configure(data_dir, host="0.0.0.0")                              # False after
    """
    path = settings_file(data_dir)
    before = json.loads(path.read_text()) if path.exists() else {}
    settings = json.loads(json.dumps(before))
    settings.setdefault("server", {}).update({"port": PORT, "host": host})
    settings.setdefault("model", {})["model_dir"] = str(models_folder())
    if key is not None:
        settings.setdefault("auth", {})["api_key"] = key
    if settings == before:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2))
    return True


# ── Its LaunchAgent ──────────────────────────────────────────────────────

def start(ctx, data_dir: str | Path, program: str, *, changed: bool) -> None:
    """Run famstack's oMLX: write its LaunchAgent for `program` (the oMLX
    binary) and load it when launchd does not have it; restart it when it
    runs and its settings `changed` or the agent changed. A loaded, unchanged
    oMLX is left running. `ctx` is a hook context (`ctx.shell`).

        start(ctx, data_dir, "/opt/homebrew/opt/omlx/bin/omlx", changed=False)
    """
    path = agent_file()
    content = agent(data_dir, program)
    rewritten = not path.exists() or path.read_text() != content
    if rewritten:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    if not _loaded(ctx):
        ctx.shell(f'launchctl load "{path}"')
    elif rewritten or changed:
        restart(ctx)


def restart(ctx) -> None:
    """Restart famstack's oMLX, so it reads its settings again: launchd
    unloads and loads its agent (`restart_commands`).

        restart(ctx)    # ctx.shell("launchctl unload …"), then "launchctl load …"
    """
    for command in restart_commands():
        # Every part quoted for the shell, so a home such as /Users/o'brien
        # stays one path.
        ctx.shell(shlex.join(command))


def restart_commands() -> list[list[str]]:
    """The commands that restart famstack's oMLX, as argument lists, for a
    caller without a hook context (`stack ai key new`).

        restart_commands()
        # [["launchctl", "unload", ".../dev.famstack.omlx.plist"],
        #  ["launchctl", "load", ".../dev.famstack.omlx.plist"]]
    """
    return [["launchctl", "unload", str(agent_file())], ["launchctl", "load", str(agent_file())]]


def agent(data_dir: str | Path, program: str) -> str:
    """The LaunchAgent's plist for `program`, run in famstack's folder.
    `OMLX_SUPERVISED` tells oMLX that launchd restarts it, as Homebrew's
    service says it, so the restart button of its admin page works.
    launchd gives no PATH of its own. Built with `plistlib`, so a path
    with `&` or `<` is written as it is.

        agent(data_dir, "/opt/homebrew/opt/omlx/bin/omlx")
        # '<?xml …<string>serve</string>\n\t\t<string>--base-path</string>\n\t\t<string>…/ai/omlx</string>…'
    """
    base = str(folder(data_dir))
    log = str(folder(data_dir) / "omlx.log")
    return plistlib.dumps({
        "Label": LABEL,
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": base,
        "ProgramArguments": [program, "serve", "--base-path", base],
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            "OMLX_SUPERVISED": "launchd",
        },
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }, sort_keys=False).decode()


def _loaded(ctx) -> bool:
    """Whether launchd has the agent: `launchctl list` exits 1 when not."""
    try:
        ctx.shell(f'launchctl list "{LABEL}"')
    except RuntimeError:
        return False
    return True
