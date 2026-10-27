"""The first install, run the way an automated install runs it.

A script, a provisioning tool or this test installs famstack on a new Mac
from a family file: `./stack install --family <file> --yes`, in a login
shell with no terminal. Nothing may wait for input. What it gets back is
core running, the family in users.toml and stack.toml, the passwords, and
a last line saying where the dashboard is. The chat follows the way an
automated install brings it up, `./stack up messages`, which asks nothing
either now that the family named the chat's server. This module runs that
once on a Mac with nothing of famstack on it, and holds the result to
account: every account signs in, sits in the family's space and rooms,
and the dashboard greets the family.

The first run in the browser, which an admin who types `./stack` gets
instead, has a test of its own against a local tools server
(tests/integration/test_first_run_in_the_browser.py).

Only `script/test remote install [ref]` runs it. That lane clones a
pushed ref onto the remote test Mac, resets the Mac to pristine before
and after, and sets:

    FAMSTACK_INSTALL_DIR      the fresh clone to run `./stack` in
    FAMSTACK_INSTALL_RESULTS  where the transcript is written
    FAMSTACK_INSTALL_PIDFILE  the installer's pid, so the lane can stop
                              it if this run dies first
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

CLONE = os.environ.get("FAMSTACK_INSTALL_DIR", "")
RESULTS = os.environ.get("FAMSTACK_INSTALL_RESULTS", "")
PIDFILE = os.environ.get("FAMSTACK_INSTALL_PIDFILE", "")

pytestmark = pytest.mark.skipif(
    not CLONE, reason="run through `script/test remote install`, which provides a pristine Mac"
)

# ── The family the file describes ──────────────────────────────────────
# "en" on a Mac whose time zone proposes "de": the file's language wins.

FAMILY_FILE = """\
name = "Simpson"
language = "en"

[[people]]
name = "Homer"
words = "he"

[[people]]
name = "Marge"
words = "she"

[[people]]
name = "Bart"
words = "he"

[[people]]
name = "Lisa"
words = "she"
"""
PEOPLE = ["homer", "marge", "bart", "lisa"]

INSTALL_S = 40 * 60
UP_S = 20 * 60

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-Za-z]|[@-Z\\-_])")


def plain(raw: str) -> str:
    """The recording as text: escape sequences gone, spinner redraws on their own lines."""
    return ANSI.sub("", raw).replace("\r\n", "\n").replace("\r", "\n")


# ── A fresh login shell ────────────────────────────────────────────────
# What an admin has in a new terminal tab: a login shell built from
# their profile, not the test runner's environment (uv puts its venv's
# python first on PATH, and `./stack` would pick that one).

def _login_env() -> list[str]:
    keep = {"HOME", "USER", "LOGNAME", "TMPDIR", "LANG"}
    pairs = [f"{k}={v}" for k, v in os.environ.items() if k in keep]
    return [*pairs, "SHELL=/bin/zsh", "TERM=xterm-256color"]


# ── Running the install ────────────────────────────────────────────────

@dataclass
class Run:
    exitstatus: int
    text: str
    seconds: float


def run_without_a_terminal(command: str, name: str, timeout: int) -> Run:
    """`command` in a new login shell with stdin closed, as a script runs
    it; the transcript goes to the results as `<name>-transcript.txt`."""
    started = time.monotonic()
    child = subprocess.Popen(
        ["/usr/bin/env", "-i", *_login_env(), "/bin/zsh", "-lc", command],
        cwd=CLONE, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace",
    )
    if PIDFILE:
        Path(PIDFILE).write_text(f"{child.pid}\n")
    try:
        out, _ = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        out, _ = child.communicate()
    results = Path(RESULTS)
    results.mkdir(parents=True, exist_ok=True)
    (results / f"{name}-transcript.txt").write_text(plain(out))
    return Run(child.returncode, plain(out), time.monotonic() - started)


@pytest.fixture(scope="module")
def install() -> Run:
    """`./stack install --family <file> --yes` in the fresh clone, once."""
    family = Path(RESULTS) / "simpsons.toml"
    family.parent.mkdir(parents=True, exist_ok=True)
    family.write_text(FAMILY_FILE)
    return run_without_a_terminal(f"exec ./stack install --family {family} --yes", "install", INSTALL_S)


@pytest.fixture(scope="module")
def chat(install) -> Run:
    """`./stack up messages` after the install, asking nothing."""
    assert install.exitstatus == 0, "the install failed; see install-transcript.txt"
    return run_without_a_terminal("exec ./stack up messages", "messages", UP_S)


def config(name: str) -> dict:
    return tomllib.loads((Path(CLONE) / name).read_text())


# ── Matrix, as a client sees it ────────────────────────────────────────

def _http(url: str, body: dict | None = None, token: str | None = None) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def login(hs: str, user: str, password: str) -> str:
    status, body = _http(f"{hs}/_matrix/client/v3/login", {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": user},
        "password": password,
    })
    assert status == 200, f"{user} cannot log in to {hs}: {status} {body[:200]!r}"
    return json.loads(body)["access_token"]


def resolve(hs: str, token: str, alias: str) -> str | None:
    quoted = urllib.parse.quote(alias, safe="")
    status, body = _http(f"{hs}/_matrix/client/v3/directory/room/{quoted}", token=token)
    return json.loads(body)["room_id"] if status == 200 else None


def joined(hs: str, token: str) -> set[str]:
    status, body = _http(f"{hs}/_matrix/client/v3/joined_rooms", token=token)
    assert status == 200, f"joined_rooms answered {status}"
    return set(json.loads(body)["joined_rooms"])


def lan_ip() -> str:
    """The address other devices on the network reach this Mac on."""
    route = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True)
    iface = re.search(r"interface:\s*(\S+)", route.stdout)
    assert iface, f"no default route:\n{route.stdout}{route.stderr}"
    ip = subprocess.run(["ipconfig", "getifaddr", iface[1]], capture_output=True, text=True)
    return ip.stdout.strip()


# ── The install ────────────────────────────────────────────────────────

def test_the_install_runs_to_the_end_without_a_terminal(install):
    assert install.exitstatus == 0, f"./stack install exited {install.exitstatus}; see install-transcript.txt"
    print(f"install took {install.seconds:.0f}s")


def test_it_names_the_family_and_where_the_dashboard_is(install):
    assert "The Simpsons are set up" in install.text
    urls = re.findall(r"https?://\S+/dashboard", install.text)
    assert urls, "the install does not say where the dashboard is"
    parts = urllib.parse.urlsplit(urls[-1])
    assert (parts.hostname, parts.port) == (lan_ip(), 42000), urls[-1]


def test_nothing_it_printed_is_an_error_or_a_hole(install):
    """A traceback, a `None` or an unfilled template reads as a broken install."""
    text = install.text
    problems = [
        *(["a traceback"] if "Traceback (most recent call last)" in text else []),
        *(["'Unknown error'"] if "Unknown error" in text else []),
        *[f"{m!r}" for m in re.findall(r".*\bNone\b.*", text)],
        *[f"unfilled {m!r}" for m in re.findall(r"\{[A-Za-z_][A-Za-z0-9_.]*\}", text)],
    ]
    assert not problems, "the installer printed:\n" + "\n".join(problems)


def test_the_family_is_written(install):
    users = config("users.toml")["users"]
    assert [u["id"] for u in users] == PEOPLE
    assert [u["role"] for u in users] == ["admin", "member", "member", "member"]
    assert [u["relation"] for u in users] == ["parent", "parent", "child", "child"]
    cfg = config("stack.toml")
    assert cfg["core"]["stack_owner"] == "Simpson"
    assert cfg["messages"]["server_name"] == "simpson"
    assert (cfg["core"]["language"], cfg["ai"]["language"]) == ("en", "en")


def test_the_dashboard_greets_the_family(install):
    url = f"http://{lan_ip()}:42000/dashboard"
    status, body = _http(url)
    assert status == 200, f"{url} answered {status}"
    page = body.decode()
    assert "Welcome home, <em>Simpsons.</em>" in page
    assert "data-first-run" not in page, "the family is set, so there is no first run"


# ── The chat, brought up after ─────────────────────────────────────────

def test_the_chat_comes_up_without_a_question(chat):
    assert chat.exitstatus == 0, f"./stack up messages exited {chat.exitstatus}; see messages-transcript.txt"


def test_messages_and_core_are_healthy(chat):
    names = []
    for sid in ("messages", "core"):
        compose = (Path(CLONE) / "stacklets" / sid / "docker-compose.yml").read_text()
        names += re.findall(r"^\s*container_name:\s*(\S+)", compose, re.M)
    assert names

    # A container without a healthcheck counts once it runs. Give a
    # service still inside its start period a minute to settle.
    deadline = time.monotonic() + 60
    while True:
        states = {}
        for name in names:
            out = subprocess.run(
                ["docker", "inspect", "-f",
                 "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}", name],
                capture_output=True, text=True,
            )
            states[name] = out.stdout.strip() or out.stderr.strip()
        bad = {n: s for n, s in states.items() if s not in ("running", "running healthy")}
        if not bad or time.monotonic() > deadline:
            break
        time.sleep(5)
    assert not bad, f"not healthy: {bad}"


def test_every_family_member_is_in_the_space_and_rooms(chat):
    """Everyone joins the space, #famchat and #memories; the admin also
    #famstack. A first password is the username, as `stack family set`
    writes it."""
    hs = "http://localhost:42031"
    server = config("stack.toml")["messages"]["server_name"]
    missing = []
    for user in config("users.toml")["users"]:
        uid, is_admin = user["id"], user["role"] == "admin"
        token = login(hs, uid, uid)
        rooms = {alias: resolve(hs, token, f"#{alias}:{server}")
                 for alias in ("family", "famchat", "memories", "famstack")}
        assert all(rooms.values()), f"rooms that do not exist: {[a for a, r in rooms.items() if not r]}"
        expected = ["family", "famchat", "memories"] + (["famstack"] if is_admin else [])
        mine = joined(hs, token)
        missing += [f"@{uid} not in #{a}" for a in expected if rooms[a] not in mine]
    assert not missing, "\n".join(missing)


def test_the_secrets_file_holds_the_service_admin_password(chat):
    secrets = tomllib.loads((Path(CLONE) / ".stack" / "secrets.toml").read_text())
    login("http://localhost:42031", "stackadmin", secrets["global__ADMIN_PASSWORD"])
