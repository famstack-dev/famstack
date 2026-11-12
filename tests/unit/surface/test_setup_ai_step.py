"""The AI step of /setup: the AI from the oMLX app, as the admin meets it.

Step 5 finds the oMLX app on this Mac and offers it, or says where to get
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

from tests.unit.stacklets.omlx_stand_in import KEY, OMLX

from .conftest import a_closed_port
from .dsl import Page
from .setup_stand_in import on_this_mac, phone, stack_in_state

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
    wanting its key; without, nothing answers there."""
    running_server = importlib.import_module("stacklets.ai.running_server")

    def enter(*, app: bool = True, downloaded=(), holds: bool = True, ai: str = "",
              state: str = "services", macos: str = "15.5") -> Mac:
        root = tmp_path / "instance"
        _write_toml(root, ai)
        host = stack_in_state(tmp_path, monkeypatch, httpserver)(state)
        omlx = OMLX(httpserver, downloaded=downloaded, wants_key=True, holds=holds) if app else None
        where = httpserver.url_for("/v1") if app else f"http://127.0.0.1:{a_closed_port()}/v1"
        monkeypatch.setattr(running_server, "RUNNING_SERVERS", (where,))
        # The macOS the host reports, so the step's download does not
        # depend on the Mac the tests run on.
        monkeypatch.setattr(importlib.import_module("stacklets.ai.omlx_app"), "this_macos",
                            lambda: macos)
        # The setup page knows the app by the same address (`setup_page.OMLX_APP`).
        monkeypatch.setattr(importlib.import_module("setup_page"), "OMLX_APP", where)
        host.configure = lambda answers: host._stack().configure("ai", answers)
        return Mac(host, omlx, root)
    return enter


def connected(httpserver) -> str:
    """`[ai]` as Connect leaves it: the oMLX app, its key."""
    return (f'\n[ai]\nprovider = "external"\nopenai_url = "{httpserver.url_for("/v1")}"\n'
            f'openai_key = "{KEY}"\n')


def step(page: Page) -> str:
    """The sub-step the page shows, by its part's class."""
    for part, sub in (("ai-found", "5a"), ("ai-get", "5a"), ("ai-connect", "5b"),
                      ("ai-elsewhere", "5b"), ("ai-models", "5c"), ("ai-bars", "5d"),
                      ("ai-can", "5e"), ("setup-terminal", "terminal")):
        if f'class="{part}' in page.body or f'class="setup-box {part}' in page.body \
                or f'class="setup-note {part}' in page.body:
            return sub
    return ""


def rows(page: Page) -> dict[str, str]:
    """5c's rows: each role's state."""
    return dict(re.findall(r'class="ai-row" data-role="(\w+)" data-state="(\w+)"', page.body))


def bars(page: Page) -> list[str]:
    """5d's bars, by role."""
    return re.findall(r'class="ai-bar" data-role="(\w+)"', page.body)


# ── 5a: choose ───────────────────────────────────────────────────────────

def test_the_app_running_on_this_mac_is_offered(mac):
    """Given the oMLX app answers on this Mac, when the admin opens the AI
    step, then it offers the app, and the terminal's route beside it."""
    mac(app=True)

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "5a" and 'class="setup-box ai-found"' in page.body
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
])
def test_the_download_is_the_tested_releases_disk_image_for_this_macos(mac, macos, disk_image):
    """Given nothing answers and the Mac runs `macos`, when the admin opens
    the AI step, then "Download oMLX" downloads the disk image of the oMLX
    release the stack is tested with, the one built for that macOS. The
    newest oMLX release can be a dev or rc build, so it is never the target."""
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


def test_installing_omlx_instead_shows_the_terminal_command(mac):
    """The Homebrew route keeps its terminal box, and the AI's tile below
    it follows that run."""
    mac(app=True)

    page = on_this_mac().opens("/setup?step=ai&ai=install")

    assert step(page) == "terminal"
    assert page.texts("setup-terminal-command") == ["./stack up ai"]
    assert 'class="tile" data-id="ai"' in page.body


# ── 5b: connect ──────────────────────────────────────────────────────────

def test_on_this_mac_the_key_has_a_field(mac):
    mac(app=True)

    page = on_this_mac().opens("/setup?step=ai&ai=connect")

    assert step(page) == "5b"
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
    stack uses the app with that key, and the step shows 5c."""
    m = mac(app=True)

    status, answer = m.connects()

    assert status == 200 and answer.get("ready") is True, answer
    ai = m.toml()["ai"]
    assert (ai["provider"], ai["openai_url"], ai["openai_key"]) == (
        "external", httpserver.url_for("/v1"), KEY)
    assert step(on_this_mac().opens("/setup?step=ai")) == "5c"


def test_a_wrong_key_stores_nothing(mac):
    m = mac(app=True)

    status, _ = m.connects(key="not-the-key")

    assert status >= 400
    assert "provider" not in m.toml().get("ai", {})
    assert step(on_this_mac().opens("/setup?step=ai")) == "5a"


# ── 5c: what your AI gets ────────────────────────────────────────────────

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


# ── 5d: downloading ──────────────────────────────────────────────────────

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

    assert step(later) == "5d"
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
    assert step(on_this_mac().opens("/setup?step=ai")) == "5c"


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


# ── 5e: ready ────────────────────────────────────────────────────────────

def test_an_ai_that_runs_says_what_it_can_do(mac, httpserver):
    """Given the AI runs with speech on, then 5e lists reading, hearing
    and speaking, each done."""
    mac(app=True, ai=connected(httpserver) + "speech = true\n", state="done")

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "5e"
    assert re.findall(r'<li data-status="(\w+)">', page.body) == ["done", "done", "done"]


def test_with_speech_off_voice_messages_are_said_to_be_off(mac, httpserver):
    mac(app=True, ai=connected(httpserver) + "speech = false\n", state="done")

    page = on_this_mac().opens("/setup?step=ai")

    assert re.findall(r'<li data-status="(\w+)">', page.body) == ["done", "off"]



def test_an_ai_server_the_stack_does_not_manage_is_ready_without_a_terminal(mac, httpserver):
    """Given the stack uses an AI server it does not manage and the AI is
    ready, then 5e shows no AI tile and no `stack up ai`."""
    mac(app=True, ai=connected(httpserver), state="done")

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "5e"
    assert 'data-id="ai"' not in page.body
    assert "stack up ai" not in page.body

# ── The terminal ─────────────────────────────────────────────────────────

def test_the_command_downloads_and_waits(mac, httpserver):
    """`stack ai recommended download` without `--no-wait` returns when
    oMLX has finished: every needed model is on the Mac."""
    m = mac(app=True, holds=False, ai=connected(httpserver))

    plan = m.host._stack().run_cli_command("ai", "recommended", ["download", "--speech", "on"])

    assert [r["state"] for r in plan["models"]] == ["on_mac", "on_mac", "on_mac"]
    assert m.app.requested == [CHAT, WHISPER, KOKORO]
