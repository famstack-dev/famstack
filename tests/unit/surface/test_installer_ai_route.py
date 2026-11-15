"""The installer asks once how the stack's AI runs.

`./stack` typed in a terminal on a new Mac asks it after the dependency
checks and before core starts: famstack installs oMLX (Enter), your own
AI server, or no AI for now. The answer is written into `[ai]` the way
the setup page writes it, so the page asks nothing more:

- famstack's oMLX: `[ai]` names it and holds the key its settings file
  makes; once core is up the installer starts the AI's `up ai` run as a
  job of the host API, says the AI is set up in the background and that
  the browser shows how far it is, opens the browser and returns;
- your own server: the address and the key, checked as `stack ai switch`
  checks them; a failed check asks again or goes on without a server;
- no AI for now: what waits for the AI, then "Continue?"; yes writes
  `provider = "none"`.

Nothing is asked when `[ai]` names a route already or without a terminal.

Driven through the real entry point, `stack.cli.main`, from a checkout in
`tmp_path` with the installer's stand-ins (`test_install_from_file.py`):
Homebrew, Docker, the browser and a terminal that types the given lines.
The checkout carries the real ai stacklet, so its `stack ai switch` runs
for real. The host API is a stand-in on a loopback port that notes each
request and answers as the real one does once a run began: with the
run's first event. Your own AI server is a stand-in on a loopback port.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path

import pytest

from stack.live.envelope import RESULT, RUN, Event

from . import test_install_from_file as installer_tests
from .test_install_from_file import Terminal, install, read_toml, typed

# The installer's stand-ins, shared with its other surface tests: Homebrew,
# Docker, the checkout, and the terminal with its browser.
brew = installer_tests.brew
docker = installer_tests.docker
checkout = installer_tests.checkout
terminal = installer_tests.terminal

REPO = Path(__file__).resolve().parents[3]
CORE_TOKEN = "core-token-5d1e"


# ── Stand-ins ────────────────────────────────────────────────────────────

class HostAPI:
    """The host API on a loopback port: notes each request and answers
    with `reply`, one event line, as the real one answers a streamed `up`
    whose run began."""

    def __init__(self):
        self.asked: list[dict] = []
        self.reply = Event.now(1, RUN, {"stacklet": "ai", "action": "up"})
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen()
        self.port = self._sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                self.asked.append(json.loads(conn.makefile("rb").readline()))
                conn.sendall(self.reply.to_line().encode())

    def close(self):
        self._sock.close()


@pytest.fixture
def mac(checkout, terminal, monkeypatch):
    """The new Mac: the checkout with the real ai stacklet, the host API's
    stand-in, core's token as core's start leaves it, nothing running."""
    (checkout / "stacklets" / "ai").symlink_to(REPO / "stacklets" / "ai")
    monkeypatch.setattr("stack.docker.project_states", lambda: {})
    # The language follows the time zone of the Mac that runs the suite.
    monkeypatch.setattr("stack.installer.detect_language", lambda _tz: "en")
    # Core's start generates its token; the stand-in core has no hook for it.
    from stack.secrets import TomlSecretStore
    TomlSecretStore(checkout / ".stack" / "secrets.toml").set("core", "STACK_API_TOKEN", CORE_TOKEN)
    api = HostAPI()
    monkeypatch.setenv("STACK_API_PORT", str(api.port))
    yield api
    api.close()


def answers(monkeypatch, *lines: str) -> None:
    """The admin types `lines`: the first answers "Start the installation?"."""
    monkeypatch.setattr(sys, "stdin", Terminal(["", *lines]))


def ai(checkout) -> dict:
    return read_toml(checkout / "stack.toml").get("ai", {})


# ── The question ─────────────────────────────────────────────────────────

def test_the_install_asks_the_route_before_core_and_enter_picks_famstacks_omlx(
        checkout, mac, docker, terminal, capfd, monkeypatch):
    asked_before_core = []
    monkeypatch.setattr("stack.ai_route.choose", lambda title, options, default=None: (
        asked_before_core.append((title, options, list(docker.started))) or default))
    answers(monkeypatch)

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    title, options, started = asked_before_core[0]
    assert title == "How should famstack's AI run?"
    assert options == ["famstack installs oMLX (recommended)", "Your own AI server (address and key)",
                       "No AI for now"]
    assert started == []
    assert ai(checkout)["provider"] == "managed"


def test_a_german_stack_asks_in_german(checkout, mac, capfd, monkeypatch):
    monkeypatch.setattr("stack.installer.detect_language", lambda _tz: "de")
    answers(monkeypatch, "")

    _, printed = typed(capfd, monkeypatch)

    assert "Wie soll famstacks KI laufen?" in printed
    assert "famstack installiert oMLX (empfohlen)" in printed
    assert "Im Browser siehst du, wie weit sie ist." in printed


# ── Route 1: famstack installs oMLX ──────────────────────────────────────

def test_route_one_starts_the_ais_job_after_core_and_returns(checkout, mac, docker, terminal, capfd,
                                                            monkeypatch):
    answers(monkeypatch, "1")

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert docker.started == ["core"]
    assert mac.asked == [{"cmd": "up", "stacklet": "ai", "surface": "installer", "token": CORE_TOKEN,
                          "stream": True}]
    assert "The AI is set up in the background (20 to 30 minutes)" in printed
    assert "The browser shows how far it is." in printed
    assert len(terminal) == 1


def test_route_one_writes_famstacks_omlx_with_its_own_key(checkout, mac, capfd, monkeypatch):
    answers(monkeypatch, "")

    typed(capfd, monkeypatch)

    route = ai(checkout)
    assert route["provider"] == "managed"
    assert route["openai_url"] == "http://localhost:42060/v1"
    settings = checkout / "home" / "famstack-data" / "ai" / "omlx" / "settings.json"
    key = json.loads(settings.read_text())["auth"]["api_key"]
    assert key not in ("", "local")
    assert route["openai_key"] == key


def test_a_job_that_does_not_start_is_said_and_the_install_still_ends(checkout, mac, terminal, capfd,
                                                                     monkeypatch):
    mac.reply = Event.now(1, RESULT, {"error": "the ai stacklet is not found"})
    answers(monkeypatch, "")

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert "The AI did not start in the background: the ai stacklet is not found" in printed
    assert "set up in the background" not in printed
    assert len(terminal) == 1


# ── Route 2: your own AI server ──────────────────────────────────────────

def _serve_ai(httpserver):
    httpserver.expect_request("/v1/models").respond_with_json({"data": [{"id": "qwen3-8b"}]})
    httpserver.expect_request("/v1/chat/completions", method="POST").respond_with_json(
        {"choices": [{"message": {"role": "assistant", "content": "OK"}}]})
    return httpserver.url_for("/v1")


@pytest.fixture
def key_typed(monkeypatch):
    """The key the admin types without echo, one per question."""
    import getpass
    keys: list[str] = []
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: keys.pop(0))
    return keys


def test_route_two_checks_the_server_and_writes_it(checkout, mac, key_typed, httpserver, capfd,
                                                  monkeypatch):
    address = _serve_ai(httpserver)
    key_typed.append("sk-simpson")
    answers(monkeypatch, "2", address)

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    route = ai(checkout)
    assert route["provider"] == "external"
    assert route["openai_url"] == address
    assert route["openai_key"] == "sk-simpson"
    assert route["default"] == "qwen3-8b"
    assert mac.asked == []


def test_a_server_that_does_not_answer_asks_again(checkout, mac, key_typed, httpserver, capfd,
                                                 monkeypatch):
    from .conftest import a_closed_port
    address = _serve_ai(httpserver)
    key_typed.extend(["", ""])
    answers(monkeypatch, "2", f"http://127.0.0.1:{a_closed_port()}/v1", "y", address)

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert "Nothing answers at" in printed
    assert ai(checkout)["openai_url"] == address


def test_no_to_trying_again_goes_on_without_a_server(checkout, mac, key_typed, capfd, monkeypatch):
    from .conftest import a_closed_port
    key_typed.append("")
    answers(monkeypatch, "2", f"http://127.0.0.1:{a_closed_port()}/v1", "n")

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert "You can set it up in the browser later." in printed
    assert ai(checkout)["provider"] == ""
    assert mac.asked == []


# ── Route 3: no AI for now ───────────────────────────────────────────────

def test_route_three_warns_and_yes_writes_no_ai(checkout, mac, capfd, monkeypatch):
    answers(monkeypatch, "3", "y")

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert "Tags, the wiki and the diary come with the AI." in printed
    assert ai(checkout)["provider"] == "none"
    assert mac.asked == []


def test_no_to_continue_asks_the_route_again(checkout, mac, capfd, monkeypatch):
    answers(monkeypatch, "3", "n", "1")

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert printed.count("How should famstack's AI run?") == 2
    assert ai(checkout)["provider"] == "managed"


# ── Nothing asked ────────────────────────────────────────────────────────

def test_a_route_set_before_is_not_asked_again(checkout, mac, capfd, monkeypatch):
    answers(monkeypatch, "3", "y")
    typed(capfd, monkeypatch, "install")

    monkeypatch.setattr(sys, "stdin", Terminal())   # Enter for the start, nothing more
    code, printed = typed(capfd, monkeypatch, "install")

    assert code == 0, printed
    assert "How should famstack's AI run?" not in printed
    assert ai(checkout)["provider"] == "none"


def test_without_a_terminal_nothing_is_asked(checkout, mac, capfd, monkeypatch):
    """`./stack` starts the installer only in a terminal; a script installs
    with `stack install --family <file> --yes`, which asks nothing, and the
    setup page asks the route later."""
    code, printed = install(capfd, monkeypatch, "--family", "simpsons.toml", "--yes", "--no-pair")

    assert code == 0, printed
    assert "How should famstack's AI run?" not in printed
    assert ai(checkout)["provider"] == ""
    assert mac.asked == []


# ── Ctrl-C and terminals ─────────────────────────────────────────────────

class Interrupted(Terminal):
    """A terminal where the admin presses Ctrl-C at the AI's question."""

    def readline(self, *args):
        if not self.lines:
            raise KeyboardInterrupt
        return super().readline(*args)

    read = readline


def test_ctrl_c_at_the_question_writes_no_route_and_starts_nothing(checkout, mac, docker, terminal, capfd,
                                                                  monkeypatch):
    monkeypatch.setattr(sys, "stdin", Interrupted([""]))

    typed(capfd, monkeypatch)

    assert ai(checkout)["provider"] == ""
    assert docker.started == []
    assert mac.asked == []
    assert terminal == []


def test_needs_asking_needs_a_terminal(checkout, mac, monkeypatch):
    import io

    from stack import ai_route
    from stack.cli import create_stack
    (checkout / "stack.toml").write_text('[core]\nlanguage = "en"\n\n[ai]\nprovider = ""\n')
    stck = create_stack(checkout, checkout)

    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    assert ai_route.needs_asking(stck) is False
    monkeypatch.setattr(sys, "stdin", Terminal())
    assert ai_route.needs_asking(stck) is True


@pytest.mark.parametrize("route", [
    '[ai]\nprovider = "managed"\nopenai_url = "http://localhost:42060/v1"\nopenai_key = "k-1"\n',
    '[ai]\nprovider = "external"\nopenai_url = "http://192.168.1.20:8000/v1"\nopenai_key = "k-2"\n',
])
def test_a_route_set_before_is_neither_asked_nor_changed(checkout, mac, capfd, monkeypatch, route):
    (checkout / "stack.toml").write_text(f'[core]\nlanguage = "en"\ndata_dir = "{checkout / "data"}"\n\n'
                                         f'[messages]\nserver_name = ""\n\n{route}')
    (checkout / ".stack" / "ai.setup-done").touch()
    before = ai(checkout)
    monkeypatch.setattr(sys, "stdin", Terminal())

    code, printed = typed(capfd, monkeypatch, "install")

    assert code == 0, printed
    assert "How should famstack's AI run?" not in printed
    assert ai(checkout) == before
    assert mac.asked == []


# ── Route 2: what the admin types is data ────────────────────────────────

@pytest.mark.parametrize("key", ["-sk-dash", "--model"])
def test_a_key_that_looks_like_an_option_is_the_key(checkout, mac, key_typed, httpserver, capfd,
                                                   monkeypatch, key):
    address = _serve_ai(httpserver)
    key_typed.append(key)
    answers(monkeypatch, "2", address)

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert ai(checkout)["openai_key"] == key


def test_the_key_dash_alone_is_refused_and_asked_again(checkout, mac, key_typed, httpserver, capfd,
                                                       monkeypatch):
    """The switch reads `--key=-` as "the key comes on stdin", so "-" alone
    cannot be a key."""
    address = _serve_ai(httpserver)
    key_typed.extend(["-", "sk-simpson"])
    answers(monkeypatch, "2", address)

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert "A key cannot be just" in printed
    assert ai(checkout)["openai_key"] == "sk-simpson"


@pytest.mark.parametrize("address", ["managed", "-x", "--key", "not an address"])
def test_an_address_that_is_no_address_is_refused_and_asked_again(checkout, mac, key_typed, httpserver,
                                                                 capfd, monkeypatch, address):
    good = _serve_ai(httpserver)
    key_typed.append("")
    answers(monkeypatch, "2", address, good)

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert "is not the address of an AI server" in printed
    assert ai(checkout)["openai_url"] == good


def test_a_switch_that_exits_does_not_end_the_install(checkout, mac, key_typed, capfd, monkeypatch):
    def exits(self, *args, **kwargs):
        raise SystemExit(2)
    monkeypatch.setattr("stack.stack.Stack.run_cli_command", exits)
    key_typed.append("")
    answers(monkeypatch, "2", "http://192.168.1.20:8000/v1", "n")

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert "Core is running." in printed
    assert ai(checkout)["provider"] == ""


def test_the_key_is_never_on_a_command_line_on_screen_or_in_a_file(checkout, mac, key_typed, httpserver,
                                                                   capfd, monkeypatch):
    secret = "sk-simpson-7f3q"
    address = _serve_ai(httpserver)
    key_typed.append(secret)
    argv_seen = []
    real = __import__("stack.stack", fromlist=["Stack"]).Stack.run_cli_command

    def watched(self, *args, **kwargs):
        argv_seen.append(list(sys.argv))
        return real(self, *args, **kwargs)
    monkeypatch.setattr("stack.stack.Stack.run_cli_command", watched)
    answers(monkeypatch, "2", address)

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert argv_seen and all(secret not in " ".join(argv) for argv in argv_seen)
    assert secret not in printed
    holding = [p for p in checkout.rglob("*") if p.is_file() and p.name != "stack.toml"
               and secret in p.read_text(errors="replace")]
    assert holding == []


# ── Route 2: a server with several models ────────────────────────────────

def _serve_models(httpserver, models):
    httpserver.expect_request("/v1/models").respond_with_json({"data": [{"id": m} for m in models]})
    httpserver.expect_request("/v1/chat/completions", method="POST").respond_with_json(
        {"choices": [{"message": {"role": "assistant", "content": "OK"}}]})
    return httpserver.url_for("/v1")


def test_several_models_are_offered_and_enter_takes_the_first(checkout, mac, key_typed, httpserver, capfd,
                                                            monkeypatch):
    address = _serve_models(httpserver, ["llama-3-8b", "mistral-7b"])
    key_typed.append("")
    answers(monkeypatch, "2", address, "")

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert "1) llama-3-8b" in printed and "2) mistral-7b" in printed
    assert ai(checkout)["provider"] == "external"
    assert ai(checkout)["default"] == "llama-3-8b"


def test_a_model_picked_by_its_number_is_written(checkout, mac, key_typed, httpserver, capfd, monkeypatch):
    address = _serve_models(httpserver, ["llama-3-8b", "mistral-7b"])
    key_typed.append("")
    answers(monkeypatch, "2", address, "2")

    typed(capfd, monkeypatch)

    assert ai(checkout)["default"] == "mistral-7b"


def test_a_server_that_lists_the_recommended_model_asks_no_model(checkout, mac, key_typed, httpserver,
                                                               capfd, monkeypatch):
    """The install wrote the profile's model as `[ai] default`; the switch
    takes it when the server lists it, so there is nothing to choose."""
    recommended = recommended_model(checkout)
    address = _serve_models(httpserver, ["llama-3-8b", recommended])
    key_typed.append("")
    answers(monkeypatch, "2", address)

    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert ai(checkout)["default"] == recommended


def recommended_model(checkout) -> str:
    """The chat model the installer writes for this Mac's memory profile."""
    from stack.profile import memory_gb, profile_for
    return profile_for(memory_gb()).chat_model


# ── A second ./stack after an install that stopped ───────────────────────

def test_a_second_run_starts_the_ai_that_was_never_set_up(checkout, mac, capfd, monkeypatch):
    mac.reply = Event.now(1, RESULT, {"error": "the host API is not running"})
    answers(monkeypatch, "1")
    typed(capfd, monkeypatch, "install")
    mac.reply = Event.now(2, RUN, {"stacklet": "ai", "action": "up"})

    monkeypatch.setattr(sys, "stdin", Terminal())
    code, printed = typed(capfd, monkeypatch, "install")

    assert code == 0, printed
    assert "How should famstack's AI run?" not in printed
    assert [r["cmd"] for r in mac.asked] == ["up", "up"]
    assert "The AI is set up in the background" in printed


def test_a_second_run_leaves_an_ai_that_is_set_up_alone(checkout, mac, capfd, monkeypatch):
    answers(monkeypatch, "1")
    typed(capfd, monkeypatch, "install")
    (checkout / ".stack" / "ai.setup-done").touch()

    monkeypatch.setattr(sys, "stdin", Terminal())
    typed(capfd, monkeypatch, "install")

    assert len(mac.asked) == 1


def test_a_second_run_leaves_a_run_that_goes_on_alone(checkout, mac, capfd, monkeypatch):
    from stack import runs
    answers(monkeypatch, "1")
    typed(capfd, monkeypatch, "install")

    monkeypatch.setattr(sys, "stdin", Terminal())
    with runs.claim(checkout, ["ai"]):
        typed(capfd, monkeypatch, "install")

    assert len(mac.asked) == 1
