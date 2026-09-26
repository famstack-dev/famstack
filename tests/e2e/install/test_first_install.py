"""The first-run installer, run the way a new admin runs it.

An admin clones the repo, types `./stack` in a terminal and answers the
wizard. What they get back is a running chat server and a final screen
telling them where to go, how to log in and what to type next. This
module runs that once, in a real pseudo-terminal on a Mac with nothing
of famstack on it, and then holds the final screen to account: every
URL, credential, room and command it printed has to be true.

Only `script/test remote install [ref]` runs it. That lane clones a
pushed ref onto the remote test Mac, resets the Mac to pristine before
and after, and sets:

    FAMSTACK_INSTALL_DIR      the fresh clone to run `./stack` in
    FAMSTACK_INSTALL_RESULTS  where the transcript is written
    FAMSTACK_INSTALL_PIDFILE  the installer's pid, so the lane can stop
                              it if this run dies first

Everything scraped here is read from the full recording with ANSI
stripped. The wizard clears the screen twice, so the last screen alone
would hide a traceback printed while the stacklets came up.
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

import pexpect
import pytest

CLONE = os.environ.get("FAMSTACK_INSTALL_DIR", "")
RESULTS = os.environ.get("FAMSTACK_INSTALL_RESULTS", "")
PIDFILE = os.environ.get("FAMSTACK_INSTALL_PIDFILE", "")

pytestmark = pytest.mark.skipif(
    not CLONE, reason="run through `script/test remote install`, which provides a pristine Mac"
)

# ── The family the admin types in ──────────────────────────────────────

FAMILY = "Simpson"
ADMIN = "Homer"
MEMBERS = ["Marge", "Bart", "Lisa"]

# The prompts the wizard is expected to show on a Mac that already has
# Homebrew and Docker, and what the admin types. Anything else it asks
# fails the run by name: a new question is a change to the first-run
# experience and should be a deliberate one.
def _answers() -> dict[str, list[str]]:
    return {
        "Family name": [FAMILY],
        "Language (en, de)": ["en"],
        "Your first name": [ADMIN],
        "Name (leave empty to continue)": [*MEMBERS, ""],
        "Ready?": ["y"],
    }


# `ask` prints `› <prompt> [default] `, `confirm` prints `? <question> [Y/n] `.
PROMPT = re.compile(r"^\s*[›?]\s+(?P<text>.+?)\s*(?:\[[^\]]*\])?\s*$")

# A prompt is output that stops, so wait this long before reading the
# last line as one. The spinners redraw every 80 ms and never go quiet.
QUIET_S = 1.5
# No output at all for this long means the installer hangs, or waits on
# a prompt that does not look like the wizard's own.
INACTIVITY_S = 600
TOTAL_S = 40 * 60

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-Za-z]|[@-Z\\-_])")


def plain(raw: str) -> str:
    """The recording as text: escape sequences gone, spinner redraws on their own lines."""
    return ANSI.sub("", raw).replace("\r\n", "\n").replace("\r", "\n")


def last_line(raw: str) -> str:
    text = ANSI.sub("", raw)
    return re.split(r"[\r\n]", text)[-1]


# ── A fresh login shell ────────────────────────────────────────────────
# What an admin has in a new terminal tab: a login shell built from
# their profile, not the test runner's environment (uv puts its venv's
# python first on PATH, and `./stack` would pick that one).

def _login_env() -> list[str]:
    keep = {"HOME", "USER", "LOGNAME", "TMPDIR", "LANG"}
    pairs = [f"{k}={v}" for k, v in os.environ.items() if k in keep]
    return [*pairs, "SHELL=/bin/zsh", "TERM=xterm-256color"]


def login_shell(command: str, cwd: str | Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/usr/bin/env", "-i", *_login_env(), "/bin/zsh", "-lc", command],
        cwd=cwd, capture_output=True, text=True, timeout=120,
    )


# ── Running the wizard ─────────────────────────────────────────────────

@dataclass
class InstallRun:
    exitstatus: int | None
    text: str
    seconds: float

    def after(self, marker: str) -> str:
        """The recording from the line of the last `marker` on."""
        i = self.text.rfind(marker)
        assert i >= 0, f"the installer never printed {marker!r}"
        return self.text[self.text.rfind("\n", 0, i) + 1:]

    def between(self, start: str, end: str) -> str:
        tail = self.after(start)
        j = tail.find(end)
        assert j >= 0, f"the installer printed {start!r} but no {end!r} after it"
        return tail[:j]


def _drive(child: pexpect.spawn, chunks: list[str]) -> None:
    answers = _answers()
    started = last_output = time.monotonic()
    answered_at = -1
    while True:
        try:
            chunks.append(child.read_nonblocking(65536, timeout=QUIET_S))
            last_output = time.monotonic()
            continue
        except pexpect.TIMEOUT:
            pass
        except pexpect.EOF:
            return

        raw = "".join(chunks)
        match = PROMPT.match(last_line(raw))
        if match and len(raw) != answered_at:
            question = match["text"]
            if question not in answers:
                raise AssertionError(f"the installer asked an unexpected question: {question!r}")
            if not answers[question]:
                raise AssertionError(f"the installer asked {question!r} more often than expected")
            child.sendline(answers[question].pop(0))
            answered_at = len(raw)
            continue

        now = time.monotonic()
        if now - last_output > INACTIVITY_S:
            raise AssertionError(
                f"no output for {INACTIVITY_S}s; last line: {last_line(raw).strip()!r}"
            )
        if now - started > TOTAL_S:
            raise AssertionError(f"the install did not finish within {TOTAL_S}s")


@pytest.fixture(scope="module")
def install() -> InstallRun:
    """Run `./stack` in the fresh clone once and answer the wizard."""
    results = Path(RESULTS)
    results.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = []
    started = time.monotonic()
    child = pexpect.spawn(
        "/usr/bin/env", [*("-i", *_login_env()), "/bin/zsh", "-lc", "exec ./stack"],
        cwd=CLONE, dimensions=(40, 120), encoding="utf-8", codec_errors="replace",
    )
    if PIDFILE:
        Path(PIDFILE).write_text(f"{child.pid}\n")
    try:
        _drive(child, chunks)
        child.close()
    finally:
        if child.isalive():
            child.close(force=True)
        raw = "".join(chunks)
        (results / "install-transcript.raw").write_text(raw)
        (results / "install-transcript.txt").write_text(plain(raw))
    return InstallRun(child.exitstatus, plain(raw), time.monotonic() - started)


# ── Scraping the final screen ──────────────────────────────────────────

def _final_screen(run: InstallRun) -> str:
    return run.after("are online")


def printed_url(run: InstallRun) -> str:
    urls = re.findall(r"https?://\S+", run.between("Open your browser", "Sign in"))
    assert len(urls) == 1, f"expected one URL under 'Open your browser', got {urls}"
    return urls[0]


def printed_login(run: InstallRun) -> tuple[str, str]:
    block = run.between("Sign in", "Explore your rooms")
    user = re.search(r"Username\s+(\S+)", block)
    password = re.search(r"Password\s+(\S+)", block)
    assert user and password, f"no username or password under 'Sign in':\n{block}"
    return user[1], password[1]


def printed_accounts(run: InstallRun) -> tuple[str, list[tuple[str, bool]]]:
    """The accounts the confirm screen listed, as (user id, is admin), and the server name.

    The confirm screen is the one that ends in "How it works". Its list
    has one account per line; the ticks after each name typed start with
    `✓` and are not matched.
    """
    end = run.text.find("How it works")
    assert end >= 0, "the installer never showed its confirm screen"
    block = run.text[:end]
    found = re.findall(r"^\s*@([\w.-]+):(\S+?)(\s+\(admin\))?\s*$", block, re.M)
    assert found, f"the confirm screen lists no accounts:\n{block}"
    servers = {server for _, server, _ in found}
    assert len(servers) == 1, f"accounts on more than one server: {servers}"
    return servers.pop(), [(uid, bool(admin)) for uid, _, admin in found]


def printed_commands(run: InstallRun) -> list[str]:
    block = run.between("Add more to your stack", "Service admin password")
    # A command, then its description after a run of spaces.
    commands = re.findall(r"^ +(\S+(?: \S+)*?) {2,}\S", block, re.M)
    assert commands, f"no commands under 'Add more to your stack':\n{block}"
    return commands


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


def homeserver(element_url: str) -> str:
    """The homeserver Element sends its users to, from Element's own config."""
    status, body = _http(f"{element_url.rstrip('/')}/config.json")
    assert status == 200, f"{element_url}/config.json answered {status}"
    return json.loads(body)["default_server_config"]["m.homeserver"]["base_url"].rstrip("/")


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


# ── The run itself ─────────────────────────────────────────────────────

def test_the_wizard_runs_to_the_end(install):
    assert install.exitstatus == 0, f"./stack exited {install.exitstatus}; see install-transcript.txt"
    print(f"install took {install.seconds:.0f}s")


def test_it_greets_the_family_by_name(install):
    assert f"The {FAMILY}s are online" in _final_screen(install)


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


def test_messages_and_core_are_healthy(install):
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


def test_the_printed_address_serves_element_on_the_lan(install):
    url = printed_url(install)
    parts = urllib.parse.urlsplit(url)
    assert parts.hostname == lan_ip(), f"{url} is not this Mac's LAN address {lan_ip()}"
    assert parts.port == 42030, f"{url} is not Element's port 42030"
    status, body = _http(url)
    assert status == 200 and b"Element" in body, f"{url} answered {status} without Element Web"


def test_the_printed_login_works(install):
    user, password = printed_login(install)
    login(homeserver(printed_url(install)), user, password)


def test_every_family_member_is_in_the_space_and_rooms(install):
    """Everyone joins the space, #famchat and #memories; the admin also #famstack."""
    hs = homeserver(printed_url(install))
    server, accounts = printed_accounts(install)
    assert {uid for uid, _ in accounts} == {n.lower() for n in [ADMIN, *MEMBERS]}

    missing = []
    for uid, is_admin in accounts:
        # The wizard's rule, printed under "How it works": the default
        # password is the first name, and the printed login shows its form.
        token = login(hs, uid, uid)
        rooms = {alias: resolve(hs, token, f"#{alias}:{server}")
                 for alias in ("family", "famchat", "memories", "famstack")}
        assert all(rooms.values()), f"rooms that do not exist: {[a for a, r in rooms.items() if not r]}"
        expected = ["family", "famchat", "memories"] + (["famstack"] if is_admin else [])
        mine = joined(hs, token)
        missing += [f"@{uid} not in #{a}" for a in expected if rooms[a] not in mine]
    assert not missing, "\n".join(missing)


def test_the_rooms_it_names_exist(install):
    hs = homeserver(printed_url(install))
    user, password = printed_login(install)
    server, _ = printed_accounts(install)
    token = login(hs, user, password)
    named = re.findall(r"#([\w-]+)", install.between("Explore your rooms", "Add more to your stack"))
    assert named
    missing = [a for a in named if not resolve(hs, token, f"#{a}:{server}")]
    assert not missing, f"named but missing: {missing}"


def test_the_secrets_file_holds_the_service_admin_password(install):
    path = re.search(r"Service admin password is in (\S+)", _final_screen(install))
    assert path, "the final screen does not say where the service admin password is"
    account = re.search(r"creates a (\S+) service account", install.text)
    assert account, "the wizard does not name the service account"
    secrets = tomllib.loads((Path(CLONE) / path[1]).read_text())
    login(homeserver(printed_url(install)), account[1], secrets["global__ADMIN_PASSWORD"])


def test_the_answered_language_is_the_family_language(install):
    # Answered "en" on a Mac whose time zone proposes "de": the answer wins,
    # for the family and as the first voice language.
    cfg = tomllib.loads((Path(CLONE) / "stack.toml").read_text())
    assert (cfg["core"]["language"], cfg["ai"]["language"]) == ("en", "en")


def test_the_printed_commands_name_real_commands(install):
    """Run from the checkout as `./stack`, each printed command parses.

    `up --help` accepts any name, so the stacklets it names are also
    looked up in `stack list`.
    """
    listed = login_shell("./stack list --json", CLONE)
    known = {s["id"] for s in json.loads(listed.stdout)["stacklets"]}
    broken = []
    for command in printed_commands(install):
        args = command.split()[1:]
        check = "./stack status" if args == ["status"] else " ".join(["./stack", *args, "--help"])
        result = login_shell(check, CLONE)
        if result.returncode != 0:
            broken.append(f"{command!r}: {(result.stderr or result.stdout).strip()[-300:]}")
        if args[:1] == ["up"]:
            broken += [f"{command!r}: no stacklet {sid!r}" for sid in args[1:] if sid not in known]
    assert not broken, "\n".join(broken)


def test_the_printed_commands_work_as_typed_in_a_new_terminal(install):
    """Typed exactly as printed, in a new login shell, each command runs.

    A new terminal opens in the home directory, not in the checkout, so
    that is where they run. `status` runs for real. The others would
    install services, so they are asked for their help instead, which
    proves the command and the stacklet resolve.
    """
    broken = []
    for command in printed_commands(install):
        check = command if command.split()[1:] == ["status"] else f"{command} --help"
        result = login_shell(check, Path.home())
        if result.returncode != 0:
            broken.append(f"{command!r}: {(result.stderr or result.stdout).strip()[-300:]}")
    assert not broken, "\n".join(broken)
