"""The AI step of /setup: the AI from the oMLX app, as the admin meets it.

Step 4 finds the oMLX app on this Mac and offers it, or says where to get
it; either way "Install oMLX for me instead" shows the terminal's
`./stack up ai`. Using the app asks for its API key, only on the Mac that
runs famstack. Connected, the step shows what this Mac's memory profile
recommends (the chat model, and with speech the speech model and the
house voice), downloads it through oMLX's admin API with a bar per
model, and once the AI runs says what it can do.

Driven through the tools server's FastAPI app as a browser opens it, read
as served, with the host API in the dashboard stand-in's "services" state
(`tests/dashboard_host.py`). The AI step's two host commands, `stack ai
recommended show` and `download`, and the setup forms run for real on
the stand-in's instance, against a stand-in for the oMLX app's admin API
on localhost (`omlx_stand_in.py`), where the AI stacklet looks for the app.
The instance is a 16 GB Mac by its profile (`[core] profile = "small"`):
speech is off unless the admin turns it on.
"""

from __future__ import annotations

import importlib
import re
import tomllib

import pytest

from tests.unit.stacklets.omlx_stand_in import BROKEN, KEY, OMLX, hanging

from .conftest import a_closed_port
from .dsl import Page
from .setup_stand_in import (core_restarts, on_this_mac, phone, stack_in_state,
                             the_ai_answered)

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

CHAT = "mlx-community/Qwen3.5-9B-MLX-4bit"
WHISPER = "mlx-community/whisper-large-v3-turbo-asr-fp16"
KOKORO = "mlx-community/Kokoro-82M-bf16"


# ── The instance ─────────────────────────────────────────────────────────

class Mac:
    """The Simpsons' Mac at the AI step: the host API's stand-in, the
    oMLX app's stand-in (None when the app is not open) and the instance's
    `stack.toml`."""

    def __init__(self, host, app, root):
        self.host, self.app, self.root = host, app, root

    def toml(self) -> dict:
        return tomllib.loads((self.root / "stack.toml").read_text())

    def connects(self, key: str = KEY, browser=None) -> tuple[int, dict]:
        """Connect as the page's Connect does: the setup forms' route."""
        return (browser or on_this_mac()).posts(
            "/dashboard/api/configure/ai",
            {"answers": {"use_running_server": True, "api_key": key}})

    def presses_installed(self, browser=None) -> tuple[int, dict]:
        """Press "I installed oMLX" as the page's script does: connect the
        app at its default address, without a key."""
        return (browser or on_this_mac()).posts(
            "/dashboard/api/configure/ai", {"answers": {"use_running_server": True}})

    def connects_as(self, address: str, key: str, browser=None) -> tuple[int, dict]:
        """Connect with the address and key fields, as their Connect does."""
        return (browser or on_this_mac()).posts(
            "/dashboard/api/ai/switch", {"address": address, "key": key})


def _write_toml(root, extra: str = "") -> None:
    """A small Mac's instance: `[core] profile = "small"`, port mode."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "stack.toml").write_text(
        f'[core]\ndata_dir = "{root / "data"}"\nhost = "localhost"\nprofile = "small"\n'
        f'extension_dirs = ["{root / "extensions"}"]\n\n[messages]\nserver_name = "simpson"\n'
        + extra)


@pytest.fixture
def mac(tmp_path, monkeypatch, httpserver):
    """A function that sets the Mac up: `mac(app=True, downloaded=[...])`.
    With `app` the oMLX app answers where the AI stacklet looks for it,
    wanting its key; without, nothing answers there. With `computes`
    False the app lists its models and fails every completion."""
    running_server = importlib.import_module("stacklets.ai.running_server")

    def enter(*, app: bool = True, downloaded=(), holds: bool = True, ai: str = "",
              state: str = "services", wants_key: bool = True, at_default: bool = True,
              installed: bool | None = None, macos: str = "15.5",
              computes: bool = True) -> Mac:
        root = tmp_path / "instance"
        _write_toml(root, ai)
        host = stack_in_state(tmp_path, monkeypatch, httpserver)(state)
        omlx = OMLX(httpserver, downloaded=downloaded, wants_key=wants_key, holds=holds,
                    computes=computes) if app else None
        where = httpserver.url_for("/v1") if app and at_default \
            else f"http://127.0.0.1:{a_closed_port()}/v1"
        monkeypatch.setattr(running_server, "RUNNING_SERVERS", (where,))
        # The macOS the host reports, so the step's download does not
        # depend on the Mac the tests run on.
        monkeypatch.setattr(importlib.import_module("stacklets.ai.omlx_app"), "this_macos",
                            lambda: macos)
        # The stack engine's address, which the check also looks at: closed,
        # unless a test stands the app in there.
        monkeypatch.setattr(running_server, "STACK_ENGINE_ADDRESSES",
                            (f"http://127.0.0.1:{a_closed_port()}/v1",))
        # The app's bundle in a stand-in Applications folder: there when the
        # app runs, unless a test says otherwise.
        bundle = tmp_path / "Applications" / "oMLX.app"
        if app if installed is None else installed:
            bundle.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(importlib.import_module("stacklets.ai.omlx_app"), "APP_BUNDLE", bundle)
        host.configure = lambda answers: host._stack().configure("ai", answers)
        return Mac(host, omlx, root)
    return enter


def connected(httpserver) -> str:
    """`[ai]` as Connect leaves it: the oMLX app, its key."""
    return (f'\n[ai]\nprovider = "external"\nopenai_url = "{httpserver.url_for("/v1")}"\n'
            f'openai_key = "{KEY}"\n')


def step(page: Page) -> str:
    """The sub-step the page shows, by its part's class."""
    for part, sub in (("ai-found", "4a"), ("ai-get", "4a"), ("ai-connect", "4b"),
                      ("ai-elsewhere", "4b"), ("ai-models", "4c"), ("ai-bars", "4d"),
                      ("ai-can", "4e"), ("ai-not-asked", "not asked"),
                      ("ai-no-answer", "no answer"), ("setup-terminal", "terminal")):
        if f'class="{part}' in page.body or f'class="setup-box {part}' in page.body \
                or f'class="setup-note {part}' in page.body:
            return sub
    return ""


def starts_the_ai(page: Page) -> bool:
    """Whether the page offers the AI's run again, as the AI's tile starts
    it (`FsStack`), with the tile there to follow the run."""
    return (bool(re.search(r'<button class="[^"]*tile-add[^"]*"[^>]*data-target="ai"', page.body))
            and 'data-id="ai"' in page.body)


def ai_on_the_rail(page: Page) -> str:
    """The AI step's status on the rail: done, current or todo."""
    found = re.search(r'<li class="setup-rail-step" data-step="ai" data-status="(\w+)"', page.body)
    return found[1] if found else ""


def rows(page: Page) -> dict[str, str]:
    """4c's rows: each role's state."""
    return dict(re.findall(r'class="ai-row" data-role="(\w+)" data-state="(\w+)"', page.body))


def bars(page: Page) -> list[str]:
    """4d's bars, by role."""
    return re.findall(r'class="ai-bar" data-role="(\w+)"', page.body)


# ── 4a: choose ───────────────────────────────────────────────────────────

def test_the_app_running_on_this_mac_is_offered(mac):
    """Given the oMLX app answers on this Mac, when the admin opens the AI
    step, then it offers the app, and the terminal's route beside it."""
    mac(app=True)

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "4a" and 'class="setup-box ai-found"' in page.body
    hrefs = [href for _, href in page.links()]
    assert "/setup?step=ai&ai=connect" in hrefs
    assert "/setup?step=ai&ai=install" in hrefs


def test_without_the_app_the_step_says_where_to_get_it(mac):
    """Given nothing answers, then the step links the app's download and
    "I installed oMLX", and still offers the terminal's route."""
    mac(app=False)

    page = on_this_mac().opens("/setup?step=ai")

    assert 'class="setup-box ai-get"' in page.body
    hrefs = [href for _, href in page.links()]
    assert any(href.endswith(".dmg") for href in hrefs)
    assert "/setup?step=ai" in hrefs
    assert "/setup?step=ai&ai=install" in hrefs


# The tested release's version and disk images, as oMLX names them on its
# GitHub releases.
def tested() -> str:
    return ".".join(map(str, importlib.import_module("stacklets.ai.omlx_app").TESTED_OMLX))


def release_file(name: str) -> str:
    return f"https://github.com/jundot/omlx/releases/download/v{tested()}/{name}"


def download_link(page: Page) -> str:
    """Where the step's "Download oMLX" button points."""
    return dict(page.links("ai-get")).get("Download oMLX", "")


@pytest.mark.parametrize("macos, disk_image", [
    ("15.5", "oMLX-{v}-macos15-sequoia.dmg"),
    ("26.1", "oMLX-{v}-macos26-27.dmg"),
    ("27.0", "oMLX-{v}-macos26-27.dmg"),
    ("28.0", "oMLX-{v}-macos26-27.dmg"),
])
def test_the_download_is_the_tested_releases_disk_image_for_this_macos(mac, macos, disk_image):
    """Given nothing answers and the Mac runs `macos`, when the admin opens
    the AI step, then "Download oMLX" downloads the disk image of the oMLX
    release the stack is tested with, the one built for that macOS. The
    newest oMLX release can be a dev or rc build, so it is never the target.
    A macOS newer than the release knows gets its newest disk image, so an
    admin who updated macOS still gets a download."""
    mac(app=False, macos=macos)

    page = on_this_mac().opens("/setup?step=ai")

    assert download_link(page) == release_file(disk_image.format(v=tested()))
    assert 'ai-get-macos' not in page.body


@pytest.mark.parametrize("macos", ["14.7", ""])
def test_a_macos_without_a_disk_image_gets_the_release_page_and_what_omlx_needs(mac, macos):
    """Given a macOS the tested release has no disk image for, or one the
    host cannot tell, then the button opens that release's page on GitHub,
    and the step says which macOS oMLX needs."""
    mac(app=False, macos=macos)

    page = on_this_mac().opens("/setup?step=ai")

    assert download_link(page) == f"https://github.com/jundot/omlx/releases/tag/v{tested()}"
    assert page.texts("ai-get-macos")


def test_homebrew_is_found_where_it_installs_when_path_lacks_it(mac, tmp_path, monkeypatch):
    """The host API runs under launchd, whose PATH has no Homebrew. The
    step still finds oMLX from Homebrew, at Homebrew's own location."""
    mac(app=False)
    brew = tmp_path / "homebrew" / "bin" / "brew"
    brew.parent.mkdir(parents=True)
    brew.write_text('#!/bin/sh\n[ "$*" = "list --versions omlx" ] && echo "omlx 0.7.3"\n')
    brew.chmod(0o755)
    homebrew_omlx = importlib.import_module("stacklets.ai.homebrew_omlx")
    monkeypatch.setattr(homebrew_omlx, "LIST_COMMAND", ("brew", "list", "--versions", "omlx"))
    monkeypatch.setattr(homebrew_omlx, "BREW", brew)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    page = on_this_mac().opens("/setup?step=ai")

    assert 'class="setup-box ai-found ai-homebrew"' in page.body


def test_installing_omlx_instead_shows_the_terminal_command(mac):
    """The Homebrew route keeps its terminal box, and the AI's tile below
    it follows that run."""
    mac(app=True)

    page = on_this_mac().opens("/setup?step=ai&ai=install")

    assert step(page) == "terminal"
    assert page.texts("setup-terminal-command") == ["./stack up ai"]
    assert 'class="tile" data-id="ai"' in page.body


# ── "I installed oMLX": what the step checks ─────────────────────────────

def checked(page: Page) -> str:
    """The line in which the step says what it checked."""
    lines = page.texts("ai-checked")
    return lines[0] if lines else ""


def address_field(page: Page) -> str | None:
    """The value of the address field, None when the page has none."""
    found = re.search(r'<input[^>]*name="address"[^>]*>', page.body)
    if found is None:
        return None
    value = re.search(r'value="([^"]*)"', found.group(0))
    return value.group(1) if value else ""


def test_i_installed_omlx_checks_again(mac):
    """Given nothing answered when the step was drawn, then "I installed
    oMLX" leads to the step's check, not to the same page again."""
    mac(app=False)

    page = on_this_mac().opens("/setup?step=ai")

    assert ("I installed oMLX", "/setup?step=ai&ai=check") in page.links("ai-get")


def test_an_app_that_answers_without_a_key_is_connected_at_once(mac, httpserver):
    """Case 1: given the app answers at its default address without a key,
    when the admin presses "I installed oMLX", then the stack uses it."""
    m = mac(app=True, wants_key=False)

    status, answer = m.presses_installed()

    assert status == 200 and answer.get("ready") is True, answer
    assert m.toml()["ai"]["openai_url"] == httpserver.url_for("/v1")


def test_an_app_that_wants_a_key_gets_the_address_and_key_fields(mac, httpserver):
    """Case 3: given the app answers but wants a key, then the check says
    so, names where the key is, and shows the address (its address) and
    the key."""
    m = mac(app=True)

    status, answer = m.presses_installed()
    page = on_this_mac().opens("/setup?step=ai&ai=check")

    assert answer.get("ready") is not True
    assert "provider" not in m.toml().get("ai", {})
    assert "wants an API key" in checked(page) and "Settings" in checked(page)
    assert address_field(page) == httpserver.url_for("").rstrip("/")
    assert 'type="password" name="api_key"' in page.body

    status, _ = m.connects_as(address_field(page), KEY)

    assert status == 200
    assert step(on_this_mac().opens("/setup?step=ai")) == "4c"


def test_an_installed_app_that_does_not_answer_gets_the_fields(mac):
    """Case 2: given the app is installed and nothing answers at the
    default address, then the check says so, asks to open the app, and
    shows the address filled with the default and an empty key."""
    m = mac(app=False, installed=True)

    page = on_this_mac().opens("/setup?step=ai&ai=check")

    assert "installed" in checked(page) and "does not answer" in checked(page)
    assert "Open the oMLX app" in checked(page)
    default = importlib.import_module("stacklets.ai.running_server").RUNNING_SERVERS[0]
    assert address_field(page) == default.removesuffix("/v1")
    assert 'class="setup-box ai-get"' not in page.body
    assert "provider" not in m.toml().get("ai", {})


def test_an_app_on_another_port_connects_with_its_address_and_key(mac, httpserver):
    """The case that was reported: the app runs on another port (its
    settings file names one), so nothing answers at the default address.
    The check shows the fields; the address the app answers at and its key
    connect it."""
    m = mac(app=True, at_default=False)

    page = on_this_mac().opens("/setup?step=ai&ai=check")
    assert "does not answer" in checked(page) and address_field(page) is not None

    status, answer = m.connects_as(httpserver.url_for("").rstrip("/"), KEY)

    assert status == 200 and answer.get("openai_url") == httpserver.url_for("/v1"), answer
    ai = m.toml()["ai"]
    assert (ai["provider"], ai["openai_url"], ai["openai_key"]) == (
        "external", httpserver.url_for("/v1"), KEY)


def test_an_app_on_another_port_is_the_app_and_gets_the_models(mac, httpserver):
    """The reported case to its end: the app answers only on another port
    (42060 on the reported Mac) and wants its key. After Connect the stack
    knows it as the oMLX app, not as a server elsewhere, by what answers
    there: the step shows the models, not "ready", and Download fetches
    them onto that app."""
    m = mac(app=True, at_default=False)
    m.connects_as(httpserver.url_for("").rstrip("/"), KEY)

    page = on_this_mac().opens("/setup?step=ai")
    assert step(page) == "4c", step(page)
    assert rows(page) == {"chat": "to_download", "listen": "to_download", "speak": "to_download"}

    status, _ = on_this_mac().posts("/dashboard/api/ai/download", {"speech": False})

    assert status == 200 and m.app.requested == [CHAT]


def test_a_wrong_key_or_no_answer_says_which_and_stores_nothing(mac, httpserver):
    """Case 5: a key the server refuses and an address where nothing
    answers each come back with their reason, and `[ai]` stays empty."""
    m = mac(app=True, at_default=False)

    _, refused = m.connects_as(httpserver.url_for("").rstrip("/"), "not-the-key")
    _, silent = m.connects_as(f"http://127.0.0.1:{a_closed_port()}", KEY)

    assert (refused["reason"], silent["reason"]) == ("key", "unreachable")
    assert "provider" not in m.toml().get("ai", {})


def test_no_omlx_on_the_mac_keeps_the_download(mac):
    """Case 4: given oMLX is not installed, then the check says it found
    none and keeps the download."""
    mac(app=False)

    page = on_this_mac().opens("/setup?step=ai&ai=check")

    assert "found no oMLX" in checked(page)
    assert 'class="setup-box ai-get"' in page.body
    assert download_link(page) == release_file(f"oMLX-{tested()}-macos15-sequoia.dmg")
    assert address_field(page) is None


def test_the_check_in_german(mac):
    """The check's words in German, for the reported case."""
    mac(app=False, installed=True)

    page = on_this_mac("de").opens("/setup?step=ai&ai=check")

    assert "oMLX ist installiert, antwortet aber nicht" in checked(page)
    assert "Verbinden" in page.body


def test_an_app_that_answers_without_a_key_is_offered_its_fields(mac, httpserver):
    """The check's answers case: the app answers at its default address
    without a key and the stack does not use it yet. The check says it
    answers, without asking for a key, and fills the address."""
    mac(app=True, wants_key=False)

    page = on_this_mac().opens("/setup?step=ai&ai=check")

    assert "answers at" in checked(page) and "API key" not in checked(page)
    assert address_field(page) == httpserver.url_for("").rstrip("/")


def test_the_check_finds_the_app_on_the_stack_engine_address(mac, httpserver, monkeypatch):
    """The app reads its port from the settings file the stack's own
    engine shares, so it may answer at the engine's address. The check
    looks there too and fills the field with that address."""
    mac(app=True, at_default=False)
    monkeypatch.setattr(importlib.import_module("stacklets.ai.running_server"),
                        "STACK_ENGINE_ADDRESSES", (httpserver.url_for("/v1"),))

    page = on_this_mac().opens("/setup?step=ai&ai=check")

    assert "wants an API key" in checked(page)
    assert address_field(page) == httpserver.url_for("").rstrip("/")


def test_after_i_installed_omlx_the_check_comes_before_homebrew(mac, monkeypatch):
    """Given oMLX from Homebrew as well, when the admin pressed "I
    installed oMLX", then the step shows the check, not the terminal."""
    mac(app=False, installed=True)
    monkeypatch.setattr(importlib.import_module("stacklets.ai.homebrew_omlx"),
                        "LIST_COMMAND", ("true",))

    page = on_this_mac().opens("/setup?step=ai&ai=check")

    assert step(page) != "terminal"
    assert "does not answer" in checked(page)


def test_a_server_on_the_default_port_that_is_not_omlx_is_not_the_app(mac, httpserver,
                                                                      monkeypatch):
    """A server on the app's default port that has no oMLX admin API
    (vLLM, LM Studio) is not offered as the app, and "I installed oMLX"
    connects nothing."""
    m = mac(app=False)
    httpserver.expect_request("/v1/models").respond_with_json({"data": [{"id": "qwen3:8b"}]})
    monkeypatch.setattr(importlib.import_module("stacklets.ai.running_server"),
                        "RUNNING_SERVERS", (httpserver.url_for("/v1"),))

    page = on_this_mac().opens("/setup?step=ai")
    _, answer = m.presses_installed()

    assert 'class="setup-box ai-found"' not in page.body
    assert answer.get("ready") is not True
    assert "provider" not in m.toml().get("ai", {})


def test_a_server_behind_a_login_on_the_default_port_is_not_the_app(mac, httpserver,
                                                                    monkeypatch):
    """A server that refuses every request without a login also refuses
    oMLX's admin address, but its API description does not name oMLX."""
    mac(app=False)
    httpserver.expect_request("/openapi.json").respond_with_json(
        {"openapi": "3.1.0", "info": {"title": "Gateway", "version": "1.0"}})
    httpserver.expect_request(re.compile(r"/(v1|admin)/.*")).respond_with_json(
        {"detail": "Not authenticated"}, status=401)
    monkeypatch.setattr(importlib.import_module("stacklets.ai.running_server"),
                        "RUNNING_SERVERS", (httpserver.url_for("/v1"),))

    page = on_this_mac().opens("/setup?step=ai")

    assert 'class="setup-box ai-found"' not in page.body


def test_an_app_that_stopped_answering_does_not_finish_the_step(mac):
    """Given the stack uses the app and the app was closed, then the AI
    step is not done: nothing answers, so it cannot be told from a server
    that reads."""
    closed = f"http://127.0.0.1:{a_closed_port()}/v1"
    mac(app=False, installed=True,
        ai=f'\n[ai]\nprovider = "external"\nopenai_url = "{closed}"\nopenai_key = "{KEY}"\n')

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) != "4e"


def test_when_the_host_cannot_say_the_step_stays_open(mac, httpserver, monkeypatch):
    """Given the host API does not answer whether the server is the app,
    then the AI step is not done."""
    m = mac(app=True, ai=connected(httpserver))
    answer = m.host.answer
    monkeypatch.setattr(m.host, "answer", lambda request: (
        [{"error": "The stack did not answer."}] if request.get("cmd") == "ai_app"
        else answer(request)))

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) != "4e"


def test_once_the_ai_is_set_up_reading_it_probes_no_server(mac, httpserver):
    """Once the ai stacklet is set up, the setup page and the dashboard
    still read the AI on every read, for its kept answer, but that read
    asks the app nothing: whether it is the app decides nothing then."""
    m = mac(app=True, ai=connected(httpserver), state="done")
    (m.root / ".stack").mkdir(exist_ok=True)
    (m.root / ".stack" / "ai.setup-done").touch()
    httpserver.clear_log()

    # Another step than the AI's, whose own plan asks the app for its models.
    on_this_mac().opens("/setup?step=services")
    on_this_mac().opens("/dashboard")

    probed = [r.path for r, _ in httpserver.log if r.path in ("/openapi.json", "/admin/api/models")]
    assert probed == []


# ── 4b: connect ──────────────────────────────────────────────────────────

def test_on_this_mac_the_key_has_a_field(mac):
    mac(app=True)

    page = on_this_mac().opens("/setup?step=ai&ai=connect")

    assert step(page) == "4b"
    assert 'type="password" name="api_key"' in page.body


def test_the_key_sent_from_another_device_is_refused(mac):
    """Given a key posted from a phone, then nothing is stored and the
    answer says where to connect it: a key typed on a phone would cross the
    Wi-Fi in the clear."""
    m = mac(app=True)

    status, answer = m.connects(browser=phone())

    assert status == 403 and answer["mac"] == "http://127.0.0.1:42002/setup"
    assert "provider" not in m.toml().get("ai", {})


def test_connecting_with_the_key_stores_the_app_and_shows_what_the_ai_gets(mac, httpserver):
    """Given the app's key on this Mac, when Connect posts it, then the
    stack uses the app with that key, and the step shows 4c."""
    m = mac(app=True)

    status, answer = m.connects()

    assert status == 200 and answer.get("ready") is True, answer
    ai = m.toml()["ai"]
    assert (ai["provider"], ai["openai_url"], ai["openai_key"]) == (
        "external", httpserver.url_for("/v1"), KEY)
    assert step(on_this_mac().opens("/setup?step=ai")) == "4c"


def test_a_wrong_key_stores_nothing(mac):
    m = mac(app=True)

    status, _ = m.connects(key="not-the-key")

    assert status >= 400
    assert "provider" not in m.toml().get("ai", {})
    assert step(on_this_mac().opens("/setup?step=ai")) == "4a"


# ── 4c: what your AI gets ────────────────────────────────────────────────

def test_a_small_mac_gets_the_chat_model_with_speech_off(mac, httpserver):
    """The three rows, each still to download, and the speech switch off
    on a 16 GB Mac."""
    mac(app=True, ai=connected(httpserver))

    page = on_this_mac().opens("/setup?step=ai")

    assert rows(page) == {"chat": "to_download", "listen": "to_download", "speak": "to_download"}
    assert re.search(r'<input type="checkbox" role="switch" name="speech">', page.body)
    assert 'class="setup-note ai-speech-note"' in page.body


def test_a_model_already_on_the_mac_is_not_downloaded_again(mac, httpserver):
    m = mac(app=True, downloaded=["Kokoro-82M-bf16"], ai=connected(httpserver))

    assert rows(on_this_mac().opens("/setup?step=ai"))["speak"] == "on_mac"

    on_this_mac().posts("/dashboard/api/ai/download", {"speech": True})

    assert m.app.requested == [CHAT, WHISPER]


# ── 4d: downloading ──────────────────────────────────────────────────────

def test_download_records_the_choice_and_starts_the_chat_model(mac, httpserver):
    """Given speech left off, when the admin downloads, then only the chat
    model is fetched, and `stack.toml` says speech off and that model."""
    m = mac(app=True, ai=connected(httpserver))

    status, plan = on_this_mac().posts("/dashboard/api/ai/download", {"speech": False})

    assert status == 200
    assert m.app.requested == [CHAT]
    assert m.toml()["ai"]["speech"] is False
    assert m.toml()["ai"]["default"] == CHAT
    assert [r["state"] for r in plan["models"]] == ["downloading", "to_download", "to_download"]


def test_a_bar_per_model_and_coming_back_shows_where_it_is(mac, httpserver):
    """The downloads are oMLX's own, so a page opened later shows their bars."""
    mac(app=True, ai=connected(httpserver))
    on_this_mac().posts("/dashboard/api/ai/download", {"speech": True})

    later = on_this_mac().opens("/setup?step=ai")

    assert step(later) == "4d"
    assert bars(later) == ["chat", "listen", "speak"]
    assert re.search(r'<progress max="\d+" value="\d+">', later.body)


def test_the_bars_read_the_plan_until_the_downloads_end(mac, httpserver):
    """`/dashboard/api/ai` is what the bars read; once oMLX has finished,
    every row is on the Mac and the step offers to go on."""
    m = mac(app=True, ai=connected(httpserver))
    on_this_mac().posts("/dashboard/api/ai/download", {"speech": False})
    m.app.finish()

    status, plan = on_this_mac().reads("/dashboard/api/ai")

    assert status == 200 and plan["models"][0]["state"] == "on_mac"
    assert step(on_this_mac().opens("/setup?step=ai")) == "4c"


def test_download_before_connecting_is_refused(mac):
    m = mac(app=True)

    status, _ = on_this_mac().posts("/dashboard/api/ai/download", {"speech": False})

    assert status == 503
    assert m.app.requested == []
    assert "speech" not in m.toml().get("ai", {})


def test_another_device_cannot_start_the_downloads(mac, httpserver):
    """Given the app is connected, when a phone posts to the download
    route, then it is refused: `stack.toml` keeps its `[ai]` and no model
    is requested. Only the Mac that runs famstack changes the server."""
    m = mac(app=True, ai=connected(httpserver))

    status, problem = phone().posts("/dashboard/api/ai/download", {"speech": True})

    assert status == 403
    assert problem["error"]
    assert m.app.requested == []
    assert "speech" not in m.toml()["ai"]


def test_continue_with_every_model_on_the_app_finishes_the_step(mac, httpserver):
    """A Mac set up before: the ai stacklet is set up from an earlier
    install, the stack uses the app on its default port with its key, and
    every model is on the app. Continue records the choice, and the AI's
    start, which follows on the page, asks it; the step is then done and
    shows that the AI is ready, not the models again."""
    m = mac(app=True, downloaded=["Qwen3.5-9B-MLX-4bit"], ai=connected(httpserver))
    (m.root / ".stack").mkdir(exist_ok=True)
    (m.root / ".stack" / "ai.setup-done").touch()

    status, plan = on_this_mac().posts("/dashboard/api/ai/download", {"speech": False})
    assert status == 200 and plan["models"][0]["state"] == "on_mac"

    m.host.up("ai")
    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "4e", step(page)


# ── 4e: ready ────────────────────────────────────────────────────────────

def test_an_ai_that_runs_says_what_it_can_do(mac, httpserver):
    """Given the AI runs with speech on, then 4e lists reading, hearing
    and speaking, each done."""
    mac(app=True, ai=connected(httpserver) + "speech = true\n", state="done").host.up("ai")

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "4e"
    assert re.findall(r'<li data-status="(\w+)">', page.body) == ["done", "done", "done"]


def test_with_speech_off_voice_messages_are_said_to_be_off(mac, httpserver):
    mac(app=True, ai=connected(httpserver) + "speech = false\n", state="done").host.up("ai")

    page = on_this_mac().opens("/setup?step=ai")

    assert re.findall(r'<li data-status="(\w+)">', page.body) == ["done", "off"]



def test_an_ai_server_the_stack_does_not_manage_is_ready_without_a_terminal(mac, httpserver):
    """Given the stack uses an AI server it does not manage and the AI is
    ready, then 4e shows no AI tile and no `stack up ai`."""
    mac(app=True, ai=connected(httpserver), state="done").host.up("ai")

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "4e"
    assert 'data-id="ai"' not in page.body
    assert "stack up ai" not in page.body

def test_an_ai_server_that_does_not_answer_is_not_ready(mac, httpserver):
    """Given the stack was connected to the oMLX app and the app is gone
    (nothing answers at its address), then the AI step does not say the AI
    is ready: it offers the app to get, as before the first connect,
    also when the AI answered before it was gone."""
    gone = mac(app=False, installed=False, ai=connected(httpserver), state="done")
    the_ai_answered(gone.host)
    gone.host.silent.add(httpserver.url_for("/v1"))

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "4a", page.text
    assert 'class="setup-box ai-get' in page.body

def test_an_installed_app_that_does_not_answer_is_opened_and_checked_again(mac):
    """Given oMLX is installed on this Mac and nothing answers, as right
    after a first install until macOS's first-open dialog is confirmed,
    then the step says to open the app and confirm that dialog, then to
    check again, and offers no download."""
    mac(app=False, installed=True, state="services")

    page = on_this_mac("de").opens("/setup?step=ai")

    assert 'class="setup-box ai-get' not in page.body
    assert "Öffne die oMLX-App" in page.text and "macOS" in page.text
    assert "Ich habe oMLX installiert" in page.text

# ── Does the AI answer ───────────────────────────────────────────────────
#
# The AI's run ends with one tiny question to the AI; its answer is kept in
# the instance, by the address, the model and the key it was asked with.
# `m.host.up("ai")` is the run as the host stand-in plays it.

def test_the_ais_run_asks_it_and_the_step_says_ready(mac, httpserver):
    """Given the AI's run asked the app one short question and it
    answered, then the AI step says the AI is ready."""
    m = mac(app=True, ai=connected(httpserver), state="done")

    m.host.up("ai")
    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "4e", page.text
    assert len(m.app.completed) == 1


def test_a_finished_setup_stays_done_when_core_restarts(mac, httpserver):
    """Given the AI answered at the end of its run, when core restarts,
    then the AI step is still done: the answer is kept in the instance,
    not in core."""
    m = mac(app=True, ai=connected(httpserver), state="done")
    m.host.up("ai")

    core_restarts()
    page = on_this_mac().opens("/setup")

    assert ai_on_the_rail(page) == "done", page.text
    assert step(on_this_mac().opens("/setup?step=ai")) == "4e"


def test_an_ai_that_lists_its_models_but_cannot_answer_is_not_ready(mac, httpserver):
    """Given the oMLX app lists its models and fails every completion, as
    a build for a newer macOS does, when the AI's run has asked it, then
    the step does not say ready: it says the AI does not answer, shows
    the app's own error in one line, and offers the oMLX download for
    this Mac's macOS, the AI's run again and another AI server. The AI
    step is not done."""
    m = mac(app=True, ai=connected(httpserver), state="done", computes=False)

    m.host.up("ai")
    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "no answer", page.text
    assert "Your AI is ready" not in page.text
    assert page.texts("ai-error") == [f"HTTP 500: {BROKEN}"]
    links = dict(page.links("ai-no-answer"))
    assert links["Download oMLX"].endswith("-macos15-sequoia.dmg"), links
    assert any(href == "/setup?step=ai&ai=other" for href in links.values()), links
    assert starts_the_ai(page)
    assert ai_on_the_rail(page) != "done"


def test_a_server_that_hangs_ends_the_run_with_a_timeout_and_the_links(mac, httpserver,
                                                                       monkeypatch):
    """Given the AI takes longer than the run waits for its answer, then
    the run ends and the step says the AI did not answer in time, with
    the AI's run again and another AI server: no page that asks for ever,
    and no advice about an oMLX build."""
    monkeypatch.setattr("stack.ai.probe.ANSWER_SECONDS", 0.2)
    with hanging() as server:
        ai = connected(httpserver).replace(httpserver.url_for("/v1"), server.url_for("/v1"))
        m = mac(app=True, ai=ai, state="done")

        m.host.up("ai")
    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "no answer", page.text
    assert "in time" in page.text
    assert "data-watch" not in page.body
    assert starts_the_ai(page)
    assert any(href == "/setup?step=ai&ai=other" for _, href in page.links("ai-no-answer"))
    assert "another macOS" not in page.text


@pytest.mark.parametrize("change", ['default = "mlx-community/Llama-3.2-3B-Instruct-4bit"\n',
                                    'openai_key = "a-new-key"\n'])
def test_a_new_model_or_key_at_the_same_address_is_not_checked_yet(mac, httpserver, change):
    """Given the AI answered, when `[ai]` names another model or another
    key at the same address, then that answer does not count: the AI
    step is not done and offers the AI's run, which asks again."""
    m = mac(app=True, ai=connected(httpserver), state="done")
    m.host.up("ai")
    toml = m.root / "stack.toml"
    text = toml.read_text()
    if change.startswith("openai_key"):
        text = re.sub(r'openai_key = "[^"]*"\n', "", text)
    toml.write_text(text + change)

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "not asked", page.text
    assert starts_the_ai(page)
    assert ai_on_the_rail(page) != "done"


def test_an_ai_nobody_asked_is_not_done(mac, httpserver):
    """Given the AI runs and lists its models and nobody asked it a
    question (an install from before this check), then the AI step is
    not done and offers the AI's run, and the first letter's step says
    the AI is not ready instead of waiting for a letter."""
    m = mac(app=True, ai=connected(httpserver), state="done", computes=False)

    page = on_this_mac().opens("/setup?step=ai")
    letter = on_this_mac().opens("/setup?step=try")

    assert step(page) == "not asked", page.text
    assert starts_the_ai(page)
    assert ai_on_the_rail(page) != "done"
    assert "data-watch" not in letter.body
    assert "Your AI is not running yet" in letter.text
    assert m.app.completed == []


@pytest.mark.parametrize("status, said, says", [
    (404, "Model 'Qwen3.5-9B-MLX-4bit' not found", "does not have the model"),
    (401, "Invalid API key", "refused famstack's API key"),
])
def test_a_missing_model_or_a_refused_key_says_what_it_is(mac, httpserver, status, said, says):
    """Given the AI fails the question with a missing model or a refused
    key, then the step says that, with the server's own error, and gives
    no advice about an oMLX build for another macOS."""
    m = mac(app=True, ai=connected(httpserver), state="done")
    httpserver.clear_all_handlers()
    httpserver.expect_request("/v1/chat/completions").respond_with_json({"detail": said},
                                                                       status=status)
    m.host.up("ai")

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "no answer", page.text
    assert says in page.text
    assert page.texts("ai-error") == [f"HTTP {status}: {said}"]
    assert "another macOS" not in page.text


def test_the_letter_step_waits_while_the_ai_does_not_answer(mac, httpserver):
    """Given the AI does not answer, then the first letter's step does not
    wait for a letter: it says the AI is not ready and links the AI step,
    because a letter filed now would get no tags."""
    m = mac(app=True, ai=connected(httpserver), state="done", computes=False)
    m.host.up("ai")

    page = on_this_mac().opens("/setup?step=try")

    assert "data-watch" not in page.body
    assert "Your AI is not running yet" in page.text


def test_the_answer_in_german(mac, httpserver):
    """The German words of a failed answer: no line starts with "Sie"."""
    m = mac(app=True, ai=connected(httpserver), state="done", computes=False)
    m.host.up("ai")

    page = on_this_mac("de").opens("/setup?step=ai")

    box = " ".join(page.texts("ai-no-answer"))
    assert "Eure KI antwortet nicht" in page.text
    assert "Noch einmal prüfen" in box
    assert not re.search(r"(^|[.:!?]\s)Sie\b", box), box


# ── The terminal ─────────────────────────────────────────────────────────

def test_the_command_downloads_and_waits(mac, httpserver):
    """`stack ai recommended download` without `--no-wait` returns when
    oMLX has finished: every needed model is on the Mac."""
    m = mac(app=True, holds=False, ai=connected(httpserver))

    plan = m.host._stack().run_cli_command("ai", "recommended", ["download", "--speech", "on"])

    assert [r["state"] for r in plan["models"]] == ["on_mac", "on_mac", "on_mac"]
    assert m.app.requested == [CHAT, WHISPER, KOKORO]
