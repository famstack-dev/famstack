"""The AI's key: the oMLX the stack installs gets a key of its own.

oMLX takes a request only with its key (`auth.api_key` in
`~/.omlx/settings.json`). Earlier releases installed it with the key
`local`, and in port mode oMLX listens on every interface, so every device
at home could use the AI and its admin pages with that well-known word.

- A new install of the oMLX the stack manages makes a key like
  "7fk2-pq9w-m3xd-h8ra" and writes the same key into oMLX's settings and
  into `[ai] openai_key`, where every part of the stack reads it.
- An instance that still has `local` keeps it through an update, and the
  AI's service card names it on the Mac that runs famstack, with "Make a
  new key". The same card on the home address shows neither.
- "Make a new key" (`stack ai key new`) writes a new key into both places
  and restarts oMLX, which reads its settings only when it starts.
- An oMLX app or a server the stack did not install keeps its key: the
  card names none and the command changes nothing.

The install runs the AI hook's oMLX part as `stack up ai` runs it, with
Homebrew and oMLX stood in first on PATH: `brew` notes its arguments, and
`curl` (which the install uses to wait for oMLX) answers only a request
with the key that oMLX's settings file holds, as oMLX does. The commands
run through `./stack` in this process (`StackCLI`); the pages through the
tools server's app, with the host API stood in (`tests/dashboard_host.py`)
running `stack ai key` for real on its instance. Nothing reaches a real
oMLX: the AI's address is a closed port on localhost, and the home
directory is the test's.
"""

from __future__ import annotations

import importlib
import json
import re
import tomllib
from pathlib import Path

import pytest

from .conftest import REPO, StackCLI, a_closed_port
from .dsl import Page
from .setup_stand_in import on_this_mac, phone, stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

# What a generated key looks like: four groups of four letters and digits.
GENERATED = re.compile(r"[a-z0-9]{4}(-[a-z0-9]{4}){3}")


# ── The Mac ──────────────────────────────────────────────────────────────

class Mac:
    """The Simpsons' Mac: its home directory with oMLX's settings, the
    instance's `stack.toml`, and what the stood-in `brew` was asked."""

    def __init__(self, home: Path, root: Path, bin_dir: Path):
        self.home, self.root, self.bin_dir = home, root, bin_dir

    @property
    def omlx_key(self) -> str:
        """The key oMLX takes, from its settings file."""
        settings = self.home / ".omlx" / "settings.json"
        return json.loads(settings.read_text())["auth"]["api_key"]

    @property
    def stack_key(self) -> str:
        """The key every part of the stack sends: `[ai] openai_key`."""
        return tomllib.loads((self.root / "stack.toml").read_text())["ai"].get("openai_key", "")

    def brew_asked(self) -> list[str]:
        """Every `brew` command line, in order."""
        log = self.bin_dir / "brew.log"
        return log.read_text().splitlines() if log.exists() else []

    def curl_keys(self) -> list[str]:
        """The keys `curl` was asked to send to oMLX, in order."""
        log = self.bin_dir / "curl.log"
        return log.read_text().splitlines() if log.exists() else []


def _stand_ins(bin_dir: Path, home: Path) -> None:
    """`brew` that notes its arguments and says oMLX is installed, and
    `curl` that succeeds only for the key in oMLX's settings file."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "brew").write_text(f'#!/bin/sh\necho "$*" >> {bin_dir}/brew.log\n')
    settings = home / ".omlx" / "settings.json"
    (bin_dir / "curl").write_text(
        "#!/bin/sh\n"
        'key=$(printf "%s\\n" "$@" | sed -n "s/^Authorization: Bearer //p")\n'
        f'echo "$key" >> {bin_dir}/curl.log\n'
        f'grep -q "\\"api_key\\": \\"$key\\"" {settings}\n')
    for tool in ("brew", "curl"):
        (bin_dir / tool).chmod(0o755)


def _write_mac(tmp_path, monkeypatch, *, ai: str, omlx_key: str | None) -> Mac:
    """A Mac whose instance has this `[ai]` and whose oMLX settings hold
    `omlx_key` (None: oMLX was never installed)."""
    home, root, bin_dir = tmp_path / "home", tmp_path / "instance", tmp_path / "bin"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    if omlx_key is not None:
        (home / ".omlx").mkdir(parents=True)
        (home / ".omlx" / "settings.json").write_text(json.dumps(
            {"server": {"port": 42060}, "auth": {"api_key": omlx_key}}, indent=2))
    root.mkdir(parents=True, exist_ok=True)
    (root / ".stack").mkdir(exist_ok=True)
    (root / "stack.toml").write_text(
        f'[core]\ndata_dir = "{root / "data"}"\nhost = "localhost"\n'
        f'extension_dirs = ["{root / "extensions"}"]\n\n'
        f'[messages]\nserver_name = "simpson"\n\n{ai}')
    _stand_ins(bin_dir, home)
    return Mac(home, root, bin_dir)


def managed(key: str = "local", url: str = "") -> str:
    """`[ai]` for the oMLX the stack manages, at `url`, by default an
    address where nothing answers: no test reaches the oMLX that may run
    on this Mac."""
    url = url or f"http://127.0.0.1:{a_closed_port()}/v1"
    return f'[ai]\nprovider = "managed"\nopenai_url = "{url}"\nopenai_key = "{key}"\n'



APP = ('[ai]\nprovider = "external"\nopenai_url = "http://localhost:8000/v1"\n'
       'openai_key = "homers-app-key"\n')


@pytest.fixture
def mac(tmp_path, monkeypatch, as_stack_runs_it):
    """A function that sets the Mac up: `mac(ai=managed(), omlx_key="local")`."""
    def enter(*, ai: str = "", omlx_key: str | None = "local") -> Mac:
        return _write_mac(tmp_path, monkeypatch, ai=ai, omlx_key=omlx_key)
    return enter


def install(m: Mac) -> None:
    """The oMLX part of the AI's first `stack up ai`, as the hook runs it."""
    from stack import Stack
    from stack.hooks import build_hook_ctx

    on_install = importlib.import_module("stacklets.ai.hooks.on_install")
    stck = Stack(REPO, m.root / "data", instance_dir=m.root)
    ctx = build_hook_ctx("ai", step_fn=lambda _msg: None, stack=stck)
    state_dir = m.root / "ai-state"
    state_dir.mkdir()
    on_install.install_omlx(ctx, state_dir)


# ── A new install ────────────────────────────────────────────────────────

def test_a_new_install_gives_omlx_and_the_stack_the_same_generated_key(mac):
    """`on_configure` writes the placeholder before the install, as on
    every new Mac; the install replaces it in both places."""
    m = mac(ai=managed("local"), omlx_key=None)

    install(m)

    assert GENERATED.fullmatch(m.omlx_key), m.omlx_key
    assert m.stack_key == m.omlx_key
    # The install waits for oMLX with the key oMLX now takes.
    assert m.curl_keys()[-1] == m.omlx_key


def test_two_installs_make_two_different_keys(tmp_path, monkeypatch, as_stack_runs_it):
    keys = []
    for name in ("first", "second"):
        m = _write_mac(tmp_path / name, monkeypatch, ai=managed("local"), omlx_key=None)
        install(m)
        keys.append(m.omlx_key)

    assert keys[0] != keys[1]


def test_an_install_keeps_a_key_omlx_already_has(mac):
    """An oMLX set up with its own key before famstack installed it keeps
    that key, and the stack takes it."""
    m = mac(ai=managed("local"), omlx_key="marges-own-key")

    install(m)

    assert (m.omlx_key, m.stack_key) == ("marges-own-key", "marges-own-key")


# ── The key on the terminal ──────────────────────────────────────────────

def test_the_placeholder_is_named(mac, monkeypatch):
    """An install from an earlier release keeps `local` through the
    update, and `stack ai key show` names it."""
    m = mac(ai=managed("local"), omlx_key="local")

    ran = StackCLI(m.root, monkeypatch)("ai", "key", "show", "--json")

    assert ran.code == 0, ran.err
    assert ran.answer == {"managed": True, "placeholder": True}
    assert (m.omlx_key, m.stack_key) == ("local", "local")


def test_a_generated_key_is_not_named(mac, monkeypatch):
    m = mac(ai=managed("7fk2-pq9w-m3xd-h8ra"), omlx_key="7fk2-pq9w-m3xd-h8ra")

    ran = StackCLI(m.root, monkeypatch)("ai", "key", "show", "--json")

    assert ran.answer == {"managed": True, "placeholder": False}


def test_a_new_key_goes_into_omlx_and_the_stack(mac, monkeypatch):
    m = mac(ai=managed("local"), omlx_key="local")

    ran = StackCLI(m.root, monkeypatch)("ai", "key", "new", "--json")

    assert ran.code == 0, ran.err
    key = ran.answer["key"]
    assert GENERATED.fullmatch(key)
    assert (m.omlx_key, m.stack_key) == (key, key)
    assert ran.answer["applied"] == []


def test_a_new_key_restarts_a_running_omlx(mac, monkeypatch, httpserver):
    """oMLX reads its key only when it starts; one that answers is
    restarted, so it takes only the new key from then on."""
    httpserver.expect_request("/v1/models").respond_with_json({"data": []}, status=401)
    m = mac(ai=managed("local", httpserver.url_for("/v1")), omlx_key="local")

    StackCLI(m.root, monkeypatch)("ai", "key", "new", "--json")

    assert "services restart omlx" in m.brew_asked()


def test_a_new_key_asks_the_restarted_omlx_and_keeps_its_answer(mac, monkeypatch, httpserver):
    """Once oMLX restarted with its new key, the command asks it one short
    question, as `stack ai switch` does, and keeps the answer for the new
    key: the setup page reads it."""
    httpserver.expect_request("/v1/models").respond_with_json({"data": []})
    httpserver.expect_request("/v1/chat/completions", method="POST").respond_with_json(
        {"choices": [{"message": {"role": "assistant", "content": "OK"}}]})
    m = mac(ai=managed("local", httpserver.url_for("/v1")), omlx_key="local")
    stack = StackCLI(m.root, monkeypatch)

    stack("ai", "key", "new", "--json")
    kept = stack("ai", "recommended", "app", "--json").answer["answer"]

    assert kept is not None and kept["ok"] is True, kept


def test_a_stopped_omlx_is_not_started(mac, monkeypatch):
    m = mac(ai=managed("local"), omlx_key="local")

    StackCLI(m.root, monkeypatch)("ai", "key", "new", "--json")

    assert m.brew_asked() == []


def test_an_ai_server_the_stack_did_not_install_keeps_its_key(mac, monkeypatch):
    """The oMLX app on this Mac, or a server elsewhere: its admin set the key."""
    m = mac(ai=APP, omlx_key="local")
    cli = StackCLI(m.root, monkeypatch)

    shown = cli("ai", "key", "show", "--json")
    made = cli("ai", "key", "new", "--json")

    assert shown.answer == {"managed": False, "placeholder": False}
    assert made.code == 1 and "error" in made.answer
    assert (m.omlx_key, m.stack_key) == ("local", "homers-app-key")


# ── The AI's service card ────────────────────────────────────────────────

@pytest.fixture
def pages(tmp_path, monkeypatch, httpserver):
    """A function that puts the Simpsons' stack, AI included, in the done
    state with this `[ai]` and oMLX key, and returns the Mac."""
    def enter(*, ai: str, omlx_key: str = "local") -> Mac:
        m = _write_mac(tmp_path, monkeypatch, ai=ai, omlx_key=omlx_key)
        stack_in_state(tmp_path, monkeypatch, httpserver)("done")
        return m
    return enter


def ai_card(page: Page) -> str:
    """The AI's service card on a page, as served."""
    found = re.search(r'<article class="service-card" data-service="ai".*?</article>',
                      page.body, re.S)
    assert found, "no service card for the AI"
    return found[0]


def test_the_macs_ai_card_names_the_placeholder_with_make_a_new_key(pages):
    pages(ai=managed("local"))

    card = ai_card(on_this_mac().opens("/setup?step=stack"))

    assert "Key: local. Every device at home can use the AI with it." in card
    assert ">Make a new key</button>" in card


def test_the_card_names_it_on_the_dashboard_too(pages):
    pages(ai=managed("local"))

    card = ai_card(on_this_mac().opens("/dashboard"))

    assert ">Make a new key</button>" in card


def test_the_card_in_german(pages):
    pages(ai=managed("local"))

    card = ai_card(on_this_mac("de").opens("/setup?step=stack"))

    assert ">Neuen Schlüssel erstellen</button>" in card


def test_the_home_address_shows_no_key(pages):
    pages(ai=managed("local"))

    card = ai_card(phone().opens("/dashboard"))

    assert "Make a new key" not in card and "Key: local" not in card


@pytest.mark.parametrize("ai", [managed("7fk2-pq9w-m3xd-h8ra"), APP], ids=["generated", "app"])
def test_nothing_new_for_a_generated_key_or_a_server_the_stack_did_not_install(pages, ai):
    key = "7fk2-pq9w-m3xd-h8ra" if "managed" in ai else "local"
    pages(ai=ai, omlx_key=key)

    card = ai_card(on_this_mac().opens("/setup?step=stack"))

    assert "Make a new key" not in card and "Key:" not in card


def test_make_a_new_key_answers_the_new_key_on_the_mac(pages):
    m = pages(ai=managed("local"))

    status, answer = on_this_mac().posts("/dashboard/api/ai/key", {})

    assert status == 200, answer
    assert GENERATED.fullmatch(answer["key"])
    assert (m.omlx_key, m.stack_key) == (answer["key"], answer["key"])
    assert "Make a new key" not in ai_card(on_this_mac().opens("/setup?step=stack"))


def test_the_home_address_cannot_make_a_new_key(pages):
    m = pages(ai=managed("local"))
    status, _ = phone().posts("/dashboard/api/ai/key", {})

    assert status == 403
    assert (m.omlx_key, m.stack_key) == ("local", "local")
