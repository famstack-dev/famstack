"""The host API reads and sets the family for the tools server.

`{"cmd": "family"}` answers with the family as `stack family --json` shows
it; `{"cmd": "family", "action": "set", "family": {...}}` writes one, as
`stack family set` does, and answers with the result or with the problems.
The body reaches the CLI on stdin, never on its command line, where any
process on the Mac could read it and where its size is limited.

Driven through the API's own connection handler (`handle_client`), one end
of a socket pair standing in for the container's connection. The API runs
`./stack` as a separate process; `STACK_BIN` points at a wrapper that notes
what it was called with and what came on stdin, then runs the real CLI of
this checkout on an instance in `tmp_path` (`STACK_DIR`).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import socket
import sys
import textwrap
import threading
import tomllib

import pytest

from tests.host_api_tokens import CORE_TOKEN, give_tokens

from .conftest import LIB, STACKLETS

WRAPPER = textwrap.dedent('''\
    #!{python}
    """./stack as the API calls it: notes argv and stdin, then runs the real CLI."""
    import json, os, subprocess, sys
    from pathlib import Path

    here = Path(__file__).parent
    body = sys.stdin.read()
    (here / "called").write_text(json.dumps({{"argv": sys.argv[1:], "stdin": body}}))
    env = {{**os.environ, "PYTHONPATH": {lib!r}, "STACK_DIR": {instance!r},
           "HOME": {home!r}, "PYTHONDONTWRITEBYTECODE": "1"}}
    run = subprocess.run([sys.executable, "-m", "stack", *sys.argv[1:]], input=body,
                         capture_output=True, text=True, env=env)
    sys.stdout.write(run.stdout)
    sys.stderr.write(run.stderr)
    sys.exit(run.returncode)
''')

SIMPSONS = {"name": "Simpson", "domain": "simpsons.com", "people": [
    {"name": "Homer Simpson", "words": "he"},
    {"name": "Marge Simpson", "words": "she"},
]}


@pytest.fixture
def instance(tmp_path):
    root = tmp_path / "instance"
    (root / ".stack").mkdir(parents=True)
    (root / "stack.toml").write_text(
        f'[core]\nname = "famstack"\nstack_owner = ""\ndata_dir = "{tmp_path / "data"}"\n'
        '\n[messages]\nserver_name = ""\n')
    return root


@pytest.fixture
def api(tmp_path, instance, monkeypatch):
    """The host API module, loaded from its file, with `./stack` pointed at the wrapper."""
    path = STACKLETS / "core" / "famstack-api.py"
    loader = importlib.machinery.SourceFileLoader("famstack_api", str(path))
    spec = importlib.util.spec_from_file_location("famstack_api", path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    wrapper = tmp_path / "stack"
    wrapper.write_text(WRAPPER.format(python=sys.executable, lib=str(LIB), instance=str(instance),
                                      home=str(tmp_path / "home")))
    wrapper.chmod(0o755)
    monkeypatch.setattr(module, "STACK_BIN", wrapper)
    # The API reads its callers' tokens from the instance it serves.
    give_tokens(instance)
    monkeypatch.setenv("STACK_DIR", str(instance))
    return module


def ask(api, request: dict) -> dict:
    """Send one request the way core's tools server does, with core's
    token, and read the one-line reply."""
    ours, theirs = socket.socketpair()
    threading.Thread(target=api.handle_client, args=(theirs,), daemon=True).start()
    ours.sendall((json.dumps({**request, "token": CORE_TOKEN}) + "\n").encode())
    with ours.makefile("r", encoding="utf-8") as reply:
        line = reply.readline()
    ours.close()
    return json.loads(line)


def called(tmp_path) -> dict | None:
    path = tmp_path / "called"
    return json.loads(path.read_text()) if path.exists() else None


def test_setting_the_family_writes_it_and_answers_with_it(api, instance, tmp_path):
    reply = ask(api, {"cmd": "family", "action": "set", "family": SIMPSONS})

    assert reply["ok"] is True, reply
    assert [p["id"] for p in reply["people"]] == ["homer", "marge"]
    with open(instance / "users.toml", "rb") as f:
        assert [u["email"] for u in tomllib.load(f)["users"]] == ["homer@simpsons.com", "marge@simpsons.com"]


def test_the_family_goes_to_the_cli_on_stdin_not_its_command_line(api, tmp_path):
    ask(api, {"cmd": "family", "action": "set", "family": SIMPSONS})

    call = called(tmp_path)
    assert call["argv"] == ["family", "set", "--from", "-", "--json"]
    assert json.loads(call["stdin"]) == SIMPSONS


def test_a_refused_family_answers_with_every_problem(api, instance):
    family = {**SIMPSONS, "people": [{"name": "Homer Simpson", "relation": "uncle"}]}

    reply = ask(api, {"cmd": "family", "action": "set", "family": family})

    assert "error" in reply
    assert [p["field"] for p in reply["problems"]] == ["people.0.relation"]
    assert not (instance / "users.toml").exists()


def test_reading_the_family_answers_as_stack_family_does(api):
    ask(api, {"cmd": "family", "action": "set", "family": SIMPSONS})

    reply = ask(api, {"cmd": "family"})

    assert reply["name"] == "Simpson"
    assert reply["domain"] == "simpsons.com"
    assert reply["people"][1]["words"] == "she"


def test_an_action_it_does_not_have_runs_nothing(api, tmp_path):
    reply = ask(api, {"cmd": "family", "action": "delete"})

    assert "set" in reply["error"]
    assert called(tmp_path) is None


def test_discover_lists_the_family(api):
    assert "family" in ask(api, {"cmd": "discover"})["commands"]


def test_who_is_in_the_chat_is_read_through_the_messages_command(api, tmp_path):
    """The dashboard's "Set up the chat app" reads `stack messages users`,
    read-only, through the JSON surface; here the chat does not run, and
    the answer says so."""
    reply = ask(api, {"cmd": "chat_users"})

    assert called(tmp_path)["argv"] == ["messages", "users", "--json"]
    assert "error" in reply


# ── The family's passwords, for the pages that may show them ────────────────

def test_passwords_answers_for_the_family_and_for_no_one_else(api, instance):
    """Given Homer and Marge in the family with their passwords stored,
    when the tools server asks for Homer, Marge, the tech admin and a
    name that is nobody, then the answer holds Homer's and Marge's
    stored passwords only: the command reads a person's password and no
    other secret, and changes nothing."""
    ask(api, {"cmd": "family", "action": "set", "family": SIMPSONS})
    secrets = instance / ".stack" / "secrets.toml"
    secrets.write_text(secrets.read_text().replace(
        _stored(instance, "homer"), "otter-maple-cloud-lamp"))
    before = secrets.read_bytes()

    answer = ask(api, {"cmd": "passwords", "people": ["homer", "marge", "stackadmin", "admin"]})

    assert set(answer["passwords"]) == {"homer", "marge"}
    assert answer["passwords"]["homer"] == "otter-maple-cloud-lamp"
    assert answer["passwords"]["marge"] == _stored(instance, "marge")
    assert secrets.read_bytes() == before


def test_passwords_without_core_s_token_answers_nothing(api, instance):
    """Given a request for Homer's password without core's token, then
    the answer is a refusal and holds no password."""
    ask(api, {"cmd": "family", "action": "set", "family": SIMPSONS})
    ours, theirs = socket.socketpair()
    threading.Thread(target=api.handle_client, args=(theirs,), daemon=True).start()
    ours.sendall((json.dumps({"cmd": "passwords", "people": ["homer"]}) + "\n").encode())
    with ours.makefile("r", encoding="utf-8") as reply:
        answer = json.loads(reply.readline())
    ours.close()

    assert "error" in answer and "passwords" not in answer
    assert _stored(instance, "homer") not in json.dumps(answer)


def _stored(instance, person: str) -> str:
    with open(instance / ".stack" / "secrets.toml", "rb") as f:
        return tomllib.load(f)[f"global__USER_{person.upper()}_PASSWORD"]
