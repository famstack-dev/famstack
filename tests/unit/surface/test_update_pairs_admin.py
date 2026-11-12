"""The restart that applies an update pairs the admin's phone.

After an update from 0.4.x the home address's pages open only on paired
devices, and an install from then has a family but no paired device. The
update restarts nothing; the `stack restart` it asks for brings core up
again, and then ends as a fresh install ends: the first admin's pairing
QR code, waiting for the phone where someone looks at the terminal, the
command that pairs the others, and the command that opens the Mac's own
pages. The Server room hears once where to pair, without a code.

Driven through the real entry point, `stack.cli.main`, from a checkout in
`tmp_path` with a stand-in core, as `test_install_from_file.py` does: the
instance is made by `stack install --family <file> --yes --no-pair`, then
`stack restart core` runs. Docker is stood in for in process. The chat is
a stand-in `stack` script in the checkout, which `stack messages send`
reaches as a process, and which notes each call. In a terminal the QR code
comes from core's tools server on the Mac's address (`MacAddress`), and
Homer's phone opens it while the command waits.
"""

from __future__ import annotations

import io
import json
import re
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from stack import cli

from .conftest import a_closed_port, tools_server
from .dsl import Browser
from .mac_address import MacAddress
from .setup_stand_in import stack_in_state

SIMPSONS = """\
name = "Simpson"

[[people]]
name = "Homer Simpson"
words = "he"

[[people]]
name = "Marge Simpson"
words = "she"

[[people]]
name = "Bart Simpson"
"""

HOME = "simpson.local:42000"
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1")
INTRO = "famstack's pages now open only on paired phones and browsers."
HEADING = "Pair your phone: scan this code with its camera"
OTHERS = "Pair the others on the Mac (Setup, Add a phone) or with: stack devices pair <id>"
ADMIN_LINK = "stack devices admin-link"
SERVER_ROOM_EN = ("Links to famstack now open only on paired phones and browsers. "
                  "Pair yours at the Mac: Setup, then Add a phone.")
SERVER_ROOM_DE = ("Links zu famstack öffnen sich jetzt nur auf hinzugefügten Handys und Browsern. "
                  "Füg deins am Mac hinzu: Einrichtung, dann Handy hinzufügen.")

# The chat as `stack messages send` reaches it: a script that notes its
# arguments, one JSON list per line, and answers as the chat took it.
CHAT = """#!/usr/bin/env python3
import json, sys
with open(%r, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
print(json.dumps({"ok": True}))
"""

server = tools_server("server")
access = tools_server("access")
codes = tools_server("devices")


class Checkout:
    """A checkout with a stand-in core and chat, and the Simpsons' family
    file. `stack(*argv)` runs `./stack` without a terminal; `sent()` is
    what reached the Server room."""

    def __init__(self, root: Path, capfd, monkeypatch):
        self.root, self.capfd, self.monkeypatch = root, capfd, monkeypatch
        self.chat_log = root / "chat.jsonl"

    def stack(self, *argv: str, terminal: bool = False) -> tuple[int, str]:
        self.monkeypatch.setattr(sys, "argv", ["stack", *argv])
        self.monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        self.monkeypatch.setattr(sys.stdout, "isatty", lambda: terminal)
        try:
            cli.main()
            code = 0
        except SystemExit as exit_:
            code = exit_.code or 0
        out, err = self.capfd.readouterr()
        return code, out + err

    def sent(self) -> list[list[str]]:
        """Each `stack messages send famstack ...` the chat got, as argv."""
        if not self.chat_log.exists():
            return []
        calls = [json.loads(line) for line in self.chat_log.read_text().splitlines()]
        return [c for c in calls if c[:3] == ["messages", "send", "famstack"]]

    def pairing_messages(self) -> list[list[str]]:
        return [c for c in self.sent() if "famstack" in c[3] and ("paired" in c[3] or "hinzugefügt" in c[3])]


@pytest.fixture
def checkout(tmp_path, monkeypatch, capfd):
    """The Simpsons' instance as the install makes it, with no device
    paired; core runs on a stand-in Docker."""
    core = tmp_path / "stacklets" / "core"
    core.mkdir(parents=True)
    (core / "stacklet.toml").write_text('id = "core"\nname = "Core"\n')
    (core / "docker-compose.yml").write_text("services:\n  tools:\n    image: busybox\n")
    (tmp_path / "simpsons.toml").write_text(SIMPSONS)
    stand_in = Checkout(tmp_path, capfd, monkeypatch)
    chat = tmp_path / "stack"
    chat.write_text(CHAT % str(stand_in.chat_log))
    chat.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STACK_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr("stack.installer._has_brew", lambda: True)
    # The Mac's address: nothing listens there unless a test puts the tools server there.
    monkeypatch.setattr("stack.devices.pairing.MAC_PORT", a_closed_port())
    with patch("stack.docker.check_docker", return_value=("Docker is running", None)), \
         patch("stack.docker.init_runtime", return_value=("orbstack", None)), \
         patch("stack.docker.ensure_network", return_value=("mocked", None)), \
         patch("stack.docker.compose_pull", return_value=None), \
         patch("stack.docker.compose_up", return_value=(0, "")), \
         patch("stack.docker.compose_stop", return_value=(0, "")), \
         patch("stack.docker.compose_down", return_value=(0, "")), \
         patch("stack.docker.running_project_ids", return_value={"core"}), \
         patch("stack.docker.container_commits", return_value={}):
        code, printed = stand_in.stack("install", "--family", "simpsons.toml", "--yes", "--no-pair")
        assert code == 0, printed
        # The install guesses the language from the Mac's timezone.
        set_language(tmp_path, "en")
        stand_in.chat_log.unlink(missing_ok=True)
        yield stand_in


def set_language(root: Path, language: str) -> None:
    toml = root / "stack.toml"
    text = toml.read_text()
    text = re.sub(r'(?m)^language = ".*"$', f'language = "{language}"', text, count=1)
    toml.write_text(text)


def qr_lines(printed: str) -> int:
    return sum(1 for line in printed.splitlines() if line.strip() and set(line.strip()) <= set("█▀▄ "))


# ── An install with a family and no paired device ────────────────────────

def test_without_a_terminal_the_restart_says_how_to_pair_without_a_qr_code(checkout):
    """Given a family and no paired device, when `stack restart core`
    runs without a terminal (the menu bar app, a pipe), then it ends with
    what changed, the command that pairs Homer's phone, the command for
    the others naming each of them, and the command that opens the Mac's
    pages; no QR code, no wait, and no admin key."""
    code, printed = checkout.stack("restart", "core")

    assert code == 0, printed
    end = printed[printed.index(INTRO):]
    assert "stack devices pair homer" in end
    assert OTHERS in end and "marge" in end and "bart" in end
    assert ADMIN_LINK in end
    assert qr_lines(printed) == 0
    assert "Waiting for the phone" not in printed
    key = (checkout.root / ".stack" / "admin-key").read_text().strip()
    assert key not in printed


def test_the_server_room_hears_once_where_to_pair(checkout):
    """Given a family and no paired device, when core is restarted twice,
    then the Server room got one message that says where to pair, with no
    code and no link; the terminal says it both times."""
    first = checkout.stack("restart", "core")
    second = checkout.stack("restart", "core")

    assert INTRO in first[1] and INTRO in second[1]
    [message] = checkout.pairing_messages()
    assert message[3] == SERVER_ROOM_EN
    assert "http" not in " ".join(message) and "pair=" not in " ".join(message)


def test_a_german_family_hears_it_in_german(checkout):
    """Given `[core] language = "de"`, when core is restarted, then the
    Server room's message is the German one, with the Mac's German labels."""
    set_language(checkout.root, "de")

    checkout.stack("restart", "core")

    assert [m[3] for m in checkout.pairing_messages()] == [SERVER_ROOM_DE]


def test_a_chat_that_did_not_take_it_hears_it_at_the_next_restart(checkout):
    """Given the chat refused the message, when core is restarted again
    with the chat back, then the Server room gets it then."""
    chat = checkout.root / "stack"
    working = chat.read_text()
    chat.write_text("#!/bin/sh\necho '{\"ok\": false, \"error\": \"messages is not running\"}'\n")
    checkout.stack("restart", "core")
    chat.write_text(working)

    checkout.stack("restart", "core")

    assert [m[3] for m in checkout.pairing_messages()] == [SERVER_ROOM_EN]


# ── Nothing new to say ───────────────────────────────────────────────────

def test_with_a_paired_device_the_restart_says_nothing_new(checkout):
    """Given Homer's phone is paired, when core is restarted, then the
    restart and the chat say nothing about pairing."""
    code, printed = checkout.stack("devices", "add", "--person", "homer", "--name", "iPhone",
                                   "--key-hash", "a" * 64, "--json")
    assert code == 0, printed

    code, printed = checkout.stack("restart", "core")

    assert code == 0, printed
    assert INTRO not in printed and ADMIN_LINK not in printed
    assert checkout.pairing_messages() == []


def test_without_a_family_the_restart_says_nothing_new(checkout):
    """Given no family yet, when core is restarted, then nothing is said
    about pairing."""
    (checkout.root / "users.toml").unlink()

    code, printed = checkout.stack("restart", "core")

    assert code == 0, printed
    assert INTRO not in printed
    assert checkout.pairing_messages() == []


# ── In a terminal: the QR code ───────────────────────────────────────────

@pytest.fixture
def tools_server_on_the_mac(checkout, tmp_path, monkeypatch, httpserver):
    """Core's tools server on the Mac's address, with the Simpsons as the
    host API's stand-in answers them; the command asks every 50 ms."""
    monkeypatch.setenv("LINK_BASE_URL", f"http://{HOME}/go")
    codes.CODES.forget()
    stack_in_state(tmp_path, monkeypatch, httpserver)("chat")
    monkeypatch.setenv("DEVICES_FILE", str(checkout.root / ".stack" / "devices" / "devices.toml"))
    monkeypatch.setattr("stack.devices.pairing.ASK_EVERY_SECS", 0.05)
    mac = MacAddress()
    monkeypatch.setattr("stack.devices.pairing.MAC_PORT", mac.port)
    yield mac
    mac.close()
    codes.CODES.forget()


def test_in_a_terminal_the_restart_ends_with_the_qr_code_for_homers_phone(checkout,
                                                                          tools_server_on_the_mac):
    """Given a family and no paired device, when `stack restart core`
    runs in a terminal, then after what changed it shows "Pair your phone:
    scan this code with its camera", the QR code for Homer, the first
    admin, and waits until his phone opened it; then the others' command
    and the Mac's pages' command follow."""
    mac = tools_server_on_the_mac

    def homer_opens_it():
        assert mac.made.wait(10), "the restart made no code"
        phone = Browser(server.app, listener=access.PUBLIC_PORT, host=HOME, user_agent=IPHONE,
                        by_name=True, paired=False)
        phone.opens(mac.answer["link"].split(HOME, 1)[1])
    thread = threading.Thread(target=homer_opens_it, daemon=True)
    thread.start()

    code, printed = checkout.stack("restart", "core", terminal=True)
    thread.join(10)

    assert code == 0, printed
    assert mac.answer["person"] == "homer"
    end = printed[printed.index(INTRO):]
    assert end.index(HEADING) < end.index(mac.answer["link"]) < end.index("Waiting for the phone")
    assert qr_lines(end) >= 15
    assert end.index("Waiting for the phone") < end.index(OTHERS) < end.index(ADMIN_LINK)


# ── The restart `stack update` asks for ──────────────────────────────────

def test_the_bare_restart_after_an_update_says_how_to_pair(checkout):
    """Given core runs the code of an older commit (the update moved the
    checkout past it), when the admin runs `stack restart` with no
    argument, as `stack update` tells them, then core is restarted and the
    restart ends with how to pair, and the Server room hears it."""
    import subprocess

    def git(*argv: str) -> str:
        return subprocess.run(["git", "-c", "user.email=homer@simpson.local", "-c", "user.name=Homer",
                               "-c", "core.hooksPath=/dev/null", *argv], cwd=checkout.root,
                              check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("add", "stacklets")
    git("commit", "-q", "-m", "the release core runs")
    started_from = git("rev-parse", "HEAD")
    (checkout.root / "stacklets" / "core" / "stacklet.toml").write_text('id = "core"\nname = "Core"\nversion = "2"\n')
    git("commit", "-q", "-am", "the release the update moved to")

    with patch("stack.docker.container_commits", return_value={"core": started_from}), \
         patch("stack.docker.project_states", return_value={"core": "running"}):
        code, printed = checkout.stack("restart")

    assert code == 0, printed
    assert "core: restarted" in printed
    assert "stack devices pair homer" in printed[printed.index(INTRO):]
    assert [m[3] for m in checkout.pairing_messages()] == [SERVER_ROOM_EN]
