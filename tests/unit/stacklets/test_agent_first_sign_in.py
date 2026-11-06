"""The agent signs in to chat by itself, whenever its account comes to exist.

Core's bot runner creates the agent's chat account, and core refreshes at the
end of `./stack up`, after the agent's container has started. nanobot tries its
password once and gives up, so the container's entrypoint holds nanobot back
until a login works.

These tests run the real entrypoint as a process, with a stand-in Matrix server
on localhost and a stand-in `nanobot` that tells that server when it starts, so
the order of what the server saw is the order of what happened. `sleep` is a
stand-in too, so a test does not sit through the wait between two attempts.

The entrypoint's paths under /app and /setup-state are fixed to the container.
The test runs a copy of the script with those two prefixes moved into its
temporary folder; everything else in the script runs as it ships.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from werkzeug import Request, Response

AGENT = Path(__file__).resolve().parents[3] / "stacklets" / "agent"

PASSWORD = "fabricated-agent-password"
USER_ID = "@stacky-bot:simpson"
SAVED_TOKEN = "saved-token"
NANOBOT_STARTED = "nanobot started"


class MatrixStandIn:
    """Synapse's client API, as far as the entrypoint uses it.

    Refuses `refused_logins` logins with the answer Synapse gives for an account
    that does not exist, then accepts them. Records each event in `events`, and
    keeps in `open_devices` the access tokens that were handed out and not logged
    out, which is what a login leaves behind on the real server.
    """

    def __init__(self, httpserver, refused_logins=0, saved_token_valid=True):
        self.events: list[str] = []
        self.open_devices: set[str] = set()
        self.login_bodies: list[dict] = []
        self.refused_logins = refused_logins
        self.saved_token_valid = saved_token_valid
        self.url = httpserver.url_for("").rstrip("/")
        for path, handler in (
            ("/_matrix/client/v3/login", self.login),
            ("/_matrix/client/v3/logout", self.logout),
            ("/_matrix/client/v3/account/whoami", self.whoami),
            ("/nanobot-started", self.nanobot_started),
        ):
            httpserver.expect_request(path).respond_with_handler(handler)

    def login(self, request: Request) -> Response:
        self.login_bodies.append(json.loads(request.data))
        if self.refused_logins > 0:
            self.refused_logins -= 1
            self.events.append("login refused")
            return _json(403, {"errcode": "M_FORBIDDEN", "error": "Invalid username or password"})
        token = f"token-{len(self.login_bodies)}"
        self.open_devices.add(token)
        self.events.append("login accepted")
        return _json(200, {"user_id": USER_ID, "access_token": token, "device_id": "DEVICE"})

    def logout(self, request: Request) -> Response:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in self.open_devices:
            return _json(401, {"errcode": "M_UNKNOWN_TOKEN"})
        self.open_devices.remove(token)
        self.events.append("logout")
        return _json(200, {})

    def whoami(self, request: Request) -> Response:
        self.events.append("whoami")
        if self.saved_token_valid:
            return _json(200, {"user_id": USER_ID})
        return _json(401, {"errcode": "M_UNKNOWN_TOKEN"})

    def nanobot_started(self, _request: Request) -> Response:
        self.events.append(NANOBOT_STARTED)
        return _json(200, {})


def _json(status: int, body: dict) -> Response:
    return Response(json.dumps(body), status=status, content_type="application/json")


class Container:
    """What the agent's container holds, in a temporary folder."""

    def __init__(self, tmp_path: Path, matrix: MatrixStandIn):
        self.home = tmp_path / "home"
        self.matrix_store = self.home / ".nanobot" / "matrix-store"
        self.matrix = matrix
        self.tmp_path = tmp_path
        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir()
        self.home.mkdir()

        app = tmp_path / "app"
        app.mkdir()
        (app / "config.json").write_text((AGENT / "config.json").read_text())
        (app / "workspace-seed").symlink_to(AGENT / "workspace")
        (app / "runtime").symlink_to(AGENT / "runtime")

        setup_state = tmp_path / "setup-state"
        setup_state.mkdir()
        (setup_state / "secrets.toml").write_text(f'agent__AGENT_BOT_PASSWORD = "{PASSWORD}"\n')

        script = (AGENT / "entrypoint.sh").read_text()
        self.script = tmp_path / "entrypoint.sh"
        self.script.write_text(
            script.replace("/app/", f"{app}/").replace("/setup-state/", f"{setup_state}/"))

        # The system's python3 may be older than the entrypoint's tomllib.
        (self.bin_dir / "python3").symlink_to(sys.executable)
        _executable(self.bin_dir / "sleep", "#!/bin/sh\n")
        _executable(self.bin_dir / "nanobot", (
            "#!/bin/sh\n"
            'python3 -c "import sys, urllib.request; urllib.request.urlopen(sys.argv[1])" '
            '"$AGENT_MATRIX_HOMESERVER/nanobot-started"\n'))

    def with_saved_session(self) -> None:
        self.matrix_store.mkdir(parents=True)
        (self.matrix_store / "session.json").write_text(json.dumps(
            {"access_token": SAVED_TOKEN, "user_id": USER_ID, "device_id": "SAVED"}))

    def without_password(self) -> None:
        (self.tmp_path / "setup-state" / "secrets.toml").write_text("")

    def start(self, expect_exit: int = 0) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.bin_dir}:/usr/bin:/bin",
            "HOME": str(self.home),
            "AGENT_MATRIX_HOMESERVER": self.matrix.url,
            "AGENT_MATRIX_SERVER_NAME": "simpson",
            "AGENT_BOT_ID": "stacky-bot",
        }
        result = subprocess.run(["sh", str(self.script)], capture_output=True, text=True,
                                env=env, cwd=self.tmp_path, timeout=30)
        assert result.returncode == expect_exit, result.stdout + result.stderr
        return result


def _executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


@pytest.fixture
def matrix(httpserver):
    return MatrixStandIn(httpserver)


@pytest.fixture
def container(tmp_path, matrix):
    return Container(tmp_path, matrix)


def test_nanobot_starts_only_after_the_account_accepts_a_login(container, matrix):
    """Given an account that does not exist yet, when the container starts, then
    it waits, and nanobot starts once a login works, with no further command.

    This is the first `./stack up agent` of a family: without the wait nanobot
    tries once, stays deaf, and the admin has to restart it by hand.
    """
    matrix.refused_logins = 3

    container.start()

    assert matrix.events == ["login refused"] * 3 + ["login accepted", "logout", NANOBOT_STARTED]


def wait_lines(result: subprocess.CompletedProcess) -> list[str]:
    """The lines of the container's log that name the awaited account."""
    return [line for line in (result.stdout + result.stderr).splitlines() if USER_ID in line]


def test_the_wait_says_so_at_once_and_then_each_minute(container, matrix):
    """Given refused logins with 10 seconds between attempts, then the log names
    the account at the first refusal and again each minute, so an admin who opens
    the log later still finds the reason. A line per attempt would bury the rest."""
    matrix.refused_logins = 13

    result = container.start()

    assert len(wait_lines(result)) == 3, result.stdout + result.stderr


def test_the_wait_names_both_causes_and_never_the_password(container, matrix):
    """Given a refused login, then the line names both causes, because the server
    answers M_FORBIDDEN for a missing account and for a wrong password alike. The
    log never holds the password."""
    matrix.refused_logins = 1

    result = container.start()

    line = wait_lines(result)[0]
    assert "does not exist" in line and "password" in line
    assert PASSWORD not in result.stdout + result.stderr


def test_an_empty_password_stops_the_container_with_what_is_missing(container, matrix):
    """Given no stored password and no saved session, then the container stops at
    once and names the secret, because no number of attempts can succeed."""
    container.without_password()

    result = container.start(expect_exit=1)

    log = result.stdout + result.stderr
    assert "AGENT_BOT_PASSWORD" in log
    assert ".stack/agent.setup-done" in log and "./stack up agent" in log
    assert matrix.events == []


def test_a_saved_session_needs_no_password(container, matrix):
    """Given a valid saved session and no stored password, then nanobot starts,
    because only a login needs the password."""
    container.without_password()
    container.with_saved_session()

    container.start()

    assert matrix.events == ["whoami", NANOBOT_STARTED]


def test_the_login_check_uses_the_accounts_own_password(container, matrix):
    """The check logs in as the account nanobot uses, with the stored password:
    a login under another name would say nothing about nanobot's."""
    container.start()

    identity = matrix.login_bodies[0]
    assert identity["identifier"] == {"type": "m.id.user", "user": USER_ID}
    assert identity["password"] == PASSWORD


def test_an_existing_account_starts_at_once_and_keeps_no_extra_device(container, matrix):
    """Given an account that exists and no saved session, then one login, and
    the check's device is gone before nanobot makes its own. Each device of the
    account gets the family's encrypted room keys, so a leftover one costs."""
    container.start()

    assert matrix.events == ["login accepted", "logout", NANOBOT_STARTED]
    assert matrix.open_devices == set()


def test_a_valid_saved_session_logs_in_nowhere(container, matrix):
    """Given a saved session the server still knows, then no login at all and the
    session stays, because a new login would be a new device for every restart."""
    container.with_saved_session()

    container.start()

    assert matrix.events == ["whoami", NANOBOT_STARTED]
    assert matrix.login_bodies == []
    assert (container.matrix_store / "session.json").exists()


def test_a_stale_saved_session_waits_for_a_working_login_too(container, matrix):
    """Given a saved session the server no longer knows, then it is dropped, and
    nanobot starts only once a fresh login works."""
    matrix.saved_token_valid = False
    matrix.refused_logins = 1
    container.with_saved_session()

    container.start()

    assert matrix.events == ["whoami", "login refused", "login accepted", "logout", NANOBOT_STARTED]
    assert not (container.matrix_store / "session.json").exists()


def test_a_server_that_does_not_answer_counts_as_not_yet(tmp_path):
    """Given no chat server answering, then the attempt says "not yet", without a
    traceback, because the entrypoint runs under `set -e` and repeats the attempt
    until Synapse is up."""
    result = subprocess.run(
        [sys.executable, str(AGENT / "runtime" / "try_login.py")],
        capture_output=True, text=True, cwd=tmp_path, timeout=30,
        env={"AGENT_MATRIX_HOMESERVER": "http://127.0.0.1:1", "AGENT_BOT_ID": "stacky-bot",
             "AGENT_MATRIX_SERVER_NAME": "simpson", "AGENT_MATRIX_PASSWORD": PASSWORD})

    assert result.returncode == 1
    assert result.stderr == ""
