"""ai_route: the installer's question how the stack's AI runs.

`./stack` on a new Mac asks it once, after the dependency checks and
before core starts. There are three routes, and Enter takes the first:

    How should famstack's AI run?
      1) famstack installs oMLX (recommended) (default)
      2) Your own AI server (address and key)
      3) No AI for now
      › Number [1]

    from stack import ai_route

    if ai_route.needs_asking(stck):
        ai_route.ask(stck)
    ...                                    # core starts
    if ai_route.waits_to_start(stck):
        problem = ai_route.start_in_the_background(stck)

Each route writes `[ai]` with the command the setup page uses for it, so
the terminal and the page agree on what `stack.toml` holds:

    route 1   `stack ai switch managed --no-start`   provider "managed", famstack's oMLX key
    route 2   `stack ai switch <address> --key <k>`  provider "external", checked first
    route 3   `[ai] provider = "none"`               (`stack.stack.AI_OFF`)

Route 1 writes `[ai]` before core starts, so core's first `.env` already
names the AI's address. The long install (Homebrew's oMLX, the models)
starts after core is up, as a job of the host API: the same `up ai` run
the AI's tile on the setup page starts (`stack.live.client.stream`, the
host API's `_Start`). The run goes on when the installer hangs up, and
the page follows it from its run log. The installer reads only until the
run began, so the terminal returns at once. A later `./stack` starts it
again while famstack's oMLX was never set up and no run of it goes on:
an install stopped or the job did not start, and the route stays.

Route 2 asks the address and the key, and `stack ai switch` checks them
as it does in the terminal. The key goes to the switch as one argument,
`--key=<key>`, so a key that starts with "-" stays the key; "-" alone is
refused, because the switch reads the key from stdin for it. A server that lists several models offers them as a
numbered choice (Enter takes the first). Text that is no address (it reads as an option, or is the
word `managed`) is refused before the switch runs. A server that does not answer asks again, or
goes on without an AI server; the setup page then asks later.

Nothing is asked when `[ai]` already names a route (an update keeps its
route) or when there is no terminal: the setup page asks instead.

The words are German for a stack whose language is German, else English.
"""

from __future__ import annotations

import getpass
import re
import sys

from .prompt import ORANGE, RESET, ask as ask_text, choose, confirm, dim, nl, out, warn
from .stack import AI_OFF

__all__ = ["EXTERNAL", "MANAGED", "NONE", "ask", "needs_asking", "start_in_the_background",
           "waits_to_start", "words"]

# The routes, as `[ai] provider` names them.
MANAGED, EXTERNAL, NONE = "managed", "external", AI_OFF

# The address route 2 proposes: the oMLX app's own, the most common
# server of one's own on a Mac (the ai stacklet's setup form proposes it too).
SERVER_ADDRESS = "http://localhost:8000/v1"

# An address the switch can take: http(s)://, or a host name or address
# with an optional port and path. It starts with a letter, a digit or "["
# (IPv6), so it never reads as an option, and has no spaces. `managed` is
# the word the switch reserves for famstack's oMLX, route 1.
ADDRESS = re.compile(r"(https?://)?[A-Za-z0-9\[][^\s]*")
RESERVED = "managed"

# The key value with which `stack ai switch` reads the key from stdin.
STDIN_KEY = "-"

# How long the installer waits for the AI's run to begin. The host API
# waits up to 60 s for the run to take its lock (`BEGIN_SECS`), so this is
# longer than that, never shorter.
BEGIN_SECS = 90

# Where the run says it was started (`stack.runs`): "from the installer".
SURFACE = "installer"


# ── Asking ──────────────────────────────────────────────────────────────

def needs_asking(stck) -> bool:
    """Whether the installer asks the route: `[ai]` names none yet and
    someone sits at a terminal to answer.

        needs_asking(stck)   # True on a new Mac; False after an update, or without a terminal
    """
    return not stck._cfg("ai", "provider") and sys.stdin.isatty()


def ask(stck) -> str | None:
    """Ask the route and write it into `[ai]`. Returns the route written
    (`MANAGED`, `EXTERNAL`, `NONE`), or None when nothing was written: the
    input ended, or route 2 went on without a server. The setup page then
    asks for one.

        ask(stck)   # Enter → "managed": [ai] names famstack's oMLX, nothing started

    Route 3 shows what waits for the AI and asks to go on; no returns to
    the question. Writes `stack.toml` and, for route 1, famstack's oMLX
    key into its settings file (`omlx.own_key`, through the switch).
    """
    w = words(stck)
    while True:
        picked = choose(w["question"], [w["managed"], w["external"], w["none"]], default=0)
        if picked is None:
            return None
        if picked == 0:
            return _write_managed(stck)
        if picked == 1:
            return _ask_the_server(stck, w)
        if _without_ai_agreed(w):
            stck._set_cfg("ai", "provider", NONE)
            return NONE


def start_in_the_background(stck) -> str | None:
    """Start the AI's `up ai` run as a job of the host API, and return once
    it began. None when it runs, else why it did not start.

        start_in_the_background(stck)   # None: the setup page shows its steps

    The run is the one the AI's tile starts: it prepares oMLX and the
    models holding only the AI's run lock, so the rest of the stack starts
    meanwhile, and it goes on after the installer hung up. A run of the AI
    already going on counts as started. Needs core up: the host API and
    core's token (`STACK_API_TOKEN`) come with it.
    """
    from .live import client
    from .live.envelope import RESULT

    token = stck.secrets.get("core", client.TOKEN_VARIABLE)
    request = {"cmd": "up", "stacklet": "ai", "surface": SURFACE, "token": token}
    events = client.stream(request, silence_secs=BEGIN_SECS, host=client.ON_THIS_MAC)
    # Only the first event is read: the connection closes when this
    # returns, and the host API follows the run to its end without us.
    first = next(events)
    if first.type != RESULT:
        return None     # the run began; the host API follows it to its end
    return first.data.get("error")


def waits_to_start(stck) -> bool:
    """Whether famstack's oMLX is the route and its run should start now:
    the AI was never set up (no setup marker) and no run of it goes on.

        waits_to_start(stck)   # True right after route 1, and on the next
                               # `./stack` when that run never started
    """
    from . import runs
    return (stck._cfg("ai", "provider") == MANAGED and stck._is_first_run("ai")
            and not runs.is_held(stck.instance_dir, "ai"))


def words(stck) -> dict[str, str]:
    """The installer's words for the AI, in the stack's language.

        words(stck)["question"]   # "Wie soll famstacks KI laufen?" for a German stack
    """
    return WORDS["de"] if stck._cfg("core", "language") == "de" else WORDS["en"]


# ── The routes ──────────────────────────────────────────────────────────

def _write_managed(stck) -> str | None:
    """Route 1: `[ai]` for famstack's oMLX, as the setup page writes it.
    Starts nothing; a refusal (an unreadable oMLX settings file) is said,
    and the page asks later."""
    result = _switch(stck, ["managed", "--no-start"])
    if result.get("error"):
        warn(result["error"])
        return None
    return MANAGED


def _ask_the_server(stck, w: dict) -> str | None:
    """Route 2: the address and the key, checked and written by `stack ai
    switch`, which prints what it found. A failed check asks again, or
    goes on without a server (None)."""
    while True:
        address = ask_text(w["address"], default=SERVER_ADDRESS)
        if address is None:
            return None
        if not _is_an_address(address):
            warn(w["no_address"].format(address=address))
            continue
        key = _ask_the_key(w)
        if key is None:
            return None
        result = _check_the_server(stck, address, key)
        if result.get("reason") == "model" and result.get("models"):
            model = _choose_a_model(result["models"], w)
            if model is None:
                return None
            result = _check_the_server(stck, address, key, model)
        if not result.get("error"):
            return EXTERNAL
        warn(result["error"])
        if not confirm(w["retry"]):
            dim(w["later"])
            return None


def _check_the_server(stck, address: str, key: str, model: str = "") -> dict:
    """`stack ai switch <address>` with `key` and, when given, `model`:
    its answer. A server that lists several models answers with the
    reason `model` and the list (`models`), and writes nothing."""
    # One argument each, `--key=<key>`: argparse then takes a value that
    # starts with "-" as the value, never as an option. The switch runs in
    # this process, so the key is on no command line another process sees.
    args = [address, f"--key={key}"]
    if model:
        args.append(f"--model={model}")
    return _switch(stck, args)


def _ask_the_key(w: dict) -> str | None:
    """The server's key, typed without echo; "" for a server without one.
    None when the input ended. "-" alone is refused and asked again,
    because the switch reads `--key=-` as "the key comes on stdin"."""
    while True:
        try:
            key = getpass.getpass(f"  {ORANGE}›{RESET} {w['key']} ")
        except EOFError:
            return None
        if key != STDIN_KEY:
            return key
        warn(w["dash_key"])


def _choose_a_model(models: list[str], w: dict) -> str | None:
    """The model the admin picks of the ones the server lists; Enter takes
    the first. None when the input ended.

    The switch already takes the model of this Mac's memory profile when
    the server lists it (`[ai] default`, which the installer wrote), so
    this choice comes up only for a server without it."""
    picked = choose(w["model"], models, default=0)
    return None if picked is None else models[picked]


def _is_an_address(address: str) -> bool:
    """Whether `address` can be a server's address for the switch.

        _is_an_address("http://192.168.1.20:8000/v1")   # True
        _is_an_address("localhost:8000")                # True
        _is_an_address("-x")                            # False: reads as an option
        _is_an_address("managed")                       # False: route 1's word
    """
    return address != RESERVED and ADDRESS.fullmatch(address) is not None


def _switch(stck, args: list[str]) -> dict:
    """`stack ai switch <args>`, run in this process: its answer, or an
    error. Its argument parser exits on what it cannot parse; that is an
    error here, never the end of the install."""
    try:
        return stck.run_cli_command("ai", "switch", args) or {}
    except SystemExit:
        return {"error": "stack ai switch did not take this address."}


def _without_ai_agreed(w: dict) -> bool:
    """Route 3: what works without the AI and what waits for it, then
    whether to go on that way."""
    nl()
    out(w["none_warning"])
    return confirm(w["go_on"])


# ── Words ───────────────────────────────────────────────────────────────

WORDS = {
    "en": {
        "question": "How should famstack's AI run?",
        "managed": "famstack installs oMLX (recommended)",
        "external": "Your own AI server (address and key)",
        "none": "No AI for now",
        "none_warning": "famstack connects your services to a local AI. Documents and photos work "
                        "without it. Tags, the wiki and the diary come with the AI.",
        "go_on": "Continue?",
        "address": "Address of the AI server",
        "key": "Key (leave empty if the server needs none):",
        "retry": "Try again?",
        "dash_key": "A key cannot be just \"-\". Type the key, or leave it empty.",
        "model": "The server lists several models. Which one should famstack use?",
        "no_address": "{address} is not the address of an AI server, "
                      "for example http://192.168.1.20:8000/v1.",
        "later": "The install goes on without an AI. You can set it up in the browser later.",
        "background": "The AI is set up in the background (20 to 30 minutes). "
                      "The browser shows how far it is.",
        "not_started": "The AI did not start in the background: {error}. "
                       "You can start it in the browser.",
    },
    "de": {
        "question": "Wie soll famstacks KI laufen?",
        "managed": "famstack installiert oMLX (empfohlen)",
        "external": "Eigener KI-Server (Adresse und Schlüssel)",
        "none": "Erst einmal ohne KI",
        "none_warning": "famstack verbindet deine Dienste mit einer lokalen KI. Dokumente und "
                        "Fotos funktionieren auch ohne KI. Schlagwörter, Wiki und Tagebuch "
                        "kommen erst mit ihr.",
        "go_on": "Weiter?",
        "address": "Adresse des KI-Servers",
        "key": "Schlüssel (leer lassen, wenn der Server keinen braucht):",
        "retry": "Noch einmal versuchen?",
        "dash_key": "Ein Schlüssel kann nicht nur \"-\" sein. Gib den Schlüssel ein oder lass ihn leer.",
        "model": "Der Server bietet mehrere Modelle an. Welches soll famstack nutzen?",
        "no_address": "{address} ist keine Adresse eines KI-Servers. "
                      "Ein Beispiel: http://192.168.1.20:8000/v1",
        "later": "Die Installation geht ohne KI weiter. Im Browser kannst du sie später einrichten.",
        "background": "Die KI wird im Hintergrund eingerichtet (20 bis 30 Minuten). "
                      "Im Browser siehst du, wie weit sie ist.",
        "not_started": "Die KI ließ sich nicht im Hintergrund starten: {error}. "
                       "Du kannst sie im Browser starten.",
    },
}
