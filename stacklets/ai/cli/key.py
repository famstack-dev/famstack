"""stack ai key: the key of the oMLX the stack manages.

    stack ai key                  # lists the verbs
    stack ai key show             # whether the key is still the placeholder `local`
    stack ai key new              # a new key in oMLX and the stack, applied, and waits
    stack ai key new --no-wait    # the same, and the stacklets restart on their own

oMLX takes a request only with its key. Before keys were generated the
stack installed it with `local`, which every device at home can send
(`omlx.py`). `show` says whether this instance still has it; the AI's
service card on the Mac asks it through the host API and offers "Make a
new key" while it does. `new` makes a key, writes it into oMLX's settings
and `[ai] openai_key`, restarts oMLX when it runs (it reads its key only
when it starts), and then starts every running stacklet that renders the
key into its containers again, so they send the new one. Starting the
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

from ..omlx import PLACEHOLDER_KEY, give_new_key, managed_key
from .switch import ai_consumers, running_stacklets

VERBS = {"show": "Whether the AI's key is still the placeholder 'local'",
         "new": "Make a new key for the AI, and apply it [--no-wait]"}

NOT_MANAGED = ("The stack did not install this AI server, so its key is the one its admin "
               "set. Change it there, then run './stack ai switch <url> --key <key>'.")

# oMLX's restart, and how long it may take: Homebrew stops and starts
# its LaunchAgent.
RESTART_OMLX = ["brew", "services", "restart", "omlx"]
RESTART_OMLX_SECS = 60


def run(args, stacklet, config):
    """The verb's answer as a dict; the verbs without one.

        run(["show"], stacklet, config)   # {"managed": True, "placeholder": True}
        run(["new"], stacklet, config)    # {"key": "7fk2-pq9w-m3xd-h8ra",
                                          #  "applied": ["agent", "memory"]}
    """
    if not args or args[0] not in VERBS:
        return {"verbs": VERBS} if not args else {
            "error": f"Unknown verb '{args[0]}'. Use: {', '.join(VERBS)}"}
    ai = config.get("stack", {}).get("ai", {})
    managed = ai.get("provider") == "managed"
    if args[0] == "show":
        return _show(managed)
    if not managed:
        return {"error": NOT_MANAGED}
    if args[1:] not in ([], ["--no-wait"]):
        return {"error": "Usage: stack ai key new [--no-wait]"}
    return _new(config, ai, wait=args[1:] != ["--no-wait"])


# ── show ─────────────────────────────────────────────────────────────────

def _show(managed: bool) -> dict:
    """Whether the stack manages the AI, and whether its key is still the
    placeholder. Never the key itself: the answer travels to a page."""
    placeholder = managed and managed_key() == PLACEHOLDER_KEY
    if placeholder:
        from stack.prompt import warn, out
        warn("Key: local. Every device at home can use the AI with it.")
        out("  './stack ai key new' makes a new key.")
    return {"managed": managed, "placeholder": placeholder}


# ── new ──────────────────────────────────────────────────────────────────

def _new(config: dict, ai: dict, *, wait: bool) -> dict:
    """Make a new key, restart oMLX when it runs, and apply the key to the
    running stacklets that use it. Returns the key and those stacklets."""
    from stack.ai.probe import probe

    answered = probe(ai.get("openai_url", ""), ai.get("openai_key", ""))
    omlx_runs = answered.reachable or answered.needs_auth
    key = give_new_key(lambda k: config["set_cfg"]("ai", "openai_key", k))
    if omlx_runs and (failed := _restart_omlx()):
        return {"error": f"The new key is stored, but oMLX did not restart: {failed}. "
                         "Run 'brew services restart omlx'."}
    applied = _apply(config, wait=wait)
    _report(key, applied)
    return {"key": key, "applied": applied}


def _restart_omlx() -> str:
    """Restart oMLX so it reads its new key; "" when it did, else why not."""
    try:
        done = subprocess.run(RESTART_OMLX, capture_output=True, text=True,
                              timeout=RESTART_OMLX_SECS)
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    return "" if done.returncode == 0 else (done.stderr.strip() or f"exit {done.returncode}")


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
