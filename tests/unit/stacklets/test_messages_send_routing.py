"""Where `stack messages send` and `upload` deliver a message, and how.

Three decisions, in this order:

  1. The room: an alias or a room id as given, or with `--to <person>`
     that person's private chat with the sender, the room whose only
     members are the two of them. No such room: nothing is sent, and the
     command fails so a scheduled send shows up as failed.
  2. The sender: the system account, or the one `--as` names.
  3. The path: an unencrypted room gets plain HTTP from the host. An
     encrypted room gets the message from the sender's command-line
     device in the bot runner, which encrypts it; the request reaches
     that device over stdin, so the password is in no process's arguments.

`stack messages upload` takes the same two paths for a file. In an
encrypted room the file goes to the device with the message, which
encrypts it before it reaches the media repository.

The command runs as `./stack` runs it, against a stand-in Synapse
(pytest-httpserver) and a stand-in `docker` on PATH, the two things
outside the host CLI it talks to. The encrypted send itself, inside the
container, is checked on the rig.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest
from werkzeug.wrappers import Response

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLI = _REPO_ROOT / "stacklets" / "messages" / "cli"

import base64

from stacklets.messages.cli import send as send_command
from stacklets.messages.cli import upload as upload_command

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
        self.uploads: list[bytes] = []
        # Whether reading a room's encryption state fails on the server's side.
        self.state_fails = False
        httpserver.expect_request("/_matrix/client/v3/login", method="POST") \
            .respond_with_handler(self._login)
        httpserver.expect_request("/_matrix/media/v3/upload", method="POST") \
            .respond_with_handler(self._upload)
        for method in ("GET", "POST", "PUT"):
            httpserver.expect_request(
                re.compile("/_matrix/client/v3/.*"), method=method,
            ).respond_with_handler(self._route)
        self.port = httpserver.port

    def room(self, room_id, *members, alias=None, encrypted=False, last_active=0):
        self.rooms[room_id] = {"members": {mxid(m) for m in members},
                               "encrypted": encrypted, "last_active": last_active}
        if alias:
            self.aliases[f"#{alias}:{SERVER}"] = room_id

    # ── handlers ──

    def _login(self, request):
        user = request.get_json()["user"]
        self.logins.append(user)
        return self._json({"access_token": f"token-{user}", "device_id": "HTTPDEVICE",
                           "user_id": mxid(user)})

    def _upload(self, request):
        self.uploads.append(request.get_data())
        return self._json({"content_uri": f"mxc://{SERVER}/upload{len(self.uploads)}"})

    def _route(self, request):
        user = mxid(request.headers.get("Authorization", "").removeprefix("Bearer token-"))
        path = request.path.removeprefix("/_matrix/client/v3")
        parts = path.strip("/").split("/")
        if parts[0] == "join":
            return self._json({})
        if parts[0] == "directory":
            room_id = self.aliases.get(parts[2])
            return self._json({"room_id": room_id}) if room_id else self._json({}, 404)
        if parts[0] == "joined_rooms":
            return self._json({"joined_rooms": [r for r, room in self.rooms.items()
                                                if user in room["members"]]})
        room = self.rooms[parts[1]]
        if parts[2] == "joined_members":
            return self._json({"joined": {m: {} for m in room["members"]}})
        if parts[2] == "messages":
            return self._json({"chunk": [{"origin_server_ts": room["last_active"]}]})
        if parts[2] == "state" and self.state_fails:
            return self._json({"errcode": "M_UNKNOWN", "error": "Internal server error"}, 500)
        if parts[2] == "state":
            if user not in room["members"]:
                return self._json({"errcode": "M_FORBIDDEN",
                                   "error": f"User {user} not in room"}, 403)
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


@pytest.fixture
def upload(synapse, docker, tmp_path, monkeypatch):
    """Run `stack messages upload <argv...>` as the CLI does; its result dict."""
    config = {
        "is_healthy": lambda: True,
        "instance_dir": str(tmp_path),
        "stack": {"messages": {"server_name": SERVER}},
        "secrets": SECRETS,
        "manifest": {"ports": {"synapse": synapse.port}},
    }

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["stack", "messages", "upload", *argv])
        return upload_command.run([], {}, config)
    return run


@pytest.fixture
def payslip(tmp_path):
    path = tmp_path / "payslip.pdf"
    path.write_bytes(b"%PDF-1.7 payslip for homer")
    return path


# ── an unencrypted room: plain HTTP, as before ─────────────────────────────

def test_an_unencrypted_room_gets_the_message_over_plain_http(send, synapse, docker):
    synapse.room("!chat:simpson", "stacker-bot", "homer", "marge", alias="chat")

    result = send("chat", "Backup done")

    assert result == {"ok": True, "room": "chat", "room_id": "!chat:simpson",
                      "event_id": "$sent-plain"}
    assert synapse.logins == ["stacker-bot"]
    assert synapse.sent == [("!chat:simpson", {"msgtype": "m.text", "body": "Backup done"})]
    assert docker.execs == []


def test_html_given_on_the_command_line_is_sent_as_the_formatted_message(send, synapse, docker):
    synapse.room("!chat:simpson", "stacker-bot", "homer", alias="chat")
    html = "<details><summary>Details</summary><p>Room 12</p></details>"

    send("chat", "Details: Room 12", "--html", html)

    assert synapse.sent == [("!chat:simpson", {
        "msgtype": "m.text", "body": "Details: Room 12",
        "format": "org.matrix.custom.html", "formatted_body": html})]


def test_a_message_can_say_it_mentions_nobody(send, synapse, docker):
    synapse.room("!chat:simpson", "stacker-bot", "homer", "marge", alias="chat")

    send("chat", "Filed: a note about Marge", "--mention-nobody")

    assert synapse.sent == [("!chat:simpson", {
        "msgtype": "m.text", "body": "Filed: a note about Marge", "m.mentions": {}})]


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


def test_when_the_server_cannot_tell_whether_a_room_is_encrypted_nothing_is_sent(
        send, synapse, docker):
    """Given an encrypted room whose encryption state the server cannot read,
    when a person sends into it, then nothing goes out: not knowing is no
    reason to send in the clear."""
    synapse.room("!docs:simpson", "stacker-bot", "homer", alias="documents", encrypted=True)
    synapse.state_fails = True

    result = send("documents", "the alarm code is 4711", "--as", "homer")

    assert "could not tell whether room 'documents' is encrypted" in result["error"]
    assert synapse.sent == [] and docker.execs == []


def test_with_core_down_an_encrypted_send_says_to_start_core(send, synapse, docker):
    synapse.room("!kitchen:simpson", "stacker-bot", "homer", alias="kitchen", encrypted=True)
    docker.core_running(False)

    result = send("kitchen", "Backup done")

    assert "stack up core" in result["error"]
    assert docker.execs == [] and synapse.sent == []


# ── --to: a person's private chat with the sender ─────────────────────────

def test_to_a_person_finds_their_private_chat_with_the_sender(send, synapse):
    synapse.room("!chat:simpson", "stacky-bot", "homer", "marge", alias="chat")
    synapse.room("!homer-dm:simpson", "stacky-bot", "homer")
    synapse.room("!marge-dm:simpson", "stacky-bot", "marge")

    result = send("--to", "homer", "--as", "agent", "Dentist", "tomorrow")

    assert result["room_id"] == "!homer-dm:simpson"
    assert synapse.sent == [("!homer-dm:simpson", {"msgtype": "m.text", "body": "Dentist tomorrow"})]


def test_to_a_person_without_a_private_chat_sends_nothing(send, synapse, docker):
    """A group room with the person in it is not their private chat."""
    synapse.room("!chat:simpson", "stacky-bot", "homer", "marge", alias="chat")

    result = send("--to", "homer", "--as", "agent", "Dentist tomorrow")

    assert result == {"error": "Homer has no private chat with Stacky yet."}
    assert synapse.sent == [] and docker.execs == []


def test_of_two_private_chats_the_one_used_last_gets_the_message(send, synapse):
    synapse.room("!old:simpson", "stacker-bot", "homer", last_active=1_000)
    synapse.room("!recent:simpson", "stacker-bot", "homer", last_active=2_000)

    assert send("--to", "homer", "Backup done")["room_id"] == "!recent:simpson"


def test_an_encrypted_private_chat_gets_the_message_encrypted(send, synapse, docker):
    synapse.room("!homer-dm:simpson", "stacky-bot", "homer", encrypted=True)

    result = send("--to", "homer", "--as", "agent", "Dentist tomorrow")

    assert result["event_id"] == "$sent-encrypted"
    assert json.loads(docker.execs[0]["stdin"])["room_id"] == "!homer-dm:simpson"


# ── stack messages upload: a file, by the same two paths ──────────────────

def test_an_upload_into_an_unencrypted_room_goes_over_plain_http(upload, synapse, docker,
                                                                  payslip):
    synapse.room("!docs:simpson", "stacker-bot", "homer", alias="documents")

    result = upload("documents", str(payslip), "--as", "homer")

    assert result["event_id"] == "$sent-plain"
    assert synapse.uploads == [payslip.read_bytes()]
    [(room_id, content)] = synapse.sent
    assert room_id == "!docs:simpson"
    assert content["url"] == f"mxc://{SERVER}/upload1" and "file" not in content
    assert docker.execs == []


def test_an_upload_into_an_encrypted_room_goes_to_the_bot_runner_to_be_encrypted(
        upload, synapse, docker, payslip):
    synapse.room("!docs:simpson", "stacker-bot", "homer", alias="documents", encrypted=True)

    result = upload("documents", str(payslip), "--as", "homer", "--caption", "October")

    assert result["event_id"] == "$sent-encrypted"
    assert synapse.uploads == [] and synapse.sent == []
    [call] = docker.execs
    assert call["argv"][-3:] == ["python", "-m", "stack.matrix.send"]
    request = json.loads(call["stdin"])
    assert request["room_id"] == "!docs:simpson"
    assert request["user_id"] == mxid("homer")
    assert base64.b64decode(request["attachment"]) == payslip.read_bytes()
    assert request["content"] == {
        "msgtype": "m.file", "body": "October", "filename": "payslip.pdf",
        "info": {"mimetype": "application/pdf", "size": len(payslip.read_bytes())}}


def test_an_encrypted_upload_has_the_password_in_no_process_arguments(upload, synapse,
                                                                      docker, payslip):
    synapse.room("!docs:simpson", "stacker-bot", "homer", alias="documents", encrypted=True)

    upload("documents", str(payslip), "--as", "homer")

    [call] = docker.execs
    assert "homer-pass" not in " ".join(call["argv"])
    assert json.loads(call["stdin"])["password"] == "homer-pass"


def test_with_core_down_an_encrypted_upload_says_to_start_core(upload, synapse, docker,
                                                               payslip):
    synapse.room("!docs:simpson", "stacker-bot", "homer", alias="documents", encrypted=True)
    docker.core_running(False)

    result = upload("documents", str(payslip))

    assert "stack up core" in result["error"]
    assert docker.execs == [] and synapse.uploads == [] and synapse.sent == []


def test_an_upload_to_an_unknown_room_uploads_nothing(upload, synapse, docker, payslip):
    result = upload("nowhere", str(payslip))

    assert result == {"error": "Room 'nowhere' not found"}
    assert synapse.uploads == [] and docker.execs == []


def test_an_upload_to_a_room_whose_encryption_cannot_be_read_uploads_nothing(
        upload, synapse, docker, payslip):
    """Given a room homer is a member of, whose encryption state the server
    cannot read, when he uploads a file, then nothing goes out, and he is
    told the server could not tell (not that he is no member)."""
    synapse.room("!docs:simpson", "stacker-bot", "homer", alias="documents", encrypted=True)
    synapse.state_fails = True

    result = upload("documents", str(payslip), "--as", "homer")

    assert "could not tell whether room 'documents' is encrypted" in result["error"]
    assert synapse.uploads == [] and synapse.sent == [] and docker.execs == []


def test_an_upload_to_a_room_the_person_is_not_in_says_what_the_server_said(
        upload, synapse, docker, payslip):
    """Given a room homer is not in, when he uploads a file to it, then nothing
    goes out, and he reads the server's reason rather than a guess."""
    synapse.room("!docs:simpson", "marge", alias="documents", encrypted=True)

    result = upload("documents", str(payslip), "--as", "homer")

    assert "could not tell whether room 'documents' is encrypted" in result["error"]
    assert f"User {mxid('homer')} not in room" in result["error"]
    assert synapse.uploads == [] and synapse.sent == [] and docker.execs == []


def test_a_send_to_a_room_the_person_is_not_in_sends_nothing(send, synapse, docker):
    synapse.room("!docs:simpson", "marge", alias="documents", encrypted=True)

    result = send("documents", "the alarm code is 4711", "--as", "homer")

    assert f"User {mxid('homer')} not in room" in result["error"]
    assert synapse.sent == [] and docker.execs == []
