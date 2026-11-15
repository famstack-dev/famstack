"""Route 1 of the AI step, from the page: famstack installs oMLX, no terminal.

The admin picks "famstack installs oMLX" on step 4 of /setup. The page
writes `[ai]` for the oMLX the stack runs (`stack ai switch managed
--no-start`, the host API's `ai_managed`) and starts the AI's run as the
AI's tile starts it (`FsStack`, a job of `up ai`). That run prepares
(Homebrew's oMLX, the models) holding only the AI's run lock, so the
admin goes on with the other steps meanwhile. The step shows the job's
steps with the download in percent; a failed step shows its reason and
"Try again", which starts the run again where it stopped.

Driven through the tools server's FastAPI app as a browser opens it, read
as served, without JavaScript. The host API is the dashboard's stand-in
(`tests/dashboard_host.py`) in its "services" state; its `ai_managed` runs
`stack ai switch managed --no-start` for real on the stand-in's instance.
A job the page follows is put in the tools server's jobs as the host
API's run would feed it, so the test sets where the run is.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import tomllib
from pathlib import Path

import pytest

from .conftest import StackCLI
from .dsl import Page
from .setup_stand_in import on_this_mac, phone, stack_in_state, the_ai_answered

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

MANAGED = '\n[ai]\nprovider = "managed"\nopenai_url = "http://localhost:42060/v1"\n'
NO_AI = '\n[ai]\nprovider = "none"\n'
DOWNLOAD = "Downloading the language model"
NO_ROOM = "Not enough free space: the model needs 6.2 GB"


# ── The instance ─────────────────────────────────────────────────────────

class Mac:
    """The Simpsons' Mac at the AI step: the host API's stand-in and the
    instance's `stack.toml`."""

    def __init__(self, host, root):
        self.host, self.root = host, root

    def ai(self) -> dict:
        return tomllib.loads((self.root / "stack.toml").read_text()).get("ai", {})

    def omlx_settings(self) -> Path:
        """famstack's oMLX's settings file: the owner of its key."""
        data = tomllib.loads((self.root / "stack.toml").read_text())["core"]["data_dir"]
        return Path(data) / "ai" / "omlx" / "settings.json"

    def omlx_holds(self, key: str) -> None:
        """oMLX's settings file holds `key`, as an install left it."""
        self.omlx_settings().parent.mkdir(parents=True, exist_ok=True)
        self.omlx_settings().write_text(json.dumps({"auth": {"api_key": key}}))

    def omlx_key(self) -> str:
        return json.loads(self.omlx_settings().read_text())["auth"]["api_key"]

    def asked(self, cmd: str) -> list[dict]:
        return [r for r in self.host.asked if r.get("cmd") == cmd]


@pytest.fixture
def mac(tmp_path, monkeypatch, httpserver):
    """A function that sets the Mac up with this `[ai]`: `mac(ai=MANAGED)`.
    Its home directory is the test's: the switch reads oMLX's settings there."""
    def enter(*, ai: str = "", state: str = "services") -> Mac:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        host = stack_in_state(tmp_path, monkeypatch, httpserver)(state)
        root = tmp_path / "instance"
        if ai:
            with (root / "stack.toml").open("a") as toml:
                toml.write(ai)
        return Mac(host, root)
    return enter


@pytest.fixture
def brew_on_path(tmp_path, monkeypatch):
    """A Mac with Homebrew: an empty `brew` first on PATH, which route 1
    looks for before it writes anything."""
    bin_dir = tmp_path / "brew-bin"
    bin_dir.mkdir()
    (bin_dir / "brew").write_text("#!/bin/sh\n")
    (bin_dir / "brew").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")


@pytest.fixture
def jobs(monkeypatch):
    """The tools server's jobs, fresh, as the host API's runs feed them."""
    import live_routes
    from stack.live.hub import Hub
    from stack.live.jobs import Jobs

    hub = Hub()
    monkeypatch.setattr(live_routes, "hub", hub)
    monkeypatch.setattr(live_routes, "jobs", Jobs(hub))
    return live_routes.jobs


class AiRun:
    """The AI's run as the page follows it: preparing, with the download
    at `percent`, until `end` sends its result."""

    def __init__(self, jobs, percent: int, key: str = ""):
        from stack.live.envelope import Event

        self._go_on = threading.Event()
        self._result: dict = {"ok": True, "stacklet": "ai", "healthy": True}

        def run():
            yield Event.now(1, "step.started", {"text": DOWNLOAD, **({"key": key} if key else {})})
            yield Event.now(2, "step.progress", {"started": 1, "percent": percent})
            self._go_on.wait(10)
            failed = "error" in self._result
            yield Event.now(3, "step.failed" if failed else "step.done",
                            {"started": 1, "text": DOWNLOAD})
            yield Event.now(4, "result", self._result)

        self.job = jobs.start("up", "ai", run)
        _until(lambda: any(s.get("percent") == percent for s in self.job.snapshot()["steps"]))

    def end(self, result: dict | None = None) -> None:
        if result:
            self._result = result
        self._go_on.set()
        _until(lambda: self.job.state != "running")


def _until(done, within: float = 5) -> None:
    import time
    deadline = time.monotonic() + within
    while not done():
        assert time.monotonic() < deadline, "the job did not get there"
        time.sleep(0.01)


@contextlib.contextmanager
def ai_lock_held_elsewhere(root):
    """The AI's run lock, held by another process as the AI's run holds it
    while it prepares; let go when the block ends."""
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_THE_AI_LOCK, str(root)], cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO / "lib")},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        yield
    finally:
        holder.stdin.close()
        holder.wait(5)


HOLD_THE_AI_LOCK = """
import sys
from pathlib import Path
from stack import runs
with runs.claim(Path(sys.argv[1]), ["ai"]):
    print("held", flush=True)
    sys.stdin.read()
"""
REPO = Path(__file__).resolve().parents[3]


def ai_tile(page: Page) -> str:
    found = re.search(r'<article class="tile" data-id="ai".*?</article>', page.body, re.S)
    return found[0] if found else ""


def installs(page: Page) -> bool:
    """Whether the step offers route 1's button, which writes `[ai]` and
    starts the AI's run."""
    return bool(re.search(r'<button class="[^"]*\bai-install\b', page.body))


def starts_the_ai(page: Page) -> bool:
    """Whether the AI's tile offers its start (`FsStack`, `tile-add`)."""
    return bool(re.search(r'<button class="tile-add"[^>]*data-target="ai"', page.body))


def percents(page: Page) -> list[str]:
    """The percent of each step of the AI's tile that reported one."""
    return re.findall(r'data-percent="(\d+)"', ai_tile(page))


# ── Picking route 1 ──────────────────────────────────────────────────────

def test_route_one_offers_the_install_and_no_terminal_command(mac):
    """Given no `[ai]`, when the admin picks famstack's oMLX, then the step
    offers its install on the page, the AI's tile to follow it, and route 2
    as the way out; no command for the terminal."""
    mac()

    page = on_this_mac().opens("/setup?step=ai&ai=install")

    assert installs(page), page.text
    assert 'data-id="ai"' in page.body
    assert page.texts("setup-terminal-command") == []
    assert "/setup?step=ai&ai=other" in [href for _, href in page.links()]


def answers_the_form(m, monkeypatch) -> None:
    """The host API's `configure` runs the real `stack configure ai --json
    --answers -` on the instance, in this process, as on the Mac."""
    cli = StackCLI(m.root, monkeypatch)
    m.host.configure = lambda answers: cli("configure", "ai", "--json", "--answers", "-",
                                           stdin=json.dumps(answers)).answer


def test_a_first_run_answers_the_route_form_from_the_page(mac):
    """Given no `[ai]`, then route 1's install and route 2's form answer the
    ai stacklet's route form (`data-form`), as the installer does; a route
    change later writes through the switch."""
    mac()

    install = on_this_mac().opens("/setup?step=ai&ai=install")
    other = on_this_mac().opens("/setup?step=ai&ai=other")

    assert re.search(r'class="[^"]*ai-install[^"]*"[^>]*data-form="ai"', install.body), install.body
    assert re.search(r'<form class="[^"]*ai-other[^"]*"[^>]*data-form="ai"', other.body), other.body


def test_a_route_change_writes_through_the_switch(mac):
    """Given the family runs without an AI, then the install writes through
    the switch: the route form is not asked once `[ai]` names a route."""
    mac(ai=NO_AI)

    page = on_this_mac().opens("/setup?step=ai&ai=install")

    assert installs(page), page.text
    assert 'data-form="ai"' not in page.body


def test_route_one_from_the_form_writes_famstacks_omlx(mac, monkeypatch, brew_on_path):
    m = mac()
    answers_the_form(m, monkeypatch)

    status, answer = on_this_mac().posts("/dashboard/api/configure/ai",
                                         {"answers": {"route": "managed"}})

    assert (status, answer) == (200, {"ready": True})
    assert (m.ai()["provider"], m.ai()["openai_url"]) == ("managed", "http://localhost:42060/v1")
    assert [r for r in m.host.asked if r.get("cmd") == "up"] == []


@pytest.mark.parametrize("path, body", [
    ("/dashboard/api/configure/ai", {"answers": {"route": "managed"}}),
    ("/dashboard/api/ai/managed", {})])
def test_while_the_omlx_app_runs_the_install_is_refused_with_the_reason(
        mac, monkeypatch, brew_on_path, path, body):
    """Given the oMLX app runs, when the admin installs famstack's oMLX, on
    a first run or as a change, then the page gets the reason to quit the
    app, and `[ai]` names no route: two MLX servers on one graphics chip
    can crash the Mac."""
    from stacklets.ai import omlx_app
    monkeypatch.setattr(omlx_app, "PROCESS_COMMAND", ("true",))
    m = mac()
    answers_the_form(m, monkeypatch)

    status, answer = on_this_mac().posts(path, body)

    assert status == 503, answer
    assert "Quit the oMLX app first" in answer["error"]
    assert answer["reason"] == "omlx_app"
    assert "provider" not in m.ai()


def test_route_two_from_the_form_offers_the_servers_models_then_writes_the_pick(
        mac, monkeypatch, httpserver):
    """Given a server that lists two models, when the admin connects it on
    a first run, then the answer is the form to pick one; the answers with
    the pick write it."""
    httpserver.expect_request("/v1/models").respond_with_json(
        {"data": [{"id": "llama-3-8b"}, {"id": "mistral-7b"}]})
    httpserver.expect_request("/v1/chat/completions", method="POST").respond_with_json(
        {"choices": [{"message": {"role": "assistant", "content": "OK"}}]})
    m = mac()
    answers_the_form(m, monkeypatch)
    route = {"route": "external", "address": httpserver.url_for("/v1"), "api_key": ""}

    _, picking = on_this_mac().posts("/dashboard/api/configure/ai", {"answers": route})
    status, picked = on_this_mac().posts("/dashboard/api/configure/ai",
                                         {"answers": {**route, "model": "mistral-7b"}})

    assert picking["form"]["id"] == "ai.model", picking
    assert (status, picked) == (200, {"ready": True})
    assert (m.ai()["provider"], m.ai()["default"]) == ("external", "mistral-7b")


def test_route_two_from_the_form_says_why_a_server_is_refused(mac, monkeypatch):
    """Given nothing answers at the address, then the page gets the reason
    it names in its own words, and nothing is written."""
    from .conftest import a_closed_port
    m = mac()
    answers_the_form(m, monkeypatch)
    route = {"route": "external", "address": f"http://127.0.0.1:{a_closed_port()}/v1",
             "api_key": ""}

    status, answer = on_this_mac().posts("/dashboard/api/configure/ai", {"answers": route})

    assert status == 503, answer
    assert answer["reason"] == "unreachable"
    assert "provider" not in m.ai()


def test_the_install_writes_famstacks_omlx_and_starts_nothing_itself(mac):
    """When the admin presses the install, then `[ai]` names famstack's
    oMLX, and nothing is started by the write: the page then starts the
    AI's run as its tile does, which prepares outside the stack's queue."""
    m = mac()

    status, answer = on_this_mac().posts("/dashboard/api/ai/managed", {})

    assert status == 200, answer
    assert (m.ai()["provider"], m.ai()["openai_url"]) == ("managed", "http://localhost:42060/v1")
    assert [r for r in m.host.asked if r.get("cmd") == "up"] == []


@pytest.mark.parametrize("ai", [NO_AI, '\n[ai]\nprovider = "external"\n'
                                       'openai_url = "http://192.168.1.30:11434/v1"\n'])
def test_the_install_replaces_no_ai_or_a_server_elsewhere(mac, ai):
    """Given the family ran without an AI, or with a server elsewhere, when
    the admin picks famstack's oMLX instead, then `[ai]` names it."""
    m = mac(ai=ai)

    status, answer = on_this_mac().posts("/dashboard/api/ai/managed", {})

    assert status == 200, answer
    assert m.ai()["provider"] == "managed"


REMOTE_KEY = "sk-remote-server-key"
OWN_KEY = "7fk2-pq9w-m3xd-h8ra"


EXTERNAL = ('\n[ai]\nprovider = "external"\nopenai_url = "http://192.168.1.30:11434/v1"\n'
            f'openai_key = "{REMOTE_KEY}"\n')


@pytest.mark.parametrize("ai", ["", NO_AI, EXTERNAL])
def test_the_install_gives_ai_the_key_omlx_holds(mac, ai):
    """Given no AI, no route chosen or a server elsewhere with its own key,
    when the admin picks famstack's oMLX, then oMLX's settings file holds a
    new key and `[ai]` the same one at once: every stacklet started while
    oMLX installs renders it, never `local`, never the other server's key."""
    m = mac(ai=ai)

    status, answer = on_this_mac().posts("/dashboard/api/ai/managed", {})

    assert status == 200, answer
    assert m.ai()["openai_key"] == m.omlx_key()
    assert m.omlx_key() not in ("", "local", REMOTE_KEY)


def test_a_placeholder_omlx_holds_is_replaced_in_both_places(mac):
    """Given oMLX's settings file holds the placeholder `local` (an oMLX
    started before it had a key), when the admin picks famstack's oMLX,
    then one new key goes into the settings file and into `[ai]`."""
    m = mac(ai=NO_AI)
    m.omlx_holds("local")

    status, answer = on_this_mac().posts("/dashboard/api/ai/managed", {})

    assert status == 200, answer
    assert m.omlx_key() != "local"
    assert m.ai()["openai_key"] == m.omlx_key()


def test_a_key_omlx_holds_is_kept(mac):
    """Given oMLX's settings file holds a real key (an earlier install, or
    `stack ai key new`), when the admin picks famstack's oMLX, then `[ai]`
    gets that key: the apps that know it keep working."""
    m = mac(ai=NO_AI)
    m.omlx_holds(OWN_KEY)

    status, answer = on_this_mac().posts("/dashboard/api/ai/managed", {})

    assert status == 200, answer
    assert (m.omlx_key(), m.ai()["openai_key"]) == (OWN_KEY, OWN_KEY)


def test_a_half_written_route_never_gives_omlx_another_servers_key(mac):
    """Given a write of the route stopped halfway: `[ai]` names famstack's
    oMLX but still holds the other server's key, when the install is
    pressed, then oMLX's key is its own, and `[ai]` gets it."""
    m = mac(ai=MANAGED + f'openai_key = "{REMOTE_KEY}"\n')

    status, answer = on_this_mac().posts("/dashboard/api/ai/managed", {})

    assert status == 200, answer
    assert m.omlx_key() not in ("", "local", REMOTE_KEY)
    assert m.ai()["openai_key"] == m.omlx_key()


def test_the_install_is_refused_from_another_device(mac):
    """The install writes `stack.toml`: from the Mac's address only."""
    m = mac()

    status, _ = phone().posts("/dashboard/api/ai/managed", {})

    assert status == 403
    assert "provider" not in m.ai()


# ── While the AI is set up ───────────────────────────────────────────────

def test_while_it_prepares_the_tile_shows_the_steps_and_the_page_goes_on(mac, jobs):
    """Given the AI's run downloads its model at 40 %, then the step shows
    the run's steps with the percent, that the admin can go on, and the way
    on to the next step; no command for the terminal and no second install."""
    mac(ai=MANAGED)
    run = AiRun(jobs, 40)

    page = on_this_mac().opens("/setup?step=ai")

    assert percents(page) == ["40"], ai_tile(page)
    assert page.texts("ai-go-on"), page.text
    assert "/setup?step=services" in [href for _, href in page.links("setup-act")]
    assert page.texts("setup-terminal-command") == [] and not installs(page)
    run.end()


def test_a_reload_or_another_browser_shows_the_same_step(mac, jobs):
    """Given the AI's run goes on, when the page is read again, here or on
    a second browser of the Mac, then each shows the same step and percent:
    the state is in the job, not in the page."""
    mac(ai=MANAGED)
    run = AiRun(jobs, 40)

    first = on_this_mac().opens("/setup?step=ai")
    again = on_this_mac().opens("/setup?step=ai")

    assert percents(first) == percents(again) == ["40"]
    run.end()


def test_a_second_browsers_install_joins_the_running_job(mac, jobs):
    """Given one browser started famstack's oMLX and its run holds the AI's
    lock, when a second browser, opened before, presses the install too,
    then it joins that run: no refusal, `[ai]` and its key unchanged, and
    the start it posts next gets the same job."""
    m = mac(ai=MANAGED + f'openai_key = "{OWN_KEY}"\n')
    m.omlx_holds(OWN_KEY)
    run = AiRun(jobs, 40)
    with ai_lock_held_elsewhere(m.root):
        status, answer = on_this_mac().posts("/dashboard/api/ai/managed", {})
        started, job = on_this_mac().posts("/dashboard/api/jobs", {"action": "up", "target": "ai"})

    assert status == 200, answer
    assert m.ai()["openai_key"] == OWN_KEY
    assert (started, job["id"]) == (202, run.job.id)
    run.end()


def test_the_letter_step_waits_for_the_ai_while_it_is_set_up(mac, jobs):
    """Given the AI's run goes on and Documents runs, then the letter step
    says it waits for the AI and leads back to it."""
    mac(ai=MANAGED)
    run = AiRun(jobs, 40)

    page = on_this_mac().opens("/setup?step=try")

    assert "/setup?step=ai" in [href for _, href in page.links()]
    assert not page.texts("setup-waiting")
    run.end()


def test_a_failed_step_shows_its_reason_and_try_again(mac, jobs):
    """Given the preparation stopped (the disk is too small), then the AI's
    tile shows the reason and a start again, which goes on where it
    stopped; the step offers no install of its own, since `[ai]` names
    famstack's oMLX already."""
    mac(ai=MANAGED)
    AiRun(jobs, 40).end({"error": NO_ROOM, "stacklet": "ai"})

    page = on_this_mac().opens("/setup?step=ai")

    assert any(NO_ROOM in t for t in page.texts("tile-state")), ai_tile(page)
    assert starts_the_ai(page), ai_tile(page)
    assert not installs(page)


def test_after_a_restart_of_core_a_failed_run_still_shows_its_reason(mac, jobs):
    """Given the AI's last run failed (the disk is too small) and the tools
    server keeps no job of it (core restarted, or the outcome's time on the
    tile is over), then the AI's tile reads that run from its run log: it
    shows the reason and offers the start again, not a first Start."""
    from stack import run_log, runs

    m = mac(ai=MANAGED + f'openai_key = "{OWN_KEY}"\n')
    with runs.claim(m.root, ["ai"]):
        run_log.record_result(m.root, "ai", {"error": NO_ROOM, "stacklet": "ai", "name": "AI"})

    page = on_this_mac().opens("/setup?step=ai")

    assert 'data-status="failed"' in ai_tile(page), ai_tile(page)
    assert any(NO_ROOM in t for t in page.texts("tile-state")), ai_tile(page)
    assert starts_the_ai(page)


# ── At rest ──────────────────────────────────────────────────────────────

def test_an_ai_set_before_asks_nothing_and_starts_nothing(mac):
    """Given `[ai]` names famstack's oMLX and it runs and answered (an
    update from an earlier release), then the step says it is ready, offers
    no route and no install, and the page writes and starts nothing."""
    m = mac(ai=MANAGED, state="done")
    the_ai_answered(m.host)

    page = on_this_mac().opens("/setup?step=ai")

    assert page.texts("ai-can"), page.text
    assert not installs(page)
    assert m.asked("ai_managed") == [] and m.asked("up") == []


# ── The AI's tile ────────────────────────────────────────────────────────

@pytest.mark.parametrize("language", ["en", "de"])
def test_the_ais_tile_starts_from_the_page_and_says_macos_asks(mac, language):
    """The AI's setup needs no terminal: its tile offers its start like
    any other, with the note that macOS asks for permissions on the way."""
    mac(ai=MANAGED)

    page = on_this_mac(language).opens("/setup?step=ai")

    assert starts_the_ai(page), ai_tile(page)
    assert page.texts("tile-permissions"), ai_tile(page)


# ── The tile's words while the AI is set up ──────────────────────────────

def tile_words(language: str) -> dict:
    import stack_widget
    return stack_widget.words(language)


@pytest.mark.parametrize("language", ["en", "de"])
def test_while_it_prepares_the_tile_says_it_is_set_up_not_started(mac, jobs, language):
    """A preparation installs and downloads for a while: the tile says the
    AI is being set up, not started."""
    mac(ai=MANAGED)
    run = AiRun(jobs, 40)

    page = on_this_mac(language).opens("/setup?step=ai")

    assert page.texts("tile-state") == [tile_words(language)["setting_up"]], ai_tile(page)
    run.end()


@pytest.mark.parametrize("language", ["en", "de"])
def test_a_failed_preparation_says_the_setup_failed(mac, jobs, language):
    """Given the preparation failed, then the tile says the setup failed,
    with the reason, and names no terminal command beside its start."""
    mac(ai=MANAGED)
    AiRun(jobs, 40).end({"error": "oMLX did not start", "stacklet": "ai"})

    page = on_this_mac(language).opens("/setup?step=ai")

    [state] = page.texts("tile-state")
    assert state.startswith(tile_words(language)["setup_failed"].split("{")[0]), state
    assert "oMLX did not start" in state
    assert "tile-terminal" not in ai_tile(page)


# ── The run in the page's language ───────────────────────────────────────

DOWNLOAD_KEY = "ai.download.language_model"


def test_a_step_with_a_key_is_shown_in_the_pages_language(mac, jobs):
    """Given the AI's run reports its download with the step's key, then a
    German page shows the step in German, not the command line's English."""
    import run_words

    mac(ai=MANAGED)
    run = AiRun(jobs, 40, key=DOWNLOAD_KEY)

    page = on_this_mac("de").opens("/setup?step=ai")

    assert DOWNLOAD not in ai_tile(page)
    assert run_words.step({"text": DOWNLOAD, "key": DOWNLOAD_KEY}, "de") in ai_tile(page)
    run.end()


@pytest.mark.parametrize("language, size", [("en", "6.2 GB"), ("de", "6,2 GB")])
def test_too_little_disk_says_how_much_to_free(mac, jobs, language, size):
    """Given the preparation stopped because the disk is too small, then the
    tile says how much to free on this Mac, in the page's language, and
    offers the start again; not the command line's English."""
    mac(ai=MANAGED)
    AiRun(jobs, 40).end({"error": NO_ROOM, "key": "ai.no_room", "values": {"gb": 6.2},
                         "stacklet": "ai"})

    page = on_this_mac(language).opens("/setup?step=ai")

    [state] = page.texts("tile-state")
    assert size in state and NO_ROOM not in state, state
    assert starts_the_ai(page)
