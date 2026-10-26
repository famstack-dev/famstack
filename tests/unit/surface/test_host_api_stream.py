"""The host API relays a `stack up` while it runs, to a caller that asks for it.

A caller sends `{"cmd": "up", "stacklet": "<id>", "stream": true}` and gets
each event line as the CLI writes it, then the connection closes after the
final `result` (ADR-020). Without `"stream"` the reply is what it always was:
one line, then close. The tools server and the stacker bot read exactly
that, up to the first newline.

Driven through the API's own connection handler (`handle_client`) behind a
loopback listener, as a container reaches it. The API runs `./stack` as a
separate process, so in-process patches would not reach it: `STACK_BIN`,
the module's path to `./stack`, is set on the copy this test loads to a
stand-in script. The stand-in answers the way the CLI does (event lines
with `--events`, the human summary with `--json`) and waits between its
lines until the test releases it, which is how a test can tell a line that
arrived during the run from one sent after it.

The last section asks the same API through `stack.live.client`, the
library its callers in containers use.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import socket
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from .conftest import STACKLETS

STAND_IN = textwrap.dedent('''\
    #!{python}
    """./stack, as far as the host API sees it: what it was called with,
    one event, a wait until the test writes `release`, then the end the
    release names."""
    import json, os, sys, time
    from pathlib import Path

    here = Path(__file__).parent
    args = sys.argv[1:]
    (here / "argv").write_text(json.dumps(args))
    (here / "pid").write_text(str(os.getpid()))
    if "--events" not in args:
        print("App is running")
        sys.exit(0)

    def event(n, type, data):
        line = {{"id": n, "type": type, "time": "2026-10-26T09:00:00.000Z", "data": data}}
        print(json.dumps(line), flush=True)

    event(1, "step.done", {{"text": "Rendering environment"}})
    while not (here / "release").exists():
        time.sleep(0.01)
    if (here / "release").read_text() == "result":
        event(2, "result", {{"ok": True, "stacklet": args[1]}})
    else:
        print("Traceback: the CLI fell over", file=sys.stderr)
        sys.exit(3)
''')


@pytest.fixture
def api(tmp_path, monkeypatch):
    """The host API module, loaded from its file (its name has a hyphen),
    with `./stack` pointed at the stand-in."""
    path = STACKLETS / "core" / "famstack-api.py"
    loader = importlib.machinery.SourceFileLoader("famstack_api", str(path))
    spec = importlib.util.spec_from_file_location("famstack_api", path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    stand_in = tmp_path / "stack"
    stand_in.write_text(STAND_IN.format(python=sys.executable))
    stand_in.chmod(0o755)
    monkeypatch.setattr(module, "STACK_BIN", stand_in)
    return module


@pytest.fixture
def port(api):
    """A loopback listener handing each connection to the API's handler,
    one thread per connection as its `main` does."""
    listener = socket.create_server(("127.0.0.1", 0))

    def serve():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=api.handle_client, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    yield listener.getsockname()[1]
    listener.close()


class Caller:
    """A client of the API: sends one request, reads the reply line by line."""

    def __init__(self, port: int, request: dict):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.sendall((json.dumps(request) + "\n").encode())
        self.reply = self.sock.makefile("r", encoding="utf-8")

    def next_line(self) -> dict:
        line = self.reply.readline()
        assert line, "the API closed the connection"
        return json.loads(line)

    def rest(self) -> list[dict]:
        """Every line until the API closes the connection."""
        lines = [json.loads(line) for line in self.reply]
        self.sock.close()
        return lines


def release(tmp_path: Path, how: str) -> None:
    (tmp_path / "release").write_text(how)


def stand_in_was_called_with(tmp_path: Path) -> list[str] | None:
    argv = tmp_path / "argv"
    return json.loads(argv.read_text()) if argv.exists() else None


# ── Streamed ─────────────────────────────────────────────────────────────

def test_a_caller_sees_the_first_step_before_the_run_has_finished(port, tmp_path):
    call = Caller(port, {"cmd": "up", "stacklet": "app", "stream": True})

    first = call.next_line()
    still_running = not (tmp_path / "release").exists()
    release(tmp_path, "result")
    rest = call.rest()

    assert first["type"] == "step.done"
    assert first["data"] == {"text": "Rendering environment"}
    assert still_running
    assert [e["type"] for e in rest] == ["result"]
    assert rest[0]["data"] == {"ok": True, "stacklet": "app"}
    assert stand_in_was_called_with(tmp_path) == ["up", "app", "--events"]


def test_a_run_that_falls_silent_is_ended_with_an_error(api, port, tmp_path, monkeypatch):
    """A run that keeps reporting may take as long as it needs; one that
    says nothing for the silence limit is stopped, and the caller is told."""
    monkeypatch.setattr(api, "STREAM_SILENCE_SECS", 0.3)

    call = Caller(port, {"cmd": "up", "stacklet": "app", "stream": True})
    events = [call.next_line(), *call.rest()]

    assert [e["type"] for e in events] == ["step.done", "result"]
    assert events[1]["id"] == 2
    assert "stopped hearing" in events[1]["data"]["error"]
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "pid").read_text()), 0)


def test_a_run_that_ends_without_a_result_still_ends_with_one(port, tmp_path):
    """The caller can rely on the last line being a result, even when the
    CLI fell over before it wrote its own."""
    call = Caller(port, {"cmd": "up", "stacklet": "app", "stream": True})
    call.next_line()
    release(tmp_path, "crash")
    rest = call.rest()

    assert [e["type"] for e in rest] == ["result"]
    assert rest[0]["id"] == 2
    assert "exit 3" in rest[0]["data"]["error"]
    assert "the CLI fell over" in rest[0]["data"]["output"]


def test_only_up_streams(port, tmp_path):
    call = Caller(port, {"cmd": "status", "stream": True})

    reply = call.rest()

    assert len(reply) == 1
    assert "status" in reply[0]["error"]
    assert stand_in_was_called_with(tmp_path) is None


# ── Not streamed ─────────────────────────────────────────────────────────

def test_without_stream_the_reply_is_one_line_as_before(port, tmp_path):
    call = Caller(port, {"cmd": "up", "stacklet": "app"})

    reply = call.rest()

    assert reply == [{"ok": True, "output": "App is running", "error": ""}]
    assert stand_in_was_called_with(tmp_path) == ["up", "app", "--json"]


# ── The client library, `stack.live.client` ──────────────────────────────
# What the tools server, and later the stacker bot, use to talk to the API:
# the same requests over the same loopback listener, from the caller's side.

@pytest.fixture
def at_the_api(port, monkeypatch):
    """The client pointed at the listener, as core's env points it at the host."""
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(port))


def a_closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_client_follows_a_run_while_it_happens(at_the_api, tmp_path):
    from stack.live import client

    events = client.stream({"cmd": "up", "stacklet": "app"}, silence_secs=5)
    first = next(events)
    still_running = not (tmp_path / "release").exists()
    release(tmp_path, "result")
    rest = list(events)

    assert (first.type, first.data) == ("step.done", {"text": "Rendering environment"})
    assert still_running
    assert [(e.type, e.data) for e in rest] == [("result", {"ok": True, "stacklet": "app"})]


def test_the_client_turns_a_refusal_into_the_runs_result(at_the_api):
    from stack.live import client

    events = list(client.stream({"cmd": "status"}, silence_secs=5))

    assert [e.type for e in events] == ["result"]
    assert "does not stream" in events[0].data["error"]


def test_the_client_ends_with_a_result_when_the_api_is_not_running(monkeypatch):
    """A caller following a run learns it is over, even when there was none."""
    from stack.live import client

    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(a_closed_port()))

    events = list(client.stream({"cmd": "up", "stacklet": "app"}, silence_secs=5))

    assert [(e.id, e.type) for e in events] == [(1, "result")]
    assert events[0].data == {"error": "the host API is not running"}
    assert client.call({"cmd": "status"}, timeout=5) == {"error": "the host API is not running"}


def test_the_client_gets_todays_one_line_reply(at_the_api, tmp_path):
    from stack.live import client

    assert client.call({"cmd": "up", "stacklet": "app"}, timeout=5) == {
        "ok": True, "output": "App is running", "error": ""}
    assert stand_in_was_called_with(tmp_path) == ["up", "app", "--json"]
