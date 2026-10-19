"""Where `stack messages send` delivers a message, and how.

Three decisions, in this order:

  1. The room: an alias or a room id as given.
  2. The sender: the system account, or the one `--as` names.
  3. The path: an unencrypted room gets plain HTTP from the host. An
     encrypted room gets the message from the sender's command-line
     device in the bot runner, which encrypts it; the request reaches
     that device over stdin, so the password is in no process's arguments.

The command runs as `./stack` runs it, against a stand-in Synapse
(pytest-httpserver) and a stand-in `docker` on PATH, the two things
outside the host CLI it talks to. The encrypted send itself, inside the
container, is checked on the rig.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest
from werkzeug.wrappers import Response

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLI = _REPO_ROOT / "stacklets" / "messages" / "cli"
sys.path.insert(0, str(_CLI))

_spec = importlib.util.spec_from_file_location("messages_send", _CLI / "send.py")
send_command = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(send_command)

SERVER = "simpson"
SECRETS = {
    "core__STACKER_BOT_PASSWORD": "stacker-pass",
    "agent__AGENT_BOT_PASSWORD": "agent-pass",
    "global__USER_HOMER_PASSWORD": "homer-pass",
}

DOCKER = """#!{python}
import json, os, sys
stdin = sys.stdin.read() if sys.argv[1] == "exec" else ""
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "stdin": stdin}}) + "\\n")
if sys.argv[1] == "inspect":
    print(os.environ["FAKE_CORE_RUNNING"])
else:
    print(json.dumps({{"event_id": "$sent-encrypted"}}))
"""


def mxid(name: str) -> str:
    return f"@{name}:{SERVER}"


class Synapse:
    """The client endpoints a send touches, over rooms the test lays out."""

    def __init__(self, httpserver):
        self.rooms: dict[str, dict] = {}
        self.aliases: dict[str, str] = {}
        self.logins: list[str] = []
        self.sent: list[tuple[str, dict]] = []
        httpserver.expect_request("/_matrix/client/v3/login", method="POST") \
            .respond_with_handler(self._login)
        for method in ("GET", "POST", "PUT"):
            httpserver.expect_request(
                re.compile("/_matrix/client/v3/.*"), method=method,
            ).respond_with_handler(self._route)
        self.port = httpserver.port

    def room(self, room_id, *members, alias=None, encrypted=False):
        self.rooms[room_id] = {"members": {mxid(m) for m in members}, "encrypted": encrypted}
        if alias:
            self.aliases[f"#{alias}:{SERVER}"] = room_id

    # ── handlers ──

    def _login(self, request):
        user = request.get_json()["user"]
        self.logins.append(user)
        return self._json({"access_token": f"token-{user}", "device_id": "HTTPDEVICE",
                           "user_id": mxid(user)})

    def _route(self, request):
        path = request.path.removeprefix("/_matrix/client/v3")
        parts = path.strip("/").split("/")
        if parts[0] == "join":
            return self._json({})
        if parts[0] == "directory":
            room_id = self.aliases.get(parts[2])
            return self._json({"room_id": room_id}) if room_id else self._json({}, 404)
        room = self.rooms[parts[1]]
        if parts[2] == "state":
            if room["encrypted"]:
                return self._json({"algorithm": "m.megolm.v1.aes-sha2"})
            return self._json({"errcode": "M_NOT_FOUND"}, 404)
        if parts[2] == "send":
            self.sent.append((parts[1], request.get_json()))
            return self._json({"event_id": "$sent-plain"})
        return self._json({"errcode": "M_UNRECOGNIZED"}, 400)

    @staticmethod
    def _json(body, status=200):
        return Response(json.dumps(body), status=status, content_type="application/json")


class Docker:
    """A `docker` on PATH that records each call and answers as the bot runner would."""

    def __init__(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        script = bin_dir / "docker"
        script.write_text(DOCKER.format(python=sys.executable))
        script.chmod(0o755)
        self._log = tmp_path / "docker.log"
        self._monkeypatch = monkeypatch
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        monkeypatch.setenv("FAKE_DOCKER_LOG", str(self._log))
        self.core_running(True)

    def core_running(self, running: bool):
        self._monkeypatch.setenv("FAKE_CORE_RUNNING", "true" if running else "false")

    @property
    def execs(self) -> list[dict]:
        if not self._log.exists():
            return []
        calls = [json.loads(line) for line in self._log.read_text().splitlines()]
        return [c for c in calls if c["argv"][0] == "exec"]


@pytest.fixture
def synapse(httpserver):
    return Synapse(httpserver)


@pytest.fixture
def docker(tmp_path, monkeypatch):
    return Docker(tmp_path, monkeypatch)


@pytest.fixture
def send(synapse, docker, tmp_path, monkeypatch):
    """Run `stack messages send <argv...>` as the CLI does; its result dict."""
    config = {
        "is_healthy": lambda: True,
        "instance_dir": str(tmp_path),
        "stack": {"messages": {"server_name": SERVER}},
        "secrets": SECRETS,
        "manifest": {"ports": {"synapse": synapse.port}},
    }

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["stack", "messages", "send", *argv])
        return send_command.run([], {}, config)
    return run


# ── an unencrypted room: plain HTTP, as before ─────────────────────────────

def test_an_unencrypted_room_gets_the_message_over_plain_http(send, synapse, docker):
    synapse.room("!chat:simpson", "stacker-bot", "homer", "marge", alias="chat")

    result = send("chat", "Backup done")

    assert result == {"ok": True, "room": "chat", "room_id": "!chat:simpson",
                      "event_id": "$sent-plain"}
    assert synapse.logins == ["stacker-bot"]
    assert synapse.sent == [("!chat:simpson", {"msgtype": "m.text", "body": "Backup done"})]
    assert docker.execs == []


def test_a_room_id_is_used_as_given(send, synapse):
    synapse.room("!chat:simpson", "stacker-bot", "homer")

    assert send("!chat:simpson", "Backup done")["event_id"] == "$sent-plain"


def test_an_unknown_alias_sends_nothing(send, synapse):
    result = send("nowhere", "Backup done")

    assert result == {"error": "Room 'nowhere' not found"}
    assert synapse.sent == []


# ── an encrypted room: the sender's command-line device ───────────────────

def test_an_encrypted_room_gets_the_message_from_the_bot_runner(send, synapse, docker):
    synapse.room("!kitchen:simpson", "stacker-bot", "homer", "marge",
                 alias="kitchen", encrypted=True)

    result = send("kitchen", "Dentist tomorrow 08:00", "--mention", "homer")

    assert result["event_id"] == "$sent-encrypted"
    assert synapse.sent == []
    [call] = docker.execs
    assert call["argv"][-3:] == ["python", "-m", "stack.matrix.send"]
    request = json.loads(call["stdin"])
    assert request["room_id"] == "!kitchen:simpson"
    assert request["user_id"] == mxid("stacker-bot")
    assert request["content"] == {"msgtype": "m.text", "body": "Dentist tomorrow 08:00",
                                  "m.mentions": {"user_ids": [mxid("homer")]}}


def test_the_password_reaches_the_device_in_no_process_arguments(send, synapse, docker):
    synapse.room("!kitchen:simpson", "stacker-bot", "homer", alias="kitchen", encrypted=True)

    send("kitchen", "Backup done")

    [call] = docker.execs
    assert "stacker-pass" not in " ".join(call["argv"])
    assert json.loads(call["stdin"])["password"] == "stacker-pass"


def test_a_bots_device_keeps_its_state_next_to_the_bots_own(send, synapse, docker):
    """Next to the bot's device, so it finds the account's cross-signing keys."""
    synapse.room("!kitchen:simpson", "stacky-bot", "homer", alias="kitchen", encrypted=True)
    synapse.room("!kitchen2:simpson", "homer", "marge", alias="kitchen2", encrypted=True)

    send("kitchen", "Hello", "--as", "agent")
    send("kitchen2", "Hello", "--as", "homer")

    agent, person = (json.loads(c["stdin"]) for c in docker.execs)
    assert agent["user_id"] == mxid("stacky-bot") and agent["state_dir"] == "/data/agent/bot"
    assert person["user_id"] == mxid("homer") and person["state_dir"] == "/data/messages/cli"


def test_with_core_down_an_encrypted_send_says_to_start_core(send, synapse, docker):
    synapse.room("!kitchen:simpson", "stacker-bot", "homer", alias="kitchen", encrypted=True)
    docker.core_running(False)

    result = send("kitchen", "Backup done")

    assert "stack up core" in result["error"]
    assert docker.execs == [] and synapse.sent == []
