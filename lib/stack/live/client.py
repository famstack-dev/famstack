"""A client of the host API: one reply, or a run followed while it happens.

The host API (`stacklets/core/famstack-api.py`) takes one JSON request per
connection. `call` sends one and returns the one-line reply. `stream`
sends one with `"stream": true` and yields the run's events as the API
relays them. It ends with exactly one `result`, also when the API is not
running, refuses, hangs up early or says nothing for `silence_secs`, so a
reader never waits for one that cannot come.

The tools server uses it for its status, logs and jobs; the stacker bot's
own copy of `call` (`stacklets/core/bot/stacker.py`) is the next to move.

    call({"cmd": "status"}, timeout=30)
    for event in stream({"cmd": "up", "stacklet": "photos"}, silence_secs=1320):
        ...

The API's address is `STACK_API_HOST` and `STACK_API_PORT`, as core's
containers have them, read on every call.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Iterator

from .envelope import RESULT, Event

# Where the API is from inside a container, when the environment says nothing.
DEFAULT_HOST = "host.docker.internal"
DEFAULT_PORT = 42001


def address() -> tuple[str, int]:
    """The host API's address, from the environment."""
    return (os.environ.get("STACK_API_HOST", DEFAULT_HOST),
            int(os.environ.get("STACK_API_PORT", DEFAULT_PORT)))


def call(request: dict, *, timeout: float) -> dict:
    """The API's reply to `request`, or `{"error": ...}` saying why there is none."""
    try:
        with socket.create_connection(address(), timeout=timeout) as sock:
            sock.sendall(_line(request))
            reply = sock.makefile("rb").readline()
    except OSError as e:
        return {"error": _unreachable(e, timeout)}
    if not reply:
        return {"error": "the host API closed the connection without a reply"}
    try:
        answer = json.loads(reply)
    except json.JSONDecodeError:
        return {"error": "the host API sent a reply that is not JSON"}
    return answer if isinstance(answer, dict) else {"error": "the host API sent a reply that is not an object"}


def stream(request: dict, *, silence_secs: float) -> Iterator[Event]:
    """The run `request` starts, event by event, a `result` last."""
    last_id = 0
    try:
        for line in _lines(request, silence_secs):
            event = Event.from_line(line) or Event.now(last_id + 1, RESULT, _refusal(line))
            yield event
            if event.type == RESULT:
                return
            last_id = event.id if isinstance(event.id, int) else last_id
        error = "the host API hung up before the run's result"
    except OSError as e:
        error = _unreachable(e, silence_secs)
    yield Event.now(last_id + 1, RESULT, {"error": error})


def _lines(request: dict, silence_secs: float) -> Iterator[str]:
    """The API's reply to a streamed `request`, line by line as it arrives."""
    with socket.create_connection(address(), timeout=silence_secs) as sock:
        sock.sendall(_line({**request, "stream": True}))
        yield from sock.makefile("r", encoding="utf-8", errors="replace")


def _line(request: dict) -> bytes:
    return (json.dumps(request) + "\n").encode()


def _refusal(line: str) -> dict:
    """A reply line that is not an event: the API refused the request."""
    try:
        reply = json.loads(line)
    except json.JSONDecodeError:
        reply = None
    if isinstance(reply, dict) and reply.get("error"):
        return {"error": str(reply["error"])}
    return {"error": "the host API sent a line that is not an event"}


def _unreachable(e: OSError, waited: float) -> str:
    """Why the API could not be asked, in words."""
    if isinstance(e, ConnectionRefusedError):
        return "the host API is not running"
    if isinstance(e, TimeoutError):
        return f"the host API said nothing for {waited} s"
    return f"cannot reach the host API: {e}"
