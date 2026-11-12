"""The host API answers only callers that send their own token.

Every container that reaches the Mac reaches the host API on
`127.0.0.1:42001`. So the API asks each request for its caller's token:
core's (the tools server and the stacker bot) opens the JSON surface,
which starts and stops stacklets; the agent's opens the plaintext
surface with its allowlist. A request with no token, an unknown one or
the other surface's gets a refusal, and no `./stack` runs.

Driven through the API's own connection handler (`handle_client`), one end
of a socket pair standing in for the container's connection. `STACK_BIN`
points at a stand-in `./stack` that notes each call and answers as the
CLI would. The tokens are in the instance's secret store (`STACK_DIR`), as
`stack up` writes them.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading

import pytest

from tests.host_api_tokens import AGENT_TOKEN, CORE_TOKEN, give_tokens

from .conftest import STACKLETS

STAND_IN = textwrap.dedent('''\
    #!{python}
    """./stack as the API calls it: notes its arguments, answers as the CLI."""
    import json, sys
    from pathlib import Path

    (Path(__file__).parent / "called").write_text(json.dumps(sys.argv[1:]))
    if "--json" in sys.argv:
        print(json.dumps({{"stacklets": [{{"id": "photos", "state": "running"}}]}}))
    else:
        print("Tent for four, packed by Marge")
''')

REFUSED_PLAINTEXT = 126


@pytest.fixture
def instance(tmp_path, monkeypatch):
    root = tmp_path / "instance"
    (root / ".stack").mkdir(parents=True)
    (root / "stack.toml").write_text(
        f'[core]\nname = "famstack"\nstack_owner = ""\ndata_dir = "{tmp_path / "data"}"\n')
    give_tokens(root)
    monkeypatch.setenv("STACK_DIR", str(root))
    return root


@pytest.fixture
def api(tmp_path, instance, monkeypatch):
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


def send(api, line: str) -> str:
    """Send one line the way a container does; the whole reply.

    The API reads up to the line's newline, so the request needs no
    half-close: a refusal can close the connection before one would come.
    """
    ours, theirs = socket.socketpair()
    threading.Thread(target=api.handle_client, args=(theirs,), daemon=True).start()
    ours.sendall(line.encode())
    with ours.makefile("r", encoding="utf-8") as reply:
        text = reply.read()
    ours.close()
    return text


def ask(api, request: dict) -> dict:
    return json.loads(send(api, json.dumps(request) + "\n"))


def exit_code(reply: str) -> int:
    """The status the reply's last line names; 0 when it names none, as on success."""
    marker, _, code = reply.rstrip("\n").rpartition("stack-exit:")
    return int(code) if marker or reply.startswith("stack-exit:") else 0


def ran_stack(tmp_path) -> bool:
    return (tmp_path / "called").exists()


# ── The JSON surface: core's token ─────────────────────────────────────────

def test_a_request_with_cores_token_is_answered(api, tmp_path):
    """Given core's tools server, when it asks for the list with its token,
    then the API answers as before: the token is the only change."""
    reply = ask(api, {"cmd": "list", "token": CORE_TOKEN})

    assert reply["stacklets"][0]["id"] == "photos"
    assert json.loads((tmp_path / "called").read_text()) == ["list", "--json"]


@pytest.mark.parametrize("request_", [
    {"cmd": "list"},
    {"cmd": "list", "token": ""},
    {"cmd": "list", "token": "core-token-0f3a9"},
    {"cmd": "list", "token": AGENT_TOKEN},
    {"cmd": "up", "stacklet": "photos", "token": AGENT_TOKEN},
    {"cmd": "up", "stacklet": "photos", "stream": True},
    {"cmd": "list", "token": ["core-token-0f3a9c"]},
], ids=["no token", "empty", "wrong", "the agent's", "the agent's on up", "a stream without", "not text"])
def test_a_json_request_without_cores_token_is_refused_and_runs_nothing(api, tmp_path, request_):
    """Given another container (ChatAI, Caddy, the agent), when it sends a
    JSON request without core's token, then it gets an error and no
    `./stack` runs."""
    reply = ask(api, request_)

    assert set(reply) == {"error"}
    assert not ran_stack(tmp_path)


def test_discover_needs_the_token_too(api):
    """Given a caller without a token, when it asks what the API offers,
    then it learns nothing: discover is a command like the others."""
    reply = ask(api, {"cmd": "discover"})

    assert set(reply) == {"error"}


def test_no_token_is_valid_before_its_stacklet_made_one(api, instance, tmp_path):
    """Given an instance where core has no token yet, when a caller sends an
    empty one, then it is refused: a missing secret matches nothing."""
    (instance / ".stack" / "secrets.toml").write_text("")

    reply = ask(api, {"cmd": "list", "token": ""})

    assert set(reply) == {"error"}
    assert not ran_stack(tmp_path)


# ── The plaintext surface: the agent's token ───────────────────────────────

def test_a_plaintext_command_with_the_agents_token_is_answered(api, tmp_path):
    """Given the agent, when it sends a command of its allowlist after its
    token, then the API runs exactly that command and answers in text."""
    reply = send(api, f"token {AGENT_TOKEN} memory search tent\n")

    assert exit_code(reply) == 0
    assert json.loads((tmp_path / "called").read_text()) == ["memory", "search", "tent"]


@pytest.mark.parametrize("line", [
    "memory search tent\n",
    "token memory search tent\n",
    "token agent-token-7b21e memory search tent\n",
    f"token {CORE_TOKEN} memory search tent\n",
], ids=["no token", "token without value", "wrong", "core's"])
def test_a_plaintext_command_without_the_agents_token_is_refused(api, tmp_path, line):
    """Given another container, when it sends a plaintext command without
    the agent's token, then it gets a refusal and no `./stack` runs."""
    reply = send(api, line)

    assert exit_code(reply) == REFUSED_PLAINTEXT
    assert not ran_stack(tmp_path)


def test_the_agents_token_keeps_its_allowlist(api, tmp_path):
    """Given the agent with its token, when it asks for a lifecycle command,
    then the allowlist still refuses it: the token opens the surface, not
    every command."""
    reply = send(api, f"token {AGENT_TOKEN} up memory\n")

    assert exit_code(reply) == REFUSED_PLAINTEXT
    assert not ran_stack(tmp_path)


@pytest.fixture
def listening(api):
    """The API on a loopback port, one thread per connection as its `main`
    does; the address as the agent's container names it."""
    listener = socket.create_server(("127.0.0.1", 0))

    def serve():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=api.handle_client, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    yield f"127.0.0.1:{listener.getsockname()[1]}"
    listener.close()


def agent_runs(address: str, *argv: str, token: str | None) -> subprocess.CompletedProcess:
    """`stack <argv>` in the agent's container: its shim as a process, with
    the environment the exec tool gives it."""
    env = {"PATH": os.environ["PATH"], "STACK_API_ADDR": address}
    if token is not None:
        env["STACK_API_TOKEN"] = token
    return subprocess.run([sys.executable, str(STACKLETS / "agent" / "client" / "stack"), *argv],
                          capture_output=True, text=True, env=env, timeout=30)


def test_the_agents_shim_sends_its_token_and_is_answered(listening, tmp_path):
    """Given the agent's container with its token, when its shell runs
    `stack memory search`, then the answer comes back as before."""
    run = agent_runs(listening, "memory", "search", "school run", token=AGENT_TOKEN)

    assert run.returncode == 0, run.stderr
    assert json.loads((tmp_path / "called").read_text()) == ["memory", "search", "school run"]


def test_the_agents_shim_without_a_token_is_refused(listening, tmp_path):
    """Given a container without the agent's token, when it runs the same
    shim, then the API refuses it and nothing runs."""
    run = agent_runs(listening, "memory", "search", "tent", token=None)

    assert run.returncode == REFUSED_PLAINTEXT
    assert not ran_stack(tmp_path)


# ── Refusals in the log ────────────────────────────────────────────────────

def test_a_refusal_is_logged_without_the_token(api, capsys):
    """Given a request with a wrong token, then the API's log names the
    refusal and never the token, since the log is a file anyone at the
    Mac reads."""
    ask(api, {"cmd": "list", "token": "a-guess-at-the-token"})
    send(api, "token another-guess memory search tent\n")

    log = capsys.readouterr().err
    assert len(log.strip().splitlines()) == 2
    assert "a-guess-at-the-token" not in log and "another-guess" not in log


def test_a_reply_never_carries_a_token(api):
    """Given a refused request, then its reply names no token, its own or
    the one that would have opened the surface."""
    replies = json.dumps(ask(api, {"cmd": "list", "token": AGENT_TOKEN}))
    replies += send(api, f"token {CORE_TOKEN} memory search tent\n")

    assert CORE_TOKEN not in replies and AGENT_TOKEN not in replies
