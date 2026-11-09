"""stack core firewall: whether the Mac's firewall keeps phones away from the chat.

    stack core firewall             one line: off, lets the chat in, or what blocks it and the fix
    stack core firewall --json      {"on": true, "blocks": "block-all"}

A phone on the home network reaches the chat through OrbStack, which
publishes the containers' ports on this Mac. macOS's application firewall
can stop those connections before they reach OrbStack, and nothing in the
chat app says why. This command reads the firewall's state, so the setup
page (`/setup`, step 4) can warn with the exact fix. It reads only; it
never changes the firewall.

    stack core firewall --json
      {"on": true, "blocks": "orbstack"}

`blocks` names what keeps phones out, "" when nothing does:

    block-all   "Block all incoming connections" is on
    orbstack    OrbStack's incoming connections are set to blocked
    signed      downloaded signed apps are not allowed on their own, and
                OrbStack is one

Why it works this way:

- **`socketfilterfw`, without admin rights.** macOS's own tool
  (`/usr/libexec/ApplicationFirewall/socketfilterfw`) answers its `--get`
  questions to any user, so the host API (`famstack-api.py`), which runs
  as the admin's user, can ask it. It runs on the Mac, never in a
  container: a container sees its own network, not the Mac's firewall.
- **Found on `PATH` first, then where macOS keeps it.** The tool's folder
  is not on any `PATH`; looking there first lets a test put its stand-in
  in front.
- **Read from its sentences.** The tool answers in English sentences, not
  data (`Firewall is enabled. (State = 1)`). Each answer is matched on the
  words that decide it; an answer that matches none is unreadable, and
  the command says it cannot tell instead of guessing.
- **An unreadable state is an error, not a warning.** The setup page shows
  a warning only for an answer that names a block, so a Mac whose tool
  answers differently loses the warning, never the chat.

It does not check a guest Wi-Fi, a VPN or the phone itself; the setup page
names those as likely causes while no phone reached the Mac.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

HELP = "Whether the Mac's firewall keeps phones away from the chat"

__all__ = ["HELP", "BLOCK_ALL", "ORBSTACK", "SIGNED", "run", "read", "blocks"]

# What keeps phones out, as `blocks` names it.
BLOCK_ALL = "block-all"
ORBSTACK = "orbstack"
SIGNED = "signed"

# Where OrbStack is installed; the firewall keeps its rules by the app's path.
ORBSTACK_APP = "/Applications/OrbStack.app"

# The fix for each block, as System Settings names it (macOS 15).
FIXES = {
    BLOCK_ALL: "System Settings › Network › Firewall › Options: turn off "
               "“Block all incoming connections”",
    ORBSTACK: "System Settings › Network › Firewall › Options: allow OrbStack",
    SIGNED: "System Settings › Network › Firewall › Options: allow OrbStack",
}


def run(args, stacklet, config):
    """The firewall's state as a dict: `on` and `blocks`, or `error` when it
    cannot be read. Prints one line for people.

        stack core firewall
          The firewall of this Mac keeps phones out.
          Fix: System Settings › Network › Firewall › Options: allow OrbStack
    """
    state = read()
    print(_sentence(state))
    return state


def read() -> dict:
    """Ask `socketfilterfw` for the firewall's state; never raises.

    Returns `{"on": bool, "blocks": str}`, or `{"error": str}` when the
    tool is missing, fails or answers in words this does not know.

        read()   # {"on": False, "blocks": ""} on a Mac with the firewall off
    """
    tool = shutil.which("socketfilterfw", path=os.pathsep.join(
        [os.environ.get("PATH", ""), TOOL_DIR]))
    if tool is None:
        return {"error": "macOS's firewall tool (socketfilterfw) is not on this Mac"}
    answers = {}
    for question in QUESTIONS:
        answer = _ask(tool, question)
        if answer is None:
            return {"error": f"socketfilterfw {' '.join(question)} did not answer"}
        answers[question[0]] = answer
    try:
        return blocks(answers)
    except ValueError as unknown:
        return {"error": str(unknown)}


def blocks(answers: dict[str, str]) -> dict:
    """The state the tool's answers describe, keyed by the question asked.

    Raises ValueError for an answer it cannot read. Block all comes first,
    because allowing OrbStack does not help while it is on.

        blocks({"--getglobalstate": "Firewall is enabled. (State = 1)",
                "--getblockall": "Firewall has block all state set to enabled.",
                "--getallowsigned": "... downloaded signed software ENABLED.",
                "--getappblocked": "Incoming connection to ... is permitted."})
        # {"on": True, "blocks": "block-all"}
    """
    if not _enabled(answers["--getglobalstate"]):
        return {"on": False, "blocks": ""}
    if _block_all(answers["--getblockall"]):
        found = BLOCK_ALL
    elif _app_blocked(answers["--getappblocked"]):
        found = ORBSTACK
    elif not _signed_allowed(answers["--getallowsigned"]):
        found = SIGNED
    else:
        found = ""
    return {"on": True, "blocks": found}


# ── Asking the tool ──────────────────────────────────────────────────────

# Where macOS keeps the tool; on no PATH.
TOOL_DIR = "/usr/libexec/ApplicationFirewall"

# The questions, each answered without admin rights.
QUESTIONS = (("--getglobalstate",), ("--getblockall",), ("--getallowsigned",),
             ("--getappblocked", ORBSTACK_APP))

# The tool answers at once; a hang means something is wrong with it.
ASK_TIMEOUT_SECS = 5


def _ask(tool: str, question: tuple[str, ...]) -> str | None:
    """The tool's answer to one question, or None when it failed."""
    try:
        done = subprocess.run([tool, *question], capture_output=True, text=True,
                              timeout=ASK_TIMEOUT_SECS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


# ── Reading the answers ──────────────────────────────────────────────────
# Matched on the words that decide, case aside: "disabled" does not
# contain "enabled", so each pair is told apart by one word.

_STATE = re.compile(r"State = (\d)")


def _enabled(answer: str) -> bool:
    """`Firewall is enabled. (State = 1)`; State 2 is the older macOS
    spelling of block all, which is on as well."""
    found = _STATE.search(answer)
    if found is None:
        raise ValueError(f"the firewall's state is unreadable: {answer.strip()!r}")
    return found[1] != "0"


def _block_all(answer: str) -> bool:
    """`Firewall has block all state set to enabled.`, or before macOS 15
    `Block all ENABLED!`."""
    return _on_or_off(answer, "block all")


def _app_blocked(answer: str) -> bool:
    """`Incoming connection to /Applications/OrbStack.app is blocked.`"""
    words = answer.lower()
    if "is blocked" in words:
        return True
    if "is permitted" in words:
        return False
    raise ValueError(f"OrbStack's firewall rule is unreadable: {answer.strip()!r}")


def _signed_allowed(answer: str) -> bool:
    """`Automatically allow downloaded signed software ENABLED.`: OrbStack
    is downloaded, so the built-in line does not decide."""
    line = next((line for line in answer.splitlines() if "downloaded signed" in line.lower()),
                answer)
    return _on_or_off(line, "signed software")


def _on_or_off(answer: str, what: str) -> bool:
    words = answer.lower()
    if "disabled" in words:
        return False
    if "enabled" in words:
        return True
    raise ValueError(f"the firewall's {what} setting is unreadable: {answer.strip()!r}")


def _sentence(state: dict) -> str:
    """The state in one or two lines for people."""
    if "error" in state:
        return f"Cannot tell whether the firewall keeps phones out: {state['error']}."
    if not state["on"]:
        return "The firewall of this Mac is off."
    if not state["blocks"]:
        return "The firewall of this Mac is on and lets the chat in."
    return f"The firewall of this Mac keeps phones out.\nFix: {FIXES[state['blocks']]}"
