"""The setup page: after the first run, `/setup` walks the admin through
the install one step at a time.

The page has eight steps, in the order of the install: the family, the
family chat, the chat on this Mac, the chat on the phone, the AI, photos
and documents, the first letter, and ready. A rail shows each step as
done, current or still to come, and the main column shows one step: the
current one, or the one the admin opened from the rail. A step that needs
the Mac's terminal says so and shows the command; its tile follows the run.

Driven through the tools server's FastAPI app as a browser opens it on the
published port (the Browser DSL), read as served, without JavaScript. The
host API is the dashboard's shared stand-in (`tests/dashboard_host.py`) in
one of its named states, reached at the client's edge; the memory's
history is served on localhost by `pytest-httpserver`.
"""

from __future__ import annotations

import re

import pytest

from .dsl import Page
from .setup_stand_in import paired_phone, on_this_mac, stack_in_state
from tests.dashboard_host import SIGN_INS

from stack.live import client  # noqa: E402  (on the path through setup_stand_in)

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

STEPS = ["family", "chat", "mac", "phone", "ai", "services", "try", "ready"]


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver):
    """Put the Simpsons' stack in a named state: `instance("chat")`
    (`setup_stand_in.stack_in_state`)."""
    return stack_in_state(tmp_path, monkeypatch, httpserver)


def rail(page) -> list[tuple[str, str]]:
    """(step id, status) of every step on the rail, in order."""
    return re.findall(r'<li class="setup-rail-step" data-step="(\w+)" data-status="(\w+)"', page.body)


def shown(page) -> str:
    """The id of the step the main column shows."""
    found = re.search(r'<section class="setup-step" data-step="(\w+)"', page.body)
    return found[1] if found else ""


def step_part(page) -> Page:
    """The main column's step, read on its own."""
    found = re.search(r'<section class="setup-step".*?</section>', page.body, re.S)
    return Page(200, "text/html", found[0] if found else "")


def tiles(page) -> list[str]:
    """The stacklets whose tiles the shown step holds, in order."""
    return re.findall(r'<article class="tile" data-id="(\w+)"', step_part(page).body)


def service_cards(page) -> list[str]:
    """The services whose service cards the shown step holds, in order."""
    return re.findall(r'<article class="service-card" data-service="(\w+)"', step_part(page).body)


# ── The rail and the current step ────────────────────────────────────────

def test_the_page_shows_the_eight_steps_and_the_current_one(instance):
    """Given the family is set up and only core runs, when the admin opens
    /setup, then the rail shows the eight steps with the family done and the
    chat current, and the main column shows the chat's step."""
    instance("fresh")
    page = on_this_mac().opens("/setup")

    assert page.status == 200
    assert rail(page) == [("family", "done"), ("chat", "current"), *[(s, "todo") for s in STEPS[2:]]]
    assert shown(page) == "chat"


def test_on_a_phone_the_rail_is_one_line_with_the_step_and_its_number(instance):
    instance("fresh")
    page = on_this_mac().opens("/setup")

    [line] = page.texts("setup-position")
    assert "2" in line and "8" in line
    assert page.texts("setup-rail-title")[1] in line


def test_every_step_opens_from_the_rail_and_a_done_step_opens_again(instance):
    instance("fresh")
    page = on_this_mac().opens("/setup")

    assert [href for _, href in page.links("setup-rail")] == [f"/setup?step={s}" for s in STEPS]
    again = on_this_mac().opens("/setup?step=family")
    assert shown(again) == "family"
    assert rail(again)[1] == ("chat", "current")


def test_an_unknown_step_shows_the_current_one(instance):
    instance("fresh")
    assert shown(on_this_mac().opens("/setup?step=nonsense")) == "chat"


def test_the_topbar_leads_back_to_the_dashboard(instance):
    instance("fresh")
    assert ("/dashboard" in [href for _, href in on_this_mac().opens("/setup").links("topbar")])


# ── The steps on today's machinery ───────────────────────────────────────

def test_the_chat_step_starts_the_chat_from_its_service_card(instance):
    instance("fresh")
    page = on_this_mac().opens("/setup")

    assert service_cards(page) == ["messages"]
    assert 'data-target="messages"' in step_part(page).body


def test_once_the_chat_runs_the_chat_on_this_mac_is_next_with_its_sign_in(instance):
    """The server on this Mac is localhost's, so the app on the Mac reaches
    it without the home network."""
    instance("chat", mac="192.168.1.20")
    page = on_this_mac().opens("/setup")

    assert rail(page)[1:3] == [("chat", "done"), ("mac", "current")]
    step = step_part(page)
    assert "http://localhost:42031" in step.texts("setup-value")
    assert "homer" in step.texts("setup-value")
    assert "https://element.io/download" in [href for _, href in step.links()]


def test_on_another_device_step_3_holds_no_password(instance, monkeypatch):
    """Given the chat runs, when a paired phone opens the step "The chat on
    this Mac", then it gets the page that says the setup is on the Mac,
    with no password: the family's passwords show only on the Mac that
    runs famstack."""
    monkeypatch.setenv("LINK_BASE_URL", "http://192.168.1.20:42000/go")
    instance("chat", mac="192.168.1.20")

    elsewhere = paired_phone().opens("/setup?step=mac")
    here = step_part(on_this_mac().opens("/setup?step=mac"))

    assert elsewhere.status == 403 and "data-secret" not in elsewhere.body
    assert "data-secret" in here.body


def app_part(page) -> str:
    """The status of the phone step's part 2, the sign-in with the app."""
    found = re.search(r'data-part="app" data-status="(\w+)"', page.body)
    return found[1] if found else ""


def test_a_sign_in_in_the_macs_browser_finishes_the_mac_step_not_the_phone(instance):
    """Given the admin signed in only in the browser on the Mac, the phone
    is next, its sign-in waits, and the chat can tell it is no phone app,
    so the step offers no "My phone is signed in", only "Skip for now"."""
    instance("signed", mac="192.168.1.20")
    page = on_this_mac().opens("/setup")

    assert rail(page)[2:4] == [("mac", "done"), ("phone", "current")]
    assert app_part(page) == "waiting"
    step = step_part(page)
    assert "http://192.168.1.20:42031" in step.texts("setup-value")
    hrefs = [href for _, href in step.links()]
    assert "/setup?phone=signed" not in hrefs and "/setup?phone=skipped" in hrefs


def test_a_line_that_waits_breathes_and_a_line_that_asks_stays_still(instance):
    """Given the chat runs and the admin has not signed in on the Mac, the
    step's line that waits for the sign-in is marked as live, so it
    breathes. Given the chat does not run, the line that asks the admin to
    start it first is not."""
    instance("fresh")
    asking = step_part(on_this_mac().opens("/setup?step=mac")).body
    instance("chat")
    waiting = step_part(on_this_mac().opens("/setup?step=mac")).body

    assert re.search(r'<p class="setup-waiting setup-live"[^>]*>Waiting for you to sign in', waiting)
    assert '<p class="setup-waiting">Start the family chat first.</p>' in asking


def test_a_sign_in_with_the_phone_app_finishes_the_phone_step(instance):
    """Given the admin signed in with Element X on a phone, the chat lists
    that sign-in as the phone app: the step's part 2 is done and the AI is
    next, with nothing for the admin to confirm."""
    host = instance("signed")
    host.sign_ins["homer"] = [SIGN_INS["browser"], SIGN_INS["phone"]]
    page = on_this_mac().opens("/setup?step=phone")

    assert rail(page)[2:5] == [("mac", "done"), ("phone", "done"), ("ai", "current")]
    assert app_part(page) == "done"
    assert "/setup?phone=signed" not in [href for _, href in step_part(page).links()]


@pytest.mark.parametrize("sign_ins", [[SIGN_INS["browser"], SIGN_INS["unknown"]], None],
                         ids=["a sign-in of no known app", "no list of sign-ins"])
def test_when_the_chat_cannot_tell_the_app_the_admin_confirms_the_phone(instance, sign_ins):
    """Given a sign-in whose app the chat server does not say, or no list
    of sign-ins at all, the phone app may be signed in: the step offers
    "My phone is signed in" beside "Skip for now"."""
    host = instance("signed")
    host.sign_ins["homer"] = sign_ins
    page = on_this_mac().opens("/setup")

    assert rail(page)[3] == ("phone", "current")
    hrefs = [href for _, href in step_part(page).links()]
    assert "/setup?phone=signed" in hrefs and "/setup?phone=skipped" in hrefs


def test_the_admin_confirms_the_phone_and_the_ai_is_next(instance):
    """Where the chat cannot tell which app signed in, the admin says the
    phone is in. The page keeps it on this browser."""
    host = instance("signed")
    host.sign_ins["homer"] = [SIGN_INS["unknown"]]
    browser = on_this_mac()
    confirmed = browser.opens("/setup?phone=signed")
    again = browser.opens("/setup")

    assert rail(confirmed)[3:5] == [("phone", "done"), ("ai", "current")]
    assert rail(again)[3:5] == [("phone", "done"), ("ai", "current")]


def test_a_skipped_phone_is_not_done_and_ready_names_it(instance):
    instance("done")
    browser = on_this_mac()
    page = browser.opens("/setup?phone=skipped")

    assert rail(page)[3] == ("phone", "todo")
    assert shown(page) == "ready"
    assert "/setup?step=phone" in [href for _, href in step_part(page).links("setup-not-done")]


def test_installing_omlx_from_the_ai_step_needs_the_macs_terminal(instance):
    """The AI step leads with the oMLX app (`test_setup_ai_step.py`); its
    other route is the terminal's, and the AI's tile follows that run."""
    instance("services")
    page = on_this_mac().opens("/setup?phone=signed")
    assert shown(page) == "ai"

    page = on_this_mac().opens("/setup?step=ai&ai=install")

    step = step_part(page)
    assert step.texts("setup-terminal-command") == ["./stack up ai"]
    assert tiles(page) == ["ai"]


def test_photos_and_documents_offer_the_three_and_start_all_of_them(instance):
    instance("signed")
    page = on_this_mac().opens("/setup?step=services")

    assert service_cards(page) == ["photos", "docs", "memory"]
    assert step_part(page).texts("setup-start-all")


def test_the_memory_is_started_from_its_service_card_and_code_shows_nowhere(instance):
    """Given Code is not set up, then the memory's service card offers Start: its
    run sets up Code first. Code keeps the memory's pages and has no tile,
    no step and no line on the page."""
    instance("signed")
    page = on_this_mac().opens("/setup?step=services")

    memory = re.search(r'<article class="service-card" data-service="memory".*?</article>',
                       page.body, re.S)[0]
    assert 'class="tile-add"' in memory
    assert 'data-id="code"' not in page.body
    assert "Forgejo" not in page.body


def test_with_everything_running_and_a_letter_filed_ready_leads_to_the_dashboard(instance):
    instance("done")
    page = on_this_mac().opens("/setup?phone=signed")

    assert rail(page) == [*[(s, "done") for s in STEPS[:-1]], ("ready", "current")]
    assert "/dashboard" in [href for _, href in step_part(page).links()]
    assert step_part(page).texts("setup-not-done") == []


def test_the_letter_waits_for_the_ai(instance):
    instance("services")
    page = on_this_mac().opens("/setup?step=try")

    assert rail(page)[6] == ("try", "todo")
    assert step_part(page).texts("setup-waiting") == []


# ── The page around the steps ────────────────────────────────────────────

def test_before_the_family_is_set_up_the_page_sends_the_admin_to_the_first_run(instance):
    instance("fresh", family={"name": "", "domain": "", "language": "en", "people": []})
    page = on_this_mac().opens("/setup")

    assert page.status in (302, 303, 307)


def test_when_the_stack_does_not_answer_check_again_stays_on_setup(instance, monkeypatch):
    instance("fresh")
    monkeypatch.setattr(client, "call", lambda request, timeout: {"error": "down"})
    page = on_this_mac().opens("/setup?step=ai")

    assert [href for _, href in page.links("fs-retry")] == ["/setup?step=ai"]


def test_the_page_uses_the_dashboards_files_and_events(instance):
    instance("fresh")
    scripts = on_this_mac().opens("/setup").scripts()

    assert "/dashboard/static/setup-page.js" in scripts
    assert all(src.startswith("/dashboard/static/") for src in scripts if src)


def test_in_german_the_page_speaks_german(instance):
    instance("fresh")
    english = on_this_mac().opens("/setup")
    german = on_this_mac("de").opens("/setup")

    assert '<html lang="de"' in german.body
    assert german.texts("setup-rail-title") != english.texts("setup-rail-title")
    assert rail(german) == rail(english)


def test_opening_the_page_starts_nothing(instance):
    host = instance("fresh")
    on_this_mac().opens("/setup")

    assert not [r for r in host.asked if r.get("cmd") == "up"]
