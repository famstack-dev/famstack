"""stack ai key: the key of the oMLX the stack manages.

    stack ai key                  # lists the verbs
    stack ai key show             # whether the key is still `local`, and whether `[ai]` has it
    stack ai key new              # a new key in oMLX and the stack, applied, and waits
    stack ai key new --no-wait    # the same, and the stacklets restart on their own

oMLX takes a request only with its key. Before keys were generated the
stack installed it with `local`, which every device at home can send
(`omlx.py`). `show` says whether this instance still has it, and whether
`[ai] openai_key` holds the same key as oMLX: when not, every stacklet
that uses the AI gets 401. The AI's service card on the Mac asks it
through the host API and offers "Make a new key" while the key is
`local`; `stack doctor` asks it too. `new` first asks `stack.snapshot.guard`
whether each running stacklet that uses the key may start, and refuses,
naming `stack restart`, when one may not. Then it makes a key, writes it into oMLX's settings
and `[ai] openai_key`, restarts oMLX when it runs (it reads its key only
when it starts), asks it one short question once it answers again and
keeps the answer for the setup page (`answer.py`), and then starts every
running stacklet that renders the key into its containers again, so they
send the new one. Starting the
chat's bots, the agent and the memory recreates core as well, because
its rendered env changes (`stack up` refreshes core).

The dashboard runs `new --no-wait` through the host API: the restarts
then run on their own, so the answer with the key reaches the page
before core, which serves the page, is recreated. The terminal waits.

Only the oMLX the stack manages (`[ai] provider = "managed"`). The oMLX
app and servers elsewhere keep the key their admin set: `show` says
`managed: false` and `new` refuses.
"""

__all__ = ["HELP", "run"]

HELP = "The key of the oMLX the stack manages: show, new"

import os
import subprocess
import sys
from pathlib import Path

from .. import omlx_app
from ..omlx import PLACEHOLDER_KEY, give_new_key, managed_key
from ..omlx_service import restart_commands
from .switch import ai_consumers, running_stacklets

VERBS = {"show": "Whether the AI's key is still the placeholder 'local'",
         "new": "Make a new key for the AI, and apply it [--no-wait]"}

NOT_MANAGED = ("The stack did not install this AI server, so its key is the one its admin "
               "set. Change it there, then run './stack ai switch <url> --key <key>'.")

# How long oMLX's restart may take: launchd stops and starts famstack's
# LaunchAgent (`omlx_service.restart_commands`).
RESTART_OMLX_SECS = 60


def run(args, stacklet, config):
    """The verb's answer as a dict; the verbs without one.

        run(["show"], stacklet, config)   # {"managed": True, "placeholder": True,
                                          #  "matches": True}
        run(["new"], stacklet, config)    # {"key": "7fk2-pq9w-m3xd-h8ra",
                                          #  "applied": ["agent", "memory"]}
    """
    if not args or args[0] not in VERBS:
        return {"verbs": VERBS} if not args else {
            "error": f"Unknown verb '{args[0]}'. Use: {', '.join(VERBS)}"}
    ai = config.get("stack", {}).get("ai", {})
    managed = ai.get("provider") == "managed"
    if args[0] == "show":
        return _show(managed, config["data_dir"], ai.get("openai_key", ""))
    if not managed:
        return {"error": NOT_MANAGED}
    if args[1:] not in ([], ["--no-wait"]):
        return {"error": "Usage: stack ai key new [--no-wait]"}
    return _new(config, ai, wait=args[1:] != ["--no-wait"])


# ── show ─────────────────────────────────────────────────────────────────

def _show(managed: bool, data_dir: str, stack_key: str) -> dict:
    """Whether the stack manages the AI, whether famstack's oMLX's
    settings file still holds the placeholder (`omlx.managed_key`), and
    whether `stack_key`, `[ai] openai_key`, is the key that file holds.
    A file with no key yet matches: its next start writes the key into
    both. Never a key itself: the answer travels to a page and to doctor."""
    held = managed_key(data_dir) if managed else ""
    placeholder = held == PLACEHOLDER_KEY
    matches = not held or held == stack_key
    if placeholder:
        from stack.prompt import warn, out
        warn("Key: local. Every device at home can use the AI with it.")
        out("  './stack ai key new' makes a new key.")
    return {"managed": managed, "placeholder": placeholder, "matches": matches}


# ── new ──────────────────────────────────────────────────────────────────

def _new(config: dict, ai: dict, *, wait: bool) -> dict:
    """Make a new key, restart oMLX when it runs, and apply the key to the
    running stacklets that use it. Returns the key and those stacklets.
    While the oMLX app runs, a restart would load famstack's model beside
    the app's: it returns the error `omlx_app.QUIT_FIRST` and changes
    nothing. So does a running stacklet that uses the key and whose start
    `stack up` would refuse (`stack.snapshot.guard`), such as one on
    another release after `stack update`: it would keep the old key while
    oMLX takes only the new one."""
    from stack.ai.probe import probe

    if refused := _refused_consumers(config):
        return refused
    answered = probe(ai.get("openai_url", ""), ai.get("openai_key", ""))
    omlx_runs = answered.reachable or answered.needs_auth
    if omlx_runs and omlx_app.running():
        return {"error": omlx_app.QUIT_FIRST}
    key = give_new_key(config["data_dir"], lambda k: config["set_cfg"]("ai", "openai_key", k))
    if omlx_runs and (failed := _restart_omlx()):
        return {"error": f"The new key is stored, but oMLX did not restart: {failed}. "
                         "Run './stack restart ai'."}
    if omlx_runs:
        _ask(config, wait=wait)
    applied = _apply(config, wait=wait)
    _report(key, applied)
    return {"key": key, "applied": applied}


def _refused_consumers(config: dict) -> dict | None:
    """The error that names the restart of each running stacklet that uses
    the key and that `stack up` refuses, or None when every one may start."""
    from stack.snapshot import guard
    from stack.stack import Stack

    stck = Stack(Path(config["repo_root"]), Path(config["data_dir"]),
                 instance_dir=Path(config["instance_dir"]))
    running = sorted(ai_consumers(Path(config["repo_root"])) & running_stacklets())
    refused = guard.refusals(stck, running)
    if not refused:
        return None
    return {"error": f"The key stays as it is, because {', '.join(refused)} cannot start "
                     f"again with a new one now. Run first: stack restart {' '.join(refused)}. "
                     f"Then run './stack ai key new' again.",
            "refusals": list(refused.values())}


def _restart_omlx() -> str:
    """Restart famstack's oMLX so it reads its new key; "" when it did,
    else why not."""
    for command in restart_commands():
        try:
            done = subprocess.run(command, capture_output=True, text=True,
                                  timeout=RESTART_OMLX_SECS)
        except (OSError, subprocess.TimeoutExpired) as e:
            return str(e)
        if done.returncode != 0:
            return done.stderr.strip() or f"exit {done.returncode}"
    return ""


def _ask(config: dict, *, wait: bool) -> None:
    """Ask the restarted oMLX one short question with its new key, once it
    lists its models again, and keep the answer for the setup page
    (`answer.py`), as `stack ai switch` does. Waiting, here; without,
    `stack ai check --when-ready` on its own, so the answer with the key
    is not held up."""
    if wait:
        from ..answer import check_when_ready
        import tomllib
        toml = Path(config["instance_dir"]) / "stack.toml"
        check_when_ready(config["instance_dir"], tomllib.loads(toml.read_text()).get("ai", {}))
        return
    command = [str(Path(config["repo_root"]) / "stack"), "ai", "check", "--when-ready"]
    env = {**os.environ, "STACK_DIR": config["instance_dir"]}
    subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def _apply(config: dict, *, wait: bool) -> list[str]:
    """Start again the running stacklets whose containers carry the key,
    so their env is rendered with it. Returns their ids.

    Core is left out when another one starts: each start refreshes core
    once its rendered env changed, and a second recreate would restart
    every bot again. One `stack up` with all the ids, so core is
    refreshed once.
    """
    running = sorted(ai_consumers(Path(config["repo_root"])) & running_stacklets())
    ids = [sid for sid in running if sid != "core"] or running
    if not ids:
        return []
    command = [str(Path(config["repo_root"]) / "stack"), "up", *ids]
    env = {**os.environ, "STACK_DIR": config["instance_dir"]}
    if wait:
        subprocess.run(command, env=env, stdout=sys.stderr)
    else:
        # Its own session and no pipes: it outlives this command and the
        # host API's request, and its runs are followed from their logs.
        subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    return running


def _report(key: str, applied: list[str]) -> None:
    from stack.prompt import nl, done, dim
    nl()
    done(f"The AI's new key: {key}. Apps on other computers that use this AI need it too.")
    if applied:
        dim(f"   Applied to: {', '.join(applied)}")
    nl()
