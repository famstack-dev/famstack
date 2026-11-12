"""Pairing a phone from the terminal: `stack devices pair <person>`.

    stack devices pair homer            # the QR code; waits for Homer's phone
    stack devices pair homer --json     # the address and its end; waits for nothing

    pair(root, config, "homer")         # 0 once the phone opened it, else 1

    Add Homer's phone
    Open the camera on Homer's phone and point it at this code.

    █████████████████████████████████████
    ████ ▄▄▄▄▄ █▀▀▄▄  ▄ ▀██ ▄█ ▄▄▄▄▄ ████
    ...
    http://simpson.local:42000/go/app?pair=q0JbZ3...
    The code works once, for 10 minutes.
    Waiting for the phone…
    Homer's iPhone is added.

A phone joins the family's pages by opening a one-time pairing code that
the Mac makes (128 random bits, 10 minutes, once). The Mac's page "Add a
phone" shows it as a QR code, but a Mac in a cupboard has no screen: its
admin reaches only its terminal, over ssh. This module prints the same
code there, so the first phone of such a Mac needs no screen sharing.

Why it asks core. The codes live in core's tools server, in its memory
only, where the phone opens them, and the QR encoder lives there too. So
the command is a client (ADR-019): it asks the tools server on the Mac's
address (`127.0.0.1:42002`, `[core] cards_port`) for a new code, with the
QR code as text, prints it, and asks for the code's state every second
until the phone opened it or it expired. Ctrl-C ends the code on the
server, so a code nobody waits for any more pairs nothing. The host side
stays stdlib only, as every `./stack` command does.

The Mac's address is published on 127.0.0.1 only, and its routes open
only with the admin key (`stack.devices.admin_key`), which the command
reads from the instance and sends as the admin's browser sends its
cookie. So only this Mac (or an ssh session on it) can ask for a code:
whoever is at the Mac vouches for the phone, as on the Mac's page.

What it does not do: write the list of devices (the package `stack.devices` is its
one writer, reached when the phone opens the code), or allow a browser
that waits to join. The design: ADR-023 D21.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Mapping

from ..http_json import Answer, JsonClient, Unreachable
from . import admin_key

__all__ = ["MAC_PORT", "ASK_EVERY_SECS", "pair", "new_code", "show"]

# The Mac's address, where core's tools server answers this Mac alone
# (core's compose file publishes it on 127.0.0.1 only).
MAC_PORT = 42002
# How often the command asks whether the phone opened the code.
ASK_EVERY_SECS = 1.0
# The route that makes a code, on the Mac's address only (`mac_only`).
_PAIRING = "/dashboard/api/devices/pairing"
# A request to the tools server answers at once; longer means it hangs.
_ASK_SECS = 10

Out = Callable[[str], None]


def pair(instance_dir: Path, config: Mapping, person: str, *, heading: str = "", wait: bool = True,
         out: Out = print) -> int:
    """Print a new pairing code for `person` (a user id) as a QR code with
    its address, and wait until a phone opened it. Returns the exit code.

    `instance_dir` is the instance, whose admin key the request carries;
    `config` its stack.toml as a dict (`[core] cards_port`).
    `heading` replaces the two lines that say what to do (the installer's
    "Pair your phone: scan this code with its camera"). With `wait`
    False it returns 0 after the code, which still works for 10 minutes.

    0: the phone opened it ("Homer's iPhone is added."). 1: the code
    expired, or Ctrl-C ended it ("This code has expired. Run stack devices
    pair homer again."); `person` is not in the family; core does not
    answer. A refusal prints one line and makes no code.

        pair(root, {"core": {}}, "homer")  # 0, after Homer's phone opened it
        pair(root, {"core": {}}, "ned")    # 1, "ned is not in the family. ..."
    """
    answer = new_code(instance_dir, config, person)
    if "error" in answer:
        out(answer["error"])
        return 1
    for line in show(answer, heading=heading):
        out(line)
    if not wait:
        return 0
    out("Waiting for the phone…")
    tools = _tools_of(instance_dir, config)
    try:
        state = _wait_for_the_phone(tools, answer["state_url"])
    except KeyboardInterrupt:
        state = _end(tools, answer["state_url"])
    if state.get("state") == "added":
        out(state.get("line") or f"{answer['name']}'s {state.get('device', 'phone')} is added.")
        return 0
    out(f"This code has expired. Run stack devices pair {person} again.")
    return 1


def new_code(instance_dir: Path, config: Mapping, person: str) -> dict:
    """Ask core's tools server on the Mac's address of the instance at
    `instance_dir`, whose stack.toml is `config`, for a new pairing code for `person`: its
    answer (`person`, `name`, `link`, `state_url`, `expires`, `qr`), or
    `{"error": <one line that says what to do>}` when it made none.

        new_code(root, config, "homer")
        # {"person": "homer", "name": "Homer",
        #  "link": "http://simpson.local:42000/go/app?pair=q0JbZ3...",
        #  "state_url": "/dashboard/api/devices/pairing/q0JbZ3...",
        #  "expires": "2026-11-11T13:10:00+00:00", "qr": "█████...\\n"}
        new_code(root, config, "ned")
        # {"error": "ned is not in the family. See who is with stack family."}
    """
    try:
        answer = _tools_of(instance_dir, config).post(_PAIRING, {"person": person})
    except Unreachable:
        return {"error": "Core does not answer on this Mac: start it with stack up core."}
    body = answer.body if isinstance(answer.body, dict) else {}
    if answer.status == 404:
        return {"error": f"{body.get('error') or person + ' is not in the family.'} "
                         "See who is with stack family."}
    if answer.status != 200 or "link" not in body:
        return {"error": body.get("error") or f"Core answered {answer.status}: try stack up core."}
    return body


def show(answer: dict, *, heading: str = "") -> list[str]:
    """The lines that show the code of `answer` (from `new_code`): what to
    do, the QR code, its address and how long it works.

        show({"name": "Homer", "qr": "██\\n", "link": "http://..."})
        # ["Add Homer's phone",
        #  "Open the camera on Homer's phone and point it at this code.",
        #  "", "██", "http://...", "The code works once, for 10 minutes."]
    """
    name = answer["name"]
    said = [heading] if heading else [f"Add {name}'s phone",
                                      f"Open the camera on {name}'s phone and point it at this code."]
    return [*said, "", *answer["qr"].rstrip("\n").splitlines(), answer["link"],
            "The code works once, for 10 minutes."]


# ── Waiting for the phone ────────────────────────────────────────────────

def _wait_for_the_phone(tools: JsonClient, state_url: str) -> dict:
    """Ask for the code's state until it is no longer `waiting`: the
    last state (`added` with the device, or `expired`). A tools server
    that stops answering counts as expired: a restart of core ends every
    code."""
    while True:
        state = _state(tools, state_url)
        if state.get("state") != "waiting":
            return state
        time.sleep(ASK_EVERY_SECS)


def _state(tools: JsonClient, state_url: str) -> dict:
    try:
        answer = tools.get(state_url)
    except Unreachable:
        return {"state": "expired"}
    return answer.body if answer.status == 200 and isinstance(answer.body, dict) else {"state": "expired"}


def _end(tools: JsonClient, state_url: str) -> dict:
    """End the code (Ctrl-C), so it pairs nothing any more; its state
    after that. A phone that opened it in the same moment keeps its
    pairing, and the state says `added`."""
    try:
        answer = tools.request("DELETE", state_url)
    except Unreachable:
        return {"state": "expired"}
    return answer.body if isinstance(answer.body, dict) else {"state": "expired"}


# ── The Mac's address ────────────────────────────────────────────────────

def _tools_of(instance_dir: Path, config: Mapping) -> JsonClient:
    """Core's tools server on the Mac's address of this instance, asked
    as the admin's browser asks it: with the admin key's cookie."""
    port = ((config or {}).get("core") or {}).get("cards_port") or MAC_PORT
    return _AsTheAdmin(f"http://127.0.0.1:{port}",
                       admin_key.cookie_header(instance_dir), timeout=_ASK_SECS)


class _AsTheAdmin(JsonClient):
    """A JSON client that sends the admin key's cookie with every request."""

    def __init__(self, base_url: str, cookie: dict[str, str], *, timeout: float):
        super().__init__(base_url, timeout=timeout)
        self.cookie = cookie

    def request(self, method: str, path: str, body: object = None, *,
                headers: dict[str, str] | None = None) -> Answer:
        return super().request(method, path, body, headers={**self.cookie, **(headers or {})})
