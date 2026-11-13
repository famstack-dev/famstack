"""famstack's own oMLX: its folder, its settings and its LaunchAgent.

    from ..omlx_service import configure, settings_file, start

    configure(data_dir, host="0.0.0.0")       # port 42060, the shared models, the bind address
    start(ctx, data_dir, program, changed=True)   # the LaunchAgent written and (re)loaded
    settings_file(data_dir)                   # <data_dir>/ai/omlx/settings.json

The oMLX the stack installs (route 1, `[ai] provider = "managed"`) is
Homebrew's program, run by famstack in a folder of its own:

    <data_dir>/ai/omlx/              famstack's oMLX: settings.json (port 42060,
                                     bind address, models folder, key),
                                     model_settings.json, its log, its guard
    ~/.omlx/                         the oMLX app's: famstack never writes here
    ~/.omlx/models/                  the models, shared: nothing is downloaded twice
    ~/Library/LaunchAgents/
      dev.famstack.omlx.plist        runs the guard `famstack-omlx`, which runs
                                     `omlx serve --base-path <data_dir>/ai/omlx`

Why its own folder: oMLX reads its settings, key and port from a base
folder, `~/.omlx` unless `--base-path` names another. The oMLX app uses
`~/.omlx` too, so a famstack oMLX there read the app's key as its own and
moved the app to famstack's port. Why its own LaunchAgent: the Homebrew
formula's service runs a bare `omlx serve`, and `brew services` passes no
arguments, so it cannot name another folder. Its lifecycle is Whisper's
(`host_services.py`): written by content, loaded with `launchctl`,
removed by `stack down ai`, so launchd does not start it at the next
login.

Why a guard: two MLX servers on one Mac's graphics chip can crash the
Mac. `stack up ai` refuses to start famstack's oMLX while the oMLX app
runs (`omlx_app.py`), but launchd starts the agent at login without
asking the stack, and the app may be a login item too. So the agent runs
a small script that checks for the app first, and keeps checking while
oMLX runs (`guard`):

    the app runs at the start          ─▶ oMLX does not start, exit 0
    the app starts while oMLX runs     ─▶ oMLX stops, exit 0
    oMLX ends by itself (its restart)  ─▶ exit 1: launchd starts it again

launchd restarts the agent only after an exit that is not 0, so a guard
that steps aside stays down: no restart loop. `stack up ai` starts it
again once the app is closed, and the setup page says why the AI does
not answer meanwhile (`recommended.py`, `omlx_app`).

A settings file that is not JSON stops every caller before it writes
(`read_settings`): the file holds the key the family's apps use.

What it does not do: install oMLX (`hooks/on_install.py`), make its key
(`omlx.give_new_key`), move an instance off an earlier release's
Homebrew service (`homebrew_service.py`), or touch the oMLX app's
settings or any oMLX that famstack does not run.
"""

from __future__ import annotations

import json
import plistlib
from dataclasses import dataclass
import shlex
import time
import urllib.request
from pathlib import Path

from . import host_services, omlx_app

__all__ = ["LABEL", "PORT", "folder", "settings_file", "models_folder", "agent_file",
           "guard_file", "UnreadableSettings", "read_settings", "write_settings", "configure", "answers", "start",
           "restart", "restart_commands", "agent", "GuardTiming", "guard"]

# The LaunchAgent's label (`host_services`), beside Whisper's.
LABEL = host_services.OMLX
# The port of the oMLX the stack manages (ADR-007).
PORT = 42060
# The settings file holds the key: the admin's account reads it, nobody else.
SETTINGS_MODE = 0o600
# One question to oMLX's model list: on this Mac it answers in
# milliseconds once it is up.
ANSWER_SECONDS = 2
ANSWER_POLL_SECONDS = 1


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
    return host_services.agent_file(LABEL)




def guard_file(data_dir: str | Path) -> Path:
    """The guard script the LaunchAgent runs instead of oMLX itself
    (`guard`).

        guard_file("/Users/homer/famstack-data")    # Path(".../ai/omlx/famstack-omlx")
    """
    return folder(data_dir) / "famstack-omlx"


# ── Its settings ─────────────────────────────────────────────────────────

class UnreadableSettings(RuntimeError):
    """famstack's oMLX settings file exists and is not a JSON object. Its
    message names the file and says what to do; the caller stops before it
    changes anything, because the stack cannot keep settings it cannot read.

        raise UnreadableSettings(Path(".../ai/omlx/settings.json"), "line 1 column 30")
    """

    def __init__(self, path: Path, reason: str):
        super().__init__(
            f"The settings file of famstack's oMLX cannot be read: {path} ({reason}). "
            "Correct it or move it away, then run './stack up ai' again. Nothing changed.")
        self.path = path


def read_settings(path: Path) -> dict:
    """The settings in oMLX's settings file at `path`, `{}` when there is
    none yet. Raises `UnreadableSettings` for a file that is not a JSON
    object, so no caller writes over settings it could not read.

        read_settings(settings_file(data_dir))    # {"server": {"port": 42060, ...}, ...}
    """
    if not path.exists():
        return {}
    try:
        settings = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise UnreadableSettings(path, str(e)) from None
    if not isinstance(settings, dict):
        raise UnreadableSettings(path, "not a JSON object")
    return settings


def configure(data_dir: str | Path, *, host: str, key: str | None = None) -> bool:
    """Write what famstack decides into famstack's oMLX settings: `PORT`,
    the bind address `host`, the shared models folder and, when given,
    the key. Every other setting stays, so a setting changed in oMLX's
    admin page survives. Creates the folder and the file, readable by the
    admin only (`write_settings`). Returns whether
    the file changed; oMLX reads it at its start only. Raises
    `UnreadableSettings`, writing nothing, for a file it cannot read.

        configure(data_dir, host="0.0.0.0", key="7fk2-pq9w-m3xd-h8ra")   # True the first time
        configure(data_dir, host="0.0.0.0")                              # False after
    """
    path = settings_file(data_dir)
    before = read_settings(path)
    settings = json.loads(json.dumps(before))
    settings.setdefault("server", {}).update({"port": PORT, "host": host})
    settings.setdefault("model", {})["model_dir"] = str(models_folder())
    if key is not None:
        settings.setdefault("auth", {})["api_key"] = key
    if settings == before:
        if path.exists():
            path.chmod(SETTINGS_MODE)  # a file an earlier release left readable
        return False
    write_settings(path, settings)
    return True


def write_settings(path: Path, settings: dict) -> None:
    """Write oMLX's settings to `path`, readable by the admin only
    (`SETTINGS_MODE`): the file holds the key every app of the family
    sends. Creates the folder.

        write_settings(settings_file(data_dir), {"auth": {"api_key": "7fk2-pq9w-m3xd-h8ra"}})
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=SETTINGS_MODE, exist_ok=True)
    path.chmod(SETTINGS_MODE)
    path.write_text(json.dumps(settings, indent=2))


# ── Whether it answers ───────────────────────────────────────────────────

def answers(base_url: str, key: str, timeout: float) -> bool:
    """Whether the oMLX at `base_url` (`http://localhost:42060/v1`) lists its
    models within `timeout` seconds, asked with `key`: oMLX answers 401
    without it. The key goes in a request header, never on a command line.

        answers("http://localhost:42060/v1", "7fk2-pq9w-m3xd-h8ra", 30)   # True once it is up
    """
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(f"{base_url.rstrip('/')}/models",
                                     headers={"Authorization": f"Bearer {key}"})
    while True:
        try:
            with urllib.request.urlopen(request, timeout=ANSWER_SECONDS) as response:
                if response.status == 200:
                    return True
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(ANSWER_POLL_SECONDS)


# ── Its LaunchAgent ──────────────────────────────────────────────────────

def start(ctx, data_dir: str | Path, program: str, *, changed: bool) -> str:
    """Run famstack's oMLX for `program` (the oMLX binary) through its
    LaunchAgent (`host_services.start`): loaded when launchd does not have
    it, restarted when its settings `changed`, its agent changed or its
    process does not run. Returns what `host_services.start` returns.
    `ctx` is a hook context (`ctx.shell`).

        start(ctx, data_dir, "/opt/homebrew/opt/omlx/bin/omlx", changed=False)   # ""
    """
    return host_services.start(ctx.shell, agent(data_dir, program), changed=changed)


def restart(ctx) -> None:
    """Restart famstack's oMLX, so it reads its settings again.

        restart(ctx)    # ctx.shell("launchctl kickstart -k gui/501/dev.famstack.omlx")
    """
    host_services.restart(ctx.shell, LABEL)


def restart_commands() -> list[list[str]]:
    """The commands that restart famstack's oMLX, as argument lists, for a
    caller without a hook context (`stack ai key new`).

        restart_commands()
        # [["launchctl", "kickstart", "-k", "gui/501/dev.famstack.omlx"]]
    """
    return host_services.restart_commands(LABEL)


def agent(data_dir: str | Path, program: str, *, timing: GuardTiming | None = None,
          app: str = omlx_app.IN_THE_APP) -> host_services.Agent:
    """The LaunchAgent for `program`, run in famstack's folder through its
    guard (`guard`), with the guard script as the agent's file. `app` is
    what in a process's command line marks the oMLX app.

    `KeepAlive` restarts it only after an exit that is not 0: the guard
    exits 0 when it steps aside for the oMLX app or oMLX cannot start at
    all, and 1 when oMLX ended by itself after it ran. `OMLX_SUPERVISED`
    tells oMLX that a supervisor restarts it, as Homebrew's service says
    it, so the restart button of its admin page works: oMLX then ends
    itself and the guard exits 1. launchd gives no PATH of its own. Built
    with `plistlib`, so a path with `&` or `<` is written as it is.

        agent(data_dir, "/opt/homebrew/opt/omlx/bin/omlx").plist
        # '<?xml …<string>…/ai/omlx/famstack-omlx</string>…'
    """
    base = str(folder(data_dir))
    log = str(folder(data_dir) / "omlx.log")
    script = guard_file(data_dir)
    plist = plistlib.dumps({
        "Label": LABEL,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "WorkingDirectory": base,
        "ProgramArguments": [str(script)],
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            "OMLX_SUPERVISED": "launchd",
        },
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }, sort_keys=False).decode()
    return host_services.Agent(LABEL, plist,
                               files={script: guard(program, base, timing, app=app)})


@dataclass(frozen=True)
class GuardTiming:
    """The waits of the guard, in seconds.

    `settle`: the wait before every start, after which the guard looks for
    the oMLX app once. An app that is a login item can start a few seconds
    after this agent; macOS starts login items within that time on a Mac
    at rest. `poll`: how often it looks again while oMLX runs; the app's
    server needs longer than that to load a model. `startup`: an oMLX that
    ends sooner than that after its start could not start at all (a
    missing model folder, port 42060 taken), and is not started again
    until `stack up ai`.

        GuardTiming()                               # settle 10, poll 5, startup 15
        GuardTiming(settle=0, poll=0.2, startup=0)  # a test that waits for nothing
    """

    settle: float = 10
    poll: float = 5
    startup: float = 15


def guard(program: str, base: str, timing: GuardTiming | None = None, *,
          app: str = omlx_app.IN_THE_APP) -> str:
    """The script the LaunchAgent runs: famstack's oMLX, never beside the
    oMLX app.

        before every start   wait `settle`, then look for the app once:
                             it runs        ─▶ exit 0, oMLX does not start
                             program gone   ─▶ exit 0, the reason in the log
        while oMLX runs      look every `poll`:
                             the app runs   ─▶ stop oMLX, exit 0
        oMLX ended           within `startup` of its start ─▶ exit 0 (it
                             cannot start, for example port 42060 taken);
                             later ─▶ exit 1, launchd starts it again
        TERM (stack down)    stop oMLX at once, exit 0

    Every program is called by its full path, because launchd's PATH for
    the agent is only the one the agent names. The waits run in the
    background (`sleep & wait`), so a TERM is handled at once.

        guard("/opt/homebrew/opt/omlx/bin/omlx", "/Users/homer/famstack-data/ai/omlx")
        # '#!/bin/sh\\n…/usr/bin/pgrep -f oMLX.app/Contents/…'
    """
    timing = timing or GuardTiming()
    values = {"@APP@": shlex.quote(app), "@PROGRAM@": shlex.quote(program),
              "@BASE@": shlex.quote(base), "@SETTLE@": str(timing.settle),
              "@POLL@": str(timing.poll), "@STARTUP@": str(int(timing.startup))}
    script = GUARD
    for token, value in values.items():
        script = script.replace(token, value)
    return script


# The guard, as a shell script: launchd runs it with no Python of the
# stack's. Exit 0 means "stays down", 1 "start me again". A raw string
# with @NAME@ tokens, so the shell's own `$` and `\` stay as written.
GUARD = r"""#!/bin/sh
# famstack's oMLX, started by launchd (dev.famstack.omlx). Never beside the
# oMLX app: two AI servers on one Mac's graphics chip can crash the Mac.
app_runs() { /usr/bin/pgrep -f @APP@ >/dev/null; }
stays_down() { echo "$(/bin/date): $1"; exit 0; }
server=
trap 'if [ -n "$server" ]; then kill -TERM "$server" 2>/dev/null; wait "$server"; fi; exit 0' TERM INT

# An app that is a login item may start a moment after this agent.
/bin/sleep @SETTLE@ & wait $!
app_runs && stays_down "the oMLX app runs, so famstack's oMLX does not start."
[ -x @PROGRAM@ ] || stays_down "@PROGRAM@ is missing, so famstack's oMLX does not start. './stack up ai' installs it."

started=$(/bin/date +%s)
@PROGRAM@ serve --base-path @BASE@ &
server=$!
while kill -0 "$server" 2>/dev/null; do
  /bin/sleep @POLL@ & wait $!
  if app_runs; then
    kill -TERM "$server"; wait "$server"
    stays_down "the oMLX app started, so famstack's oMLX stopped."
  fi
done
wait "$server"
code=$?
if [ $(( $(/bin/date +%s) - started )) -lt @STARTUP@ ]; then
  stays_down "oMLX ended during its start (exit $code), for example because port 42060 is taken. See the lines above; './stack up ai' starts it again."
fi
exit 1
"""
