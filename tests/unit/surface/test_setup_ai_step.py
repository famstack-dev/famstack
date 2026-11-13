"""The AI step of /setup: whether the AI answers, and what it can do.

Once `[ai]` names a server, the step says whether the AI answered the
stack's one short question for it: ready with what it can do, not asked
yet, or not answering with the server's own error.

Driven through the tools server's FastAPI app as a browser opens it, read
as served, with the host API in the dashboard stand-in's "services" or
"done" state (`tests/dashboard_host.py`). The AI step's host commands,
`stack ai recommended show` and `stack ai check --last`, run for real on
the stand-in's instance, against a stand-in for an oMLX on localhost
(`omlx_stand_in.py`). The instance is a 16 GB Mac by its profile
(`[core] profile = "small"`): speech is off unless `[ai]` turns it on.
"""

from __future__ import annotations

import importlib
import re
import tomllib

import pytest

from tests.unit.stacklets.omlx_stand_in import BROKEN, KEY, OMLX, hanging

from .dsl import Page
from .setup_stand_in import core_restarts, on_this_mac, phone, stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")


# ── The instance ─────────────────────────────────────────────────────────

class Mac:
    """The Simpsons' Mac at the AI step: the host API's stand-in, the
    oMLX's stand-in and the instance's `stack.toml`."""

    def __init__(self, host, app, root):
        self.host, self.app, self.root = host, app, root

    def toml(self) -> dict:
        return tomllib.loads((self.root / "stack.toml").read_text())


def _write_toml(root, extra: str = "") -> None:
    """A small Mac's instance: `[core] profile = "small"`, port mode."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "stack.toml").write_text(
        f'[core]\ndata_dir = "{root / "data"}"\nhost = "localhost"\nprofile = "small"\n'
        f'extension_dirs = ["{root / "extensions"}"]\n\n[messages]\nserver_name = "simpson"\n'
        + extra)


@pytest.fixture
def mac(tmp_path, monkeypatch, httpserver):
    """A function that sets the Mac up: `mac(ai=connected(httpserver))`.
    An oMLX answers on localhost, wanting its key; `ai` is the instance's
    `[ai]`. With `computes` False the oMLX lists its models and fails every
    completion."""
    def enter(*, ai: str = "", state: str = "services", computes: bool = True) -> Mac:
        root = tmp_path / "instance"
        _write_toml(root, ai)
        host = stack_in_state(tmp_path, monkeypatch, httpserver)(state)
        omlx = OMLX(httpserver, wants_key=True, holds=True, computes=computes)
        return Mac(host, omlx, root)
    return enter


def connected(httpserver) -> str:
    """`[ai]` as `stack ai switch` leaves it: the oMLX stand-in, its key."""
    return (f'\n[ai]\nprovider = "external"\nopenai_url = "{httpserver.url_for("/v1")}"\n'
            f'openai_key = "{KEY}"\n')


def managed(httpserver) -> str:
    """`[ai]` of the AI the stack manages, here at the oMLX stand-in."""
    return connected(httpserver).replace('"external"', '"managed"')


def step(page: Page) -> str:
    """The sub-step the page shows, by its part's class."""
    for part, sub in (("ai-can", "ready"), ("ai-not-asked", "not asked"),
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


# ── Ready ────────────────────────────────────────────────────────────────

def test_an_ai_that_runs_says_what_it_can_do(mac, httpserver):
    """Given the AI runs with speech on, then 4e lists reading, hearing
    and speaking, each done."""
    mac(ai=managed(httpserver) + "speech = true\n", state="done").host.up("ai")

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "ready"
    assert re.findall(r'<li data-status="(\w+)">', page.body) == ["done", "done", "done"]


def test_with_speech_off_voice_messages_are_said_to_be_off(mac, httpserver):
    mac(ai=managed(httpserver) + "speech = false\n", state="done").host.up("ai")

    page = on_this_mac().opens("/setup?step=ai")

    assert re.findall(r'<li data-status="(\w+)">', page.body) == ["done", "off"]



def test_an_ai_server_the_stack_does_not_manage_is_ready_without_a_terminal(mac, httpserver):
    """Given the stack uses an AI server it does not manage and the AI is
    ready, then 4e shows no AI tile and no `stack up ai`."""
    mac(ai=connected(httpserver), state="done").host.up("ai")

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "ready"
    assert 'data-id="ai"' not in page.body
    assert "stack up ai" not in page.body

# ── Does the AI answer ───────────────────────────────────────────────────
#
# The AI's run ends with one tiny question to the AI; its answer is kept in
# the instance, by the address, the model and the key it was asked with.
# `m.host.up("ai")` is the run as the host stand-in plays it.

def test_the_ais_run_asks_it_and_the_step_says_ready(mac, httpserver):
    """Given the AI's run asked the AI one short question and it
    answered, then the AI step says the AI is ready."""
    m = mac(ai=managed(httpserver), state="done")

    m.host.up("ai")
    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "ready", page.text
    assert len(m.app.completed) == 1


def test_a_finished_setup_stays_done_when_core_restarts(mac, httpserver):
    """Given the AI answered at the end of its run, when core restarts,
    then the AI step is still done: the answer is kept in the instance,
    not in core."""
    m = mac(ai=managed(httpserver), state="done")
    m.host.up("ai")

    core_restarts()
    page = on_this_mac().opens("/setup")

    assert ai_on_the_rail(page) == "done", page.text
    assert step(on_this_mac().opens("/setup?step=ai")) == "ready"


def test_an_ai_that_lists_its_models_but_cannot_answer_is_not_ready(mac, httpserver):
    """Given the AI lists its models and fails every completion, as an oMLX
    built for a newer macOS does, when the AI's run has asked it, then
    the step does not say ready: it says the AI does not answer, shows
    the server's own error in one line, and offers the AI's run again and
    another AI server, never a download. The AI step is not done."""
    m = mac(ai=managed(httpserver), state="done", computes=False)

    m.host.up("ai")
    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "no answer", page.text
    assert "Your AI is ready" not in page.text
    assert page.texts("ai-error") == [f"HTTP 500: {BROKEN}"]
    links = dict(page.links("ai-no-answer"))
    assert not any(href.endswith(".dmg") for href in links.values()), links
    assert any(href == "/setup?step=ai&ai=other" for href in links.values()), links
    assert starts_the_ai(page)
    assert ai_on_the_rail(page) != "done"


def test_a_server_that_hangs_ends_the_run_with_a_timeout_and_the_links(mac, httpserver,
                                                                       monkeypatch):
    """Given the AI takes longer than the run waits for its answer, then
    the run ends and the step says the AI did not answer in time, with
    the AI's run again and another AI server: no page that asks for ever,
    and no advice about an oMLX build."""
    # By the module itself: `stack.ai.probe` as a dotted path can name a
    # function of the package's namespace instead.
    monkeypatch.setattr(importlib.import_module("stack.ai.probe"), "ANSWER_SECONDS", 0.2)
    with hanging() as server:
        ai = managed(httpserver).replace(httpserver.url_for("/v1"), server.url_for("/v1"))
        m = mac(ai=ai, state="done")

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
def test_a_new_model_or_key_does_not_reopen_a_finished_setup(mac, httpserver, change):
    """Given the AI answered, when `[ai]` names another model or another
    key at the same address (as `stack ai key new` writes), then that
    answer is unknown, not failed: the AI step stays done, and on the
    step itself famstack offers to ask the AI now, beside the way on."""
    m = mac(ai=managed(httpserver), state="done")
    m.host.up("ai")
    toml = m.root / "stack.toml"
    text = toml.read_text()
    if change.startswith("openai_key"):
        text = re.sub(r'openai_key = "[^"]*"\n', "", text)
    toml.write_text(text + change)

    page = on_this_mac().opens("/setup?step=ai")

    assert ai_on_the_rail(page) == "done", page.text
    assert step(page) == "not asked", page.text
    assert starts_the_ai(page)
    assert "/setup?step=services" in [href for _, href in page.links()]


def test_a_setup_finished_before_this_check_stays_done(mac, httpserver):
    """Given a setup finished with a release that did not ask the AI, so
    no answer is kept, when the admin opens the setup after the update,
    then nothing reopens: the AI counts as it did before, the letter step
    stays done, and the setup is ready. Only the AI step offers to ask."""
    m = mac(ai=managed(httpserver), state="done")

    page = on_this_mac().opens("/setup?phone=signed")
    letter = on_this_mac().opens("/setup?step=try")
    ai = on_this_mac().opens("/setup?step=ai")

    assert ai_on_the_rail(page) == "done", page.text
    assert "Welcome home" in page.text
    assert "Your AI is not running yet" not in letter.text
    assert step(ai) == "not asked" and starts_the_ai(ai)
    assert m.app.completed == []


def test_the_ais_page_opens_the_server_the_stack_reads(mac, httpserver):
    """Given the stack reads an oMLX at its own address (a server on
    another port than the stack's engine), when the AI step says the AI
    does not answer, then the AI's Open goes to that server's admin page
    at that address, never to the stack's engine port."""
    m = mac(ai=connected(httpserver), state="done", computes=False)
    m.host.up("ai")

    page = on_this_mac().opens("/setup?step=ai")

    app = httpserver.url_for("/admin")
    tile = re.search(r'data-id="ai".*?</article>', page.body, re.S)
    assert tile, page.body
    opens = re.findall(r'href="(http[^"]+)"', tile[0])
    assert opens == [app], opens
    assert ":42060" not in page.body


@pytest.mark.parametrize("status, said, says", [
    (404, "Model 'Qwen3.5-9B-MLX-4bit' not found", "does not have the model"),
    (401, "Invalid API key", "refused famstack's API key"),
])
def test_a_missing_model_or_a_refused_key_says_what_it_is(mac, httpserver, status, said, says):
    """Given the AI fails the question with a missing model or a refused
    key, then the step says that, with the server's own error, and gives
    no advice about an oMLX build for another macOS."""
    m = mac(ai=managed(httpserver), state="done")
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
    m = mac(ai=managed(httpserver), state="done", computes=False)
    m.host.up("ai")

    page = on_this_mac().opens("/setup?step=try")

    assert "data-watch" not in page.body
    assert "Your AI is not running yet" in page.text


def test_the_answer_in_german(mac, httpserver):
    """The German words of a failed answer: no line starts with "Sie"."""
    m = mac(ai=managed(httpserver), state="done", computes=False)
    m.host.up("ai")

    page = on_this_mac("de").opens("/setup?step=ai")

    box = " ".join(page.texts("ai-no-answer"))
    assert "Eure KI antwortet nicht" in page.text
    assert "Erneut prüfen" in box
    assert not re.search(r"(^|[.:!?]\s)Sie\b", box), box


# ── The two routes ───────────────────────────────────────────────────────
#
# Each test sets `stack.toml` (and the answer file) and reads the page with
# no earlier page state: the step is computed from them alone.

MANAGED_COMMAND = "./stack ai switch managed"


def routes(page: Page) -> list[tuple[str, str, bool]]:
    """The routes the choice offers, in page order: (route, the address
    its button opens, whether it is the recommended one)."""
    found = re.findall(r'<div class="setup-box ai-route" data-route="(\w+)"( data-recommended)?'
                       r'.*?<a [^>]*href="([^"]+)"', page.body, re.S)
    return [(route, href.replace("&amp;", "&"), bool(recommended))
            for route, recommended, href in found]


def asked_the_server(httpserver) -> list[str]:
    """What the oMLX stand-in was asked, by path; the memory's history
    shares the stand-in's port and is not the AI."""
    return [request.path for request, _ in httpserver.log if request.path != "/history"]


def test_a_first_run_offers_two_routes_famstacks_omlx_first_and_recommended(mac, httpserver):
    """Given no `[ai]`, when the admin opens the AI step, then it offers
    two routes, famstack's oMLX first and recommended, and asks no AI
    server anything: nothing changes until the admin picks one."""
    m = mac()

    page = on_this_mac().opens("/setup?step=ai")

    assert routes(page) == [("managed", "/setup?step=ai&ai=install", True),
                            ("server", "/setup?step=ai&ai=other", False)], page.text
    assert asked_the_server(httpserver) == []
    assert "[ai]" not in (m.root / "stack.toml").read_text()


def test_route_one_shows_the_managed_command_and_the_ais_tile(mac):
    """Given route 1 chosen, then the step shows `./stack ai switch
    managed` with Copy, the AI's tile that follows the run, and route 2
    as the way out (a Mac without Homebrew has no other)."""
    mac()

    page = on_this_mac().opens("/setup?step=ai&ai=install")

    assert page.texts("setup-terminal-command") == [MANAGED_COMMAND]
    assert 'data-id="ai"' in page.body
    assert "/setup?step=ai&ai=other" in [href for _, href in page.links()]


def test_a_managed_ai_not_set_up_yet_shows_the_command_without_a_choice(mac):
    """Given `[ai]` names the AI the stack manages and its run has not
    finished yet (a run that failed halfway, or one started in the
    terminal), then the step shows the command again, never the choice."""
    mac(ai='\n[ai]\nprovider = "managed"\nopenai_url = "http://localhost:42060/v1"\n')

    page = on_this_mac().opens("/setup?step=ai")

    assert routes(page) == []
    assert page.texts("setup-terminal-command") == [MANAGED_COMMAND]


def test_famstacks_omlx_beside_the_running_omlx_app_says_why_it_does_not_answer(
        mac, monkeypatch):
    """Given `[ai]` names famstack's oMLX and the oMLX app runs (launchd
    started the app at login, famstack's oMLX stepped aside), then the
    step says the AI does not answer, and why: quit the app, then check
    again. Never the command to install it."""
    from stacklets.ai import omlx_app
    # `true` finds a process, as `pgrep` does while the app runs.
    monkeypatch.setattr(omlx_app, "PROCESS_COMMAND", ("true",))
    mac(ai='\n[ai]\nprovider = "managed"\nopenai_url = "http://localhost:42060/v1"\n')

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "no answer", page.text
    assert "Quit the oMLX app" in page.text
    assert page.texts("setup-terminal-command") == []


def test_famstacks_omlx_not_running_without_the_app_shows_the_command(mac):
    """The app is closed: the run did not finish, so the command again."""
    mac(ai='\n[ai]\nprovider = "managed"\nopenai_url = "http://localhost:42060/v1"\n')

    page = on_this_mac().opens("/setup?step=ai")

    assert "Quit the oMLX app" not in page.text
    assert page.texts("setup-terminal-command") == [MANAGED_COMMAND]


def test_route_two_is_a_form_with_the_omlx_apps_address_filled_and_the_omlx_note(mac):
    """Given route 2 chosen, then the step shows the address, filled with
    the oMLX app's default address, the key, Connect and Back to the
    choice, and says famstack is tested and tuned with oMLX."""
    mac()

    page = on_this_mac().opens("/setup?step=ai&ai=other")

    address = re.search(r'<input[^>]*name="address"[^>]*value="([^"]*)"', page.body)
    assert address and address[1] == "http://localhost:8000/v1"
    assert 'name="api_key"' in page.body
    assert re.search(r'<button[^>]*type="submit"', page.body)
    assert ("/setup?step=ai") in [href for _, href in page.links("ai-other")]
    assert page.texts("ai-omlx-note")


def test_a_server_elsewhere_that_answered_is_ready_with_the_omlx_note(mac, httpserver):
    """Given `[ai]` names a server the stack does not manage and it
    answered, then the step says the AI is ready, and that famstack is
    tested and tuned with oMLX."""
    m = mac(ai=connected(httpserver), state="done")
    m.host.up("ai")

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "ready", page.text
    assert page.texts("ai-omlx-note")
    assert routes(page) == []


def test_a_server_elsewhere_that_cannot_answer_offers_the_three_ways_on(mac, httpserver):
    """Given a server elsewhere that lists its models and fails every
    answer (the oMLX app built for another macOS), then the step says the
    AI does not answer with the server's line, and offers to check again,
    another server, or famstack's oMLX instead. Never a download."""
    m = mac(ai=connected(httpserver), computes=False)
    m.host.up("ai")

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "no answer", page.text
    assert page.texts("ai-error") == [f"HTTP 500: {BROKEN}"]
    hrefs = [href for _, href in page.links("ai-no-answer")]
    assert "/setup?step=ai&ai=other" in hrefs and "/setup?step=ai&ai=install" in hrefs
    assert not any(href.endswith(".dmg") for href in hrefs)
    assert 'class="fs-button ai-check-again"' in page.body


def test_check_again_asks_a_server_elsewhere_and_the_step_follows(mac, httpserver):
    """Given a server elsewhere that did not answer and answers now, when
    the admin presses Check again, then the stack asks it (`stack ai
    check`) and the step says the AI is ready."""
    m = mac(ai=connected(httpserver), computes=False)
    m.host.up("ai")
    httpserver.clear_all_handlers()
    OMLX(httpserver, wants_key=True)

    status, answer = on_this_mac().posts("/dashboard/api/ai/check", {})
    page = on_this_mac().opens("/setup?step=ai")

    assert status == 200 and answer["ok"] is True, answer
    assert step(page) == "ready", page.text


def test_check_again_is_refused_from_another_device(mac, httpserver):
    """Check again asks the AI from the Mac's address only, as every change does."""
    mac(ai=connected(httpserver))

    status, _ = phone().posts("/dashboard/api/ai/check", {})

    assert status == 403


def test_a_set_server_that_is_gone_shows_its_state_never_the_choice(mac, httpserver):
    """Given `[ai]` names a server elsewhere that answered once and is gone
    now, then the step does not ask for a route again: it says the AI
    does not answer, with the ways on."""
    m = mac(ai=connected(httpserver), state="done")
    m.host.up("ai")
    m.host.silent.add(httpserver.url_for("/v1"))

    page = on_this_mac().opens("/setup?step=ai")

    assert routes(page) == []
    assert step(page) == "no answer", page.text
    assert "/setup?step=ai&ai=other" in [href for _, href in page.links("ai-no-answer")]


def test_a_server_set_in_the_terminal_shows_at_the_next_read(mac, httpserver):
    """Given the page showed the choice, when `[ai]` is set in the
    terminal (`stack ai switch`), then the next read of the page shows
    that server ready: nothing of the first read is kept."""
    m = mac()
    assert routes(on_this_mac().opens("/setup?step=ai"))

    m.host._stack().run_cli_command("ai", "switch", [httpserver.url_for("/v1"), "--key", KEY])
    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "ready", page.text


# ── Route 2's hints for an oMLX ──────────────────────────────────────────

def hints(page: Page) -> dict[str, str]:
    """The settings the ready step recommends for an oMLX, by kind."""
    return dict(re.findall(r'<li data-hint="(\w+)">([^<]*)</li>', page.body))


def test_an_omlx_on_a_16_gb_mac_gets_the_small_profiles_settings_as_hints(mac, httpserver):
    """Given a server elsewhere that is an oMLX (it describes itself as
    "oMLX API") and answered, on a Mac whose profile is small, then the
    ready step recommends the light chat model, speech off, and oMLX's
    memory ceiling, chunked prefill and idle unload, with a link to the
    admin guide. famstack changes none of it on the server."""
    m = mac(ai=connected(httpserver), state="done")
    m.host.up("ai")

    page = on_this_mac().opens("/setup?step=ai")

    assert step(page) == "ready", page.text
    assert set(hints(page)) == {"chat", "ceiling", "prefill", "idle"}, page.text
    assert "Qwen3.5 9B" in hints(page)["chat"]
    assert "9" in hints(page)["ceiling"] and "10" in hints(page)["idle"]
    assert any("admin-guide.md#" in href for _, href in page.links("ai-hints"))
    assert m.app.changes == []


def test_an_omlx_on_a_large_mac_gets_the_larger_model_and_speech(mac, httpserver):
    """Given the same oMLX on a Mac whose profile is large, then the hints
    name the larger chat model and the two speech models, and no memory
    settings: oMLX keeps its own on a large Mac."""
    m = mac(ai=connected(httpserver), state="done")
    toml = m.root / "stack.toml"
    toml.write_text(toml.read_text().replace('profile = "small"', 'profile = "large"'))
    m.host.up("ai")

    page = on_this_mac().opens("/setup?step=ai")

    assert set(hints(page)) == {"chat", "listen", "speak"}, page.text
    assert "Qwen3.6" in hints(page)["chat"]


def test_route_one_shows_a_run_that_stopped_for_the_omlx_app(mac, monkeypatch):
    """Given famstack's oMLX run stopped because the oMLX app runs, then
    route 1 shows the run's message on the AI's tile, as it shows the end
    of any run: quit the app first, nothing changed."""
    import live_routes
    from stack.live.envelope import Event
    from stack.live.hub import Hub
    from stack.live.jobs import Jobs

    from stacklets.ai.omlx_app import QUIT_FIRST

    hub = Hub()
    monkeypatch.setattr(live_routes, "hub", hub)
    monkeypatch.setattr(live_routes, "jobs", Jobs(hub))
    mac()
    cursor = hub.cursor

    def stopped():
        yield Event.now(1, "result", {"cancelled": QUIT_FIRST})

    live_routes.jobs.start("up", "ai", stopped)
    while not any(e.type == "job.ended" for e in hub.after(cursor, timeout=5)):
        pass

    page = on_this_mac().opens("/setup?step=ai&ai=install")

    assert page.texts("setup-terminal-command") == [MANAGED_COMMAND]
    assert any("Quit the oMLX app first" in t for t in page.texts("tile-state")), page.body
