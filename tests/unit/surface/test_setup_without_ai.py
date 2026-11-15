"""No AI for now: `[ai] provider = "none"` on the setup page and the dashboard.

A family can run famstack without an AI and add it later. "none" is a
choice kept in `stack.toml`, not a gap: the setup page's AI step counts
as done and says what works without the AI and what waits for it; the
first letter is filed without tags and the letter step says why; the
tiles of the stacklets that need the AI wait for it instead of offering
a start that fails; "Set up the AI now" leads back to the two routes.
An empty `[ai]` (no route chosen yet) is not "none": the step asks, as
`test_setup_ai_step.py` pins.

Driven through the tools server's FastAPI app as a browser on the Mac
opens it, read as served, with the host API in the dashboard stand-in's
states (`tests/dashboard_host.py`), whose listing is the real `stack list`
over the instance's `stack.toml`.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.dashboard_host import FIRST_DOCUMENT, NO_HISTORY

from .conftest import StackCLI
from .dsl import Page
from .setup_stand_in import on_this_mac, stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

NO_AI = '\n[ai]\nprovider = "none"\n'


@pytest.fixture
def mac(tmp_path, monkeypatch, httpserver):
    """A function that sets the Mac up: `mac(ai=NO_AI, state="services")`.
    `ai` is the instance's `[ai]`, `history` what the memory's history
    tells of filed documents."""
    def enter(*, ai: str = NO_AI, state: str = "services", history: dict | None = None):
        root = tmp_path / "instance"
        root.mkdir(parents=True, exist_ok=True)
        (root / "stack.toml").write_text(
            f'[core]\ndata_dir = "{root / "data"}"\nhost = "localhost"\nprofile = "small"\n'
            f'extension_dirs = ["{root / "extensions"}"]\n\n[messages]\nserver_name = "simpson"\n'
            + ai)
        host = stack_in_state(tmp_path, monkeypatch, httpserver)(state)
        if history is not None:
            httpserver.clear_all_handlers()
            httpserver.expect_request("/history").respond_with_json(history)
        return host
    return enter


def rail(page: Page, step: str) -> str:
    """A step's status on the rail: done, current or todo."""
    found = re.search(rf'<li class="setup-rail-step" data-step="{step}" data-status="(\w+)"',
                      page.body)
    return found[1] if found else ""


def links(page: Page) -> list[str]:
    return [href.replace("&amp;", "&") for _, href in page.links()]


def tile(page: Page, sid: str) -> str:
    """The markup of one stacklet's tile or service card, "" when the page
    has none."""
    found = re.search(rf'<article class="(?:tile|service-card)" data-(?:id|service)="{sid}"'
                      r'.*?</article>', page.body, re.S)
    return found[0] if found else ""


# ── The AI step ──────────────────────────────────────────────────────────

def test_no_ai_for_now_counts_the_ai_step_as_done_and_says_what_waits(mac):
    """Given `provider = "none"`, then the AI step is done on the rail, says
    what works without the AI and what waits for it, and offers to set it
    up now, which opens the two routes."""
    mac()

    page = on_this_mac().opens("/setup?step=ai")

    assert rail(page, "ai") == "done"
    assert "Without an AI" in page.text
    assert "Documents are filed and searchable" in page.text
    assert "Tags, the wiki and the diary come with the AI" in page.text
    assert "/setup?step=ai&ai=choose" in links(page)
    assert "/setup?step=services" in links(page)


def test_set_up_the_ai_now_opens_the_two_routes(mac):
    """Given `provider = "none"`, when the admin follows "Set up the AI now",
    then the step offers famstack's oMLX and their own server, as on a
    first run; nothing is written until a route runs."""
    m = mac()

    page = on_this_mac().opens("/setup?step=ai&ai=choose")

    assert re.findall(r'class="setup-box ai-route" data-route="(\w+)"', page.body) == \
        ["managed", "server"]
    assert 'provider = "none"' in (m.root / "stack.toml").read_text()


def real_configure(m, monkeypatch):
    """The host API's `configure` as it runs on the Mac: the real `stack
    configure ai --json --answers -` of this checkout on the instance, the
    answers on stdin, in this process. Returns what the command printed."""
    cli = StackCLI(m.root, monkeypatch)
    return lambda answers: cli("configure", "ai", "--json", "--answers", "-",
                               stdin=json.dumps(answers)).answer


@pytest.mark.parametrize("set_up_before", [False, True])
def test_go_on_without_an_ai_answers_the_route_form_and_the_step_is_done(
        mac, monkeypatch, set_up_before):
    """Given no `[ai]`, also on an instance whose ai stacklet was set up
    before, when the admin presses "Go on without an AI", then the page's
    answer goes through the configure API to the ai stacklet's route form,
    `[ai]` says no AI for now, and the step reads as done."""
    m = mac(ai="")
    if set_up_before:
        (m.root / ".stack" / "ai.setup-done").touch()
    m.configure = real_configure(m, monkeypatch)

    status, answer = on_this_mac().posts("/dashboard/api/configure/ai",
                                         {"answers": {"route": "none"}})
    page = on_this_mac().opens("/setup?step=ai")

    assert (status, answer) == (200, {"ready": True})
    assert 'provider = "none"' in (m.root / "stack.toml").read_text()
    assert rail(page, "ai") == "done"


def test_no_ai_for_now_in_german(mac):
    mac()

    page = on_this_mac("de").opens("/setup?step=ai")

    assert "Ohne KI" in page.text
    assert "KI jetzt einrichten" in page.text


def test_the_step_list_says_the_ai_is_off(mac):
    """Given `provider = "none"`, then the AI's line in the step list says
    there is none, not what an AI would do."""
    mac()

    page = on_this_mac("de").opens("/setup?step=ai")

    assert re.search(r'data-step="ai".*?Ohne KI', page.body, re.S)
    assert "Liest, hört zu und spricht" not in page.text


def test_one_verb_sets_up_the_ai_everywhere(mac):
    """Given `provider = "none"`, then the step, the AI's tile and the
    dashboard all say "KI einrichten", never "einschalten"."""
    mac(history=FIRST_DOCUMENT)

    pages = [on_this_mac("de").opens(path) for path in
             ("/setup?step=ai", "/setup?step=stack", "/dashboard")]

    for page in pages:
        assert "einrichten" in page.text and "einschalten" not in page.text, page.text


def test_the_current_step_moves_past_the_ai(mac):
    """Given `provider = "none"` and the family's services running, then
    the setup goes on to the first letter, not back to the AI."""
    mac()

    page = on_this_mac().opens("/setup")

    assert rail(page, "try") == "current"


# ── The first letter ─────────────────────────────────────────────────────

def test_the_letter_step_waits_for_a_letter_and_says_it_gets_no_tags(mac):
    """Given `provider = "none"` and Documents running, then the letter
    step waits for the first letter, never for the AI, and says the letter
    is filed without tags and why."""
    mac(history=NO_HISTORY)

    page = on_this_mac().opens("/setup?step=try")

    assert 'data-watch="' in page.body
    assert "Your AI is not running yet" not in page.text
    assert "filed without tags" in page.text


def test_a_letter_filed_without_an_ai_finishes_the_step(mac):
    mac(history=FIRST_DOCUMENT)

    page = on_this_mac().opens("/setup?step=try")

    assert rail(page, "try") == "done"
    assert "filed without tags" in page.text


# ── The tiles ────────────────────────────────────────────────────────────

def test_the_ais_tile_says_there_is_none_and_offers_to_set_it_up(mac):
    """Given `provider = "none"`, then the AI's tile in "Your stack" says
    the stack runs without an AI and links the two routes."""
    mac()

    page = on_this_mac().opens("/setup?step=stack")
    ai = tile(page, "ai")

    assert "Without an AI for now" in ai, page.text
    assert 'href="/setup?step=ai&amp;ai=choose"' in ai


def test_the_agent_waits_for_the_ai(mac):
    """Given `provider = "none"`, then the agent waits for the AI: no
    Start, which would start a service with nothing to ask."""
    mac()

    page = on_this_mac().opens("/setup?step=stack")
    found = tile(page, "agent")

    assert "Waits for the AI" in found, page.text
    assert "tile-add" not in found


def test_the_memory_says_what_waits_for_the_ai(mac):
    """Given `provider = "none"`, then the memory runs and its service card
    says in one line that the wiki texts and the diary wait for the AI."""
    mac()

    for page in (on_this_mac().opens("/setup?step=stack"), on_this_mac().opens("/dashboard")):
        memory = tile(page, "memory")
        assert memory.count("wait for the AI") == 1, memory
        assert "The wiki texts and the diary wait for the AI" in memory


def test_the_memory_says_nothing_of_the_ai_with_an_empty_ai(mac):
    mac(ai="")

    assert "wait for the AI" not in tile(on_this_mac().opens("/setup?step=stack"), "memory")


def test_with_an_empty_ai_the_agent_does_not_wait_for_the_ai(mac):
    """An empty `[ai]` is no choice yet: the tiles stay as they are."""
    mac(ai="")

    page = on_this_mac().opens("/setup?step=stack")

    assert "Waits for the AI" not in tile(page, "agent")


# ── The dashboard ────────────────────────────────────────────────────────

def test_the_dashboard_offers_to_set_up_the_ai(mac):
    """Given `provider = "none"` and every step done, then the dashboard's
    link to what is left offers to set up the AI."""
    mac(history=FIRST_DOCUMENT)

    page = on_this_mac().opens("/setup")
    assert rail(page, "ready") == "current", page.text

    page = on_this_mac().opens("/dashboard")

    assert re.search(r'class="fs-link status-left" href="/setup\?step=ai&amp;ai=choose"',
                     page.body), page.body
    assert "Set up the AI" in page.text
