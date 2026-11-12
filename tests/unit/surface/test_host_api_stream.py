"""The host API relays a `stack up` while it runs, to a caller that asks for it.

A caller sends `{"cmd": "up", "stacklet": "<id>", "stream": true}` and gets
each event line as the run writes it to its run log on the Mac, a `run`
event first, then the connection closes after the final `result`
(ADR-020). Without `"stream"` the reply is what it always was:
one line, then close. The tools server and the stacker bot read exactly
that, up to the first newline.

Driven through the API's own connection handler (`handle_client`) behind a
loopback listener, as a container reaches it. The API runs `./stack` as a
separate process, so in-process patches would not reach it: `STACK_BIN`,
the module's path to `./stack`, is set on the copy this test loads to a
stand-in script. The stand-in answers the way the CLI does (a run with its
lock and its run log with `--events`, the human summary with `--json`) and
waits inside its run until the test releases it, which is how a test can
tell a line that arrived during the run from one sent after it.

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

from stack.live import follow
from tests.host_api_tokens import CORE_TOKEN, TOKEN_NAME, give_tokens

from .conftest import LIB, STACKLETS

STAND_IN = textwrap.dedent('''\
    #!{python}
    """./stack, as far as the host API sees it: what it was called with,
    then a run as the CLI makes one, with its lock and its run log: one
    step, a wait until the test writes `release`, then the end the
    release names. The instance is STACK_DIR, this file's folder."""
    import json, os, sys, time
    from pathlib import Path

    here = Path(__file__).parent
    args = sys.argv[1:]
    (here / "argv").write_text(json.dumps(args))
    (here / "pid").write_text(str(os.getpid()))
    if "--events" not in args:
        print("App is running")
        sys.exit(0)

    from stack import run_log, runs
    from stack.output import SilentOutput

    output = run_log.RunLogOutput(SilentOutput())
    with runs.hold(here, [args[1]]):
        output.step("Rendering environment")
        while not (here / "release").exists():
            time.sleep(0.01)
        if (here / "release").read_text() != "result":
            print("Traceback: the CLI fell over", file=sys.stderr, flush=True)
            os._exit(3)
        run_log.record_result(here, args[1], {{"ok": True, "stacklet": args[1]}})
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
    monkeypatch.setenv("STACK_DIR", str(tmp_path))
    # Core's token, in the secret store the API reads and in the
    # environment the client sends it from, as in core's containers.
    give_tokens(tmp_path)
    monkeypatch.setenv(TOKEN_NAME, CORE_TOKEN)
    monkeypatch.setenv("PYTHONPATH", str(LIB))
    monkeypatch.setattr(follow, "POLL_SECS", 0.02)
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
        self.sock.sendall((json.dumps({**request, "token": CORE_TOKEN}) + "\n").encode())
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
    call = Caller(port, {"cmd": "up", "stacklet": "app", "stream": True, "surface": "dashboard"})

    run, first = call.next_line(), call.next_line()
    still_running = not (tmp_path / "release").exists()
    release(tmp_path, "result")
    rest = call.rest()

    assert run["type"] == "run"
    assert run["data"]["surface"] == "dashboard"
    assert first["type"] == "step.done"
    assert first["data"] == {"text": "Rendering environment"}
    assert still_running
    assert [e["type"] for e in rest] == ["result"]
    assert rest[0]["data"] == {"ok": True, "stacklet": "app"}
    assert stand_in_was_called_with(tmp_path) == ["up", "app", "--events"]


def test_a_run_that_falls_silent_is_ended_with_an_error(api, port, tmp_path, monkeypatch):
    """A run that keeps reporting may take as long as it needs; one that
    says nothing for the silence limit is stopped, and the caller is told.

    The limit is 2 s, which this test waits out: the time to the first
    line counts as silence too, and the stand-in is a Python process
    whose start alone took over 0.3 s under the unit lane's parallel
    workers."""
    monkeypatch.setattr(api, "STREAM_SILENCE_SECS", 2)

    call = Caller(port, {"cmd": "up", "stacklet": "app", "stream": True})
    events = [call.next_line(), *call.rest()]

    assert [e["type"] for e in events] == ["run", "step.done", "result"]
    assert events[2]["id"] == 3
    assert "stopped hearing" in events[2]["data"]["error"]
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "pid").read_text()), 0)


def test_a_run_that_ends_without_a_result_still_ends_with_one(port, tmp_path):
    """The caller can rely on the last line being a result, even when the
    CLI fell over before it wrote its own."""
    call = Caller(port, {"cmd": "up", "stacklet": "app", "stream": True})
    call.next_line(), call.next_line()
    release(tmp_path, "crash")
    rest = call.rest()

    assert [e["type"] for e in rest] == ["result"]
    assert rest[0]["id"] == 3
    assert "exit 3" in rest[0]["data"]["error"]
    assert "the CLI fell over" in rest[0]["data"]["output"]


def test_follow_goes_on_from_the_last_event_a_caller_saw(port, tmp_path):
    """A caller that went away asks again with the last id it saw, and
    gets the rest of the run, nothing twice."""
    started = Caller(port, {"cmd": "up", "stacklet": "app", "stream": True})
    started.next_line(), started.next_line()

    again = Caller(port, {"cmd": "follow", "stacklet": "app", "after": 2, "stream": True})
    release(tmp_path, "result")
    rest = again.rest()
    started.rest()

    assert [(e["id"], e["type"]) for e in rest] == [(3, "result")]
    assert rest[0]["data"]["ok"] is True


def test_follow_of_a_stacklet_that_never_ran_ends_at_once(port):
    reply = Caller(port, {"cmd": "follow", "stacklet": "app", "stream": True}).rest()

    assert [e["type"] for e in reply] == ["result"]
    assert "has not run" in reply[0]["data"]["error"]


def test_only_runs_stream(port, tmp_path):
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
    run, first = next(events), next(events)
    still_running = not (tmp_path / "release").exists()
    release(tmp_path, "result")
    rest = list(events)

    assert run.type == "run"
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
