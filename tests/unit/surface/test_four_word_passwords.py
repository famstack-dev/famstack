"""A new person's first password: four words from a fixed list, never the username.

`stack family set` gives each person without a stored password one, and
the services make their accounts with it. It used to be the person's
username, the first name in lowercase, which every device at home could
read from the family list. Now it is four words from EFF's short word
list joined by "-" ("otter-maple-cloud-lamp"), about 41 bits. A password
already stored is never changed, the old first password included. The
Server room, which every admin's devices keep, names the login and says
where the password is, never the password itself.

The family is set the way an admin sets it, through `./stack family set`
(`cli.main`), over a fresh instance in `tmp_path`; the secrets are read
back from `.stack/secrets.toml` as the services read them.
"""

from __future__ import annotations

import re
import stat
from importlib import resources

import pytest

from stack import cli
from tests.springfield import make_stack, write_instance
from tests.unit.surface import test_family
from tests.unit.surface.test_family import family_file, secrets, stack, users

SIMPSONS = """\
name = "Simpson"
domain = "simpsons.com"

[[people]]
name = "Homer Simpson"

[[people]]
name = "Marge Simpson"

[[people]]
name = "Bart Simpson"
"""

# A checkout with stack.toml as a fresh install leaves it, and no one in it.
instance = test_family.instance

WITH_LISA = SIMPSONS + """
[[people]]
name = "Lisa Simpson"
"""


def word_list() -> list[str]:
    """The words the list ships, read as a reader of the file would: one
    per line after the header's comment lines, the dice number first."""
    text = resources.files("stack").joinpath("words/eff_short_wordlist_1.txt").read_text()
    return [line.split()[-1] for line in text.splitlines() if line.strip() and not line.startswith("#")]


def is_four_words(password: str) -> bool:
    parts = password.split("-")
    return len(parts) == 4 and all(p in set(word_list()) for p in parts)


# ── The word list ─────────────────────────────────────────────────────────

def test_the_word_list_has_1295_unique_words_of_a_to_z():
    """Given the shipped list, then it holds 1295 different words of a to
    z only: EFF's 1296 without "yo-yo", which repeats "yoyo". Four of
    them are about 41 bits, and "-" can only join the words when none of
    them carries one."""
    words = word_list()
    assert len(words) == 1295
    assert len(set(words)) == 1295
    assert all(re.fullmatch(r"[a-z]+", w) for w in words)


# ── A new person gets four words ──────────────────────────────────────────

def test_a_new_person_gets_four_words_from_the_list(instance, capfd, monkeypatch):
    """Given a family set for the first time, then each person's stored
    password is four words of the list joined by "-", and no one's is
    their username or another person's."""
    code, out, _ = stack(capfd, monkeypatch, "family", "set", "--from",
                         family_file(instance, SIMPSONS), "--json")

    assert code == 0, out
    stored = secrets(instance)
    passwords = [stored[f"global__USER_{p.upper()}_PASSWORD"] for p in ("homer", "marge", "bart")]
    assert all(is_four_words(p) for p in passwords), passwords
    assert "homer" not in passwords and "bart" not in passwords
    assert len(set(passwords)) == 3


def test_a_stored_password_stays_and_only_the_new_person_gets_words(instance, capfd, monkeypatch):
    """Given Homer, Marge and Bart with the old first password (their
    username) stored, when the family is set again with Lisa added, then
    the three keep theirs and only Lisa gets four words: a stored
    password is never changed, because the services' accounts take it."""
    stack(capfd, monkeypatch, "family", "set", "--from", family_file(instance, SIMPSONS), "--json")
    path = instance / ".stack" / "secrets.toml"
    path.write_text("".join(f'global__USER_{p.upper()}_PASSWORD = "{p}"\n'
                            for p in ("homer", "marge", "bart")))

    code, out, _ = stack(capfd, monkeypatch, "family", "set", "--from",
                         family_file(instance, WITH_LISA), "--json")

    assert code == 0, out
    stored = secrets(instance)
    assert [stored[f"global__USER_{p.upper()}_PASSWORD"] for p in ("homer", "marge", "bart")] == \
        ["homer", "marge", "bart"]
    assert is_four_words(stored["global__USER_LISA_PASSWORD"])


def test_a_person_left_without_a_password_by_a_stopped_run_gets_words(instance, capfd, monkeypatch):
    """Given users.toml written by a run that stopped before the secrets,
    when the family is set again, then the people get four words, never
    their username."""
    stack(capfd, monkeypatch, "family", "set", "--from", family_file(instance, SIMPSONS), "--json")
    (instance / ".stack" / "secrets.toml").unlink()
    assert set(users(instance)) == {"homer", "marge", "bart"}

    code, out, _ = stack(capfd, monkeypatch, "family", "set", "--from",
                         family_file(instance, SIMPSONS), "--json")

    assert code == 0, out
    assert is_four_words(secrets(instance)["global__USER_HOMER_PASSWORD"])


def test_a_family_with_every_password_stored_changes_nothing(instance, capfd, monkeypatch):
    """Resting state: given every person's password stored, when the same
    family is set again, then no secret changes and the file keeps mode
    0600."""
    stack(capfd, monkeypatch, "family", "set", "--from", family_file(instance, SIMPSONS), "--json")
    path = instance / ".stack" / "secrets.toml"
    before = path.read_bytes()

    code, out, _ = stack(capfd, monkeypatch, "family", "set", "--from",
                         family_file(instance, SIMPSONS), "--json")

    assert code == 0, out
    assert path.read_bytes() == before
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ── The Server room gets no password ──────────────────────────────────────

@pytest.mark.parametrize("stacklet", ["photos", "docs", "chatai"])
def test_the_server_room_welcome_names_the_login_and_holds_no_password(tmp_path, monkeypatch, stacklet):
    """Given photos, documents or ChatAI started for the first time, then
    the welcome for the Server room names Homer's login and holds no
    password: the chat keeps every message on every admin's devices.

    The welcome is posted by `./stack messages send`, a process of its
    own; the test stands in for it at that boundary and reads what would
    have been sent."""
    simpsons = write_instance(tmp_path, secrets={"global__USER_HOMER_PASSWORD": "otter-maple-cloud-lamp"})
    sent: list[str] = []
    monkeypatch.setattr(cli, "_notify", lambda _stck, message: sent.append(message))

    cli._notify_up(make_stack(simpsons), {"stacklet": stacklet, "name": stacklet, "first_run": True,
                                          "port": 42010})

    assert len(sent) == 1
    assert "otter-maple-cloud-lamp" not in sent[0]
    assert "homer" in sent[0]


# ── The pages show the stored password ────────────────────────────────────
#
# Driven through the tools server's app as browsers open it (the Browser
# DSL), with the host API the dashboard's shared stand-in, which stores
# `PASSWORDS` for the Simpsons and answers `passwords` as the host API
# does.

def _shown_passwords(page) -> list[str]:
    """The passwords a page holds, in order (masked by the script, but in the page)."""
    return re.findall(r"<code[^>]*data-secret[^>]*>([^<]*)</code>", page.body)


@pytest.fixture
def stack_state(tmp_path, monkeypatch, httpserver, docker_with_no_containers):
    """Put the Simpsons' stack in a named state: `stack_state("chat")`,
    core's links on the Mac's home-network address, where a phone's QR
    code points. No phone reach is left for the next test."""
    from tests.unit.surface.setup_stand_in import stack_in_state

    import phone_reach  # on the path through setup_stand_in
    monkeypatch.setenv("LINK_BASE_URL", "http://192.168.1.20:42000/go")
    phone_reach.forget()
    yield stack_in_state(tmp_path, monkeypatch, httpserver)
    phone_reach.forget()


def test_on_the_mac_the_chat_s_service_card_shows_the_stored_password(stack_state):
    """Given the chat runs, on the Mac's address, then the chat's service
    card shows Homer's stored password, never his username."""
    from tests.dashboard_host import PASSWORDS
    from tests.unit.surface.setup_stand_in import on_this_mac
    stack_state("chat")

    page = on_this_mac().opens("/setup?step=chat")

    card = re.search(r'<article class="service-card" data-service="messages".*?</article>', page.body, re.S)
    assert _shown_passwords(type(page)(200, "text/html", card[0])) == [PASSWORDS["homer"]]


def test_on_the_mac_the_family_step_shows_each_person_s_password(stack_state):
    """Given the family is set, on the Mac's address, then the setup's
    family step names each person with their own stored password, in the
    family's order."""
    from tests.dashboard_host import FAMILY, PASSWORDS
    from tests.unit.surface.setup_stand_in import on_this_mac
    stack_state("chat")

    page = on_this_mac().opens("/setup?step=family")

    people = re.search(r'<ul class="setup-people">.*?</ul>', page.body, re.S)
    assert _shown_passwords(type(page)(200, "text/html", people[0])) == \
        [PASSWORDS[p["id"]] for p in FAMILY["people"]]


def test_a_paired_phone_shows_its_own_person_s_password_only(stack_state):
    """Given Marge's paired phone, then the sign-in guide holds Marge's
    stored password and no one else's."""
    from tests.dashboard_host import PASSWORDS
    from tests.unit.surface.setup_stand_in import paired_phone
    stack_state("chat", mac="192.168.1.20")

    page = paired_phone("marge").opens("/go/app/sign-in")

    assert _shown_passwords(page) == [PASSWORDS["marge"]]


def test_the_phone_page_shows_the_own_password_only_at_the_end_of_pairing(stack_state):
    """Given the Mac's QR code for Marge, when her phone opens it, then
    the page that pairs it ("This phone is now Marge's") shows Marge's
    stored password, once: she types it into the chat app right then."""
    from tests.dashboard_host import PASSWORDS
    from tests.unit.surface.setup_stand_in import on_this_mac

    import server  # on the path through setup_stand_in
    from access import PUBLIC_PORT
    from tests.unit.surface.dsl import Browser
    stack_state("chat", mac="192.168.1.20")
    code = on_this_mac().opens("/setup/add-a-phone?person=marge")
    pairing = re.search(r'aria-label="QR code: https?://[^/"]+([^"]+)"', code.body)[1].replace("&amp;", "&")
    phone = Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000", paired=False)

    page = phone.opens(pairing)

    assert _shown_passwords(page) == [PASSWORDS["marge"]]


def test_a_later_visit_of_the_phone_page_links_to_the_sign_in_guide(stack_state):
    """Given Marge's paired phone, when it opens the phone page again,
    then it shows Marge's username and "Your sign-in" to the sign-in
    guide, and no password: the guide is the one page on the home address
    that carries a person's password."""
    from tests.dashboard_host import PASSWORDS
    from tests.unit.surface.setup_stand_in import paired_phone
    stack_state("chat", mac="192.168.1.20")

    page = paired_phone("marge").opens("/go/app")

    assert re.findall(r'href="([^"]*)">Your sign-in', page.body) == ["/go/app/sign-in"]
    assert "data-secret" not in page.body and PASSWORDS["marge"] not in page.body
    assert "marge" in page.body


def test_a_shared_device_gets_no_password(stack_state):
    """Given the chat runs, then a device paired for the household gets
    no page with a password, and the host API is never asked for one:
    a shared device belongs to no one person."""
    from tests.dashboard_host import PASSWORDS
    from tests.unit.surface.setup_stand_in import phone
    host = stack_state("chat", mac="192.168.1.20")

    pages = [phone().opens(path) for path in ("/go/app", "/go/app/sign-in", "/dashboard")]

    assert not any(pw in page.body for page in pages for pw in PASSWORDS.values())
    assert not [r for r in host.asked if r.get("cmd") == "passwords"]


def _cards(page) -> dict[str, str]:
    """Each service card of a page, its markup by its service."""
    found = re.findall(r'(<article class="service-card" data-service="(\w+)".*?</article>)', page.body, re.S)
    return {sid: html for html, sid in found}


def test_a_paired_device_s_service_cards_link_to_its_own_sign_in(stack_state):
    """Given Marge's paired phone and the chat running, then the chat's
    service card names Marge's own username and links "Your sign-in" to
    the sign-in guide, with no password: the guide is the one page on the
    home address that carries one. Documents, which only admins sign in
    to, gives Marge no sign-in."""
    from tests.unit.surface.setup_stand_in import paired_phone
    stack_state("services", mac="192.168.1.20")

    cards = _cards(paired_phone("marge").opens("/dashboard"))

    chat = cards["messages"]
    assert re.findall(r'<code class="setup-value"[^>]*>([^<]*)</code>', chat)[-1] == "marge"
    assert re.findall(r'href="([^"]*)">Your sign-in', chat) == ["/go/app/sign-in"]
    assert "data-secret" not in chat and "homer" not in chat
    assert "/go/app/sign-in" not in cards["docs"] and "setup-creds" not in cards["docs"]


def test_a_shared_device_s_service_cards_name_no_one(stack_state):
    """Given a device paired for the household, then no service card
    names a username or links to a sign-in guide."""
    from tests.unit.surface.setup_stand_in import phone
    stack_state("services", mac="192.168.1.20")

    cards = _cards(phone().opens("/dashboard"))

    assert cards
    assert not any("/go/app/sign-in" in c or "setup-creds" in c for c in cards.values())


def test_every_page_that_carries_a_password_is_never_cached(stack_state):
    """Given the chat runs, then the Mac's setup page, dashboard and the
    pills' JSON, and the phone page and sign-in guide on a paired phone
    are sent with `Cache-Control: no-store`: a browser keeps no copy of a
    page with a password, on disk or for the back button. About two
    seconds: it pairs a phone and opens five pages, the dashboard twice."""
    from tests.unit.surface.setup_stand_in import on_this_mac, paired_phone
    stack_state("chat", mac="192.168.1.20")
    mac, homers = on_this_mac(), paired_phone("homer")

    pages = [mac.opens("/setup?step=family"), mac.opens("/dashboard"),
             homers.opens("/go/app"), homers.opens("/go/app/sign-in")]
    pills = mac.client.get("/dashboard/api/stack", headers={"host": mac.host})

    assert [p.headers.get("cache-control") for p in pages] == ["no-store"] * 4
    assert pills.headers.get("cache-control") == "no-store"


def test_stack_up_prints_the_admin_s_stored_password(tmp_path, capsys):
    """Given Homer's password stored, when a stacklet with a login comes
    up, then the terminal prints his login and that password, read from
    the instance the command runs on."""
    simpsons = write_instance(tmp_path, secrets={"global__USER_HOMER_PASSWORD": "otter-maple-cloud-lamp"})

    cli.print_up_success({"stacklet": "photos", "name": "Photos", "manifest": {"login_field": "username"}},
                         make_stack(simpsons))

    assert "otter-maple-cloud-lamp" in capsys.readouterr().out


#
# A password changed in an app counts there. famstack shows the first
# password it stored and cannot know whether a person changed it in an
# app since, so each page that shows a password says so once, under it.

GUIDE_HINT = {"en": "Changed it in the app? Use yours.", "de": "In der App geändert? Dann gilt deins."}
FAMILY_HINT = {"en": "Changed a password in an app? Then that one counts there.",
               "de": "Ein Passwort in der App geändert? Dann gilt dort das neue."}


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_sign_in_guide_says_once_under_the_password_that_a_changed_one_counts(stack_state, language):
    """Given Homer's paired device, when it opens the sign-in guide, then
    one line under his password says a password changed in the app counts."""
    from tests.unit.surface.setup_stand_in import paired_phone
    stack_state("chat", mac="192.168.1.20")

    page = paired_phone("homer", language=language).opens("/go/app/sign-in")

    assert page.body.count(GUIDE_HINT[language]) == 1
    assert page.body.index(GUIDE_HINT[language]) > page.body.index("data-secret")


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_family_step_says_once_under_the_list_that_a_changed_password_counts(stack_state, language):
    """Given the family step on the Mac's address, with every person's
    password, then one line under the list says a password changed in an
    app counts there."""
    from tests.unit.surface.setup_stand_in import on_this_mac
    stack_state("chat")

    page = on_this_mac(language).opens("/setup?step=family")

    assert page.body.count(FAMILY_HINT[language]) == 1
    assert page.body.index(FAMILY_HINT[language]) > page.body.index("</ul>", page.body.index("setup-people"))


def test_a_page_without_a_password_has_no_line_about_a_changed_one(stack_state):
    """Given a device paired for the household, then none of its pages
    shows a password, and none says a changed one counts."""
    from tests.unit.surface.setup_stand_in import phone
    stack_state("chat", mac="192.168.1.20")

    pages = [phone().opens(path) for path in ("/go/app", "/go/app/sign-in", "/dashboard", "/setup?step=family")]

    assert not [p for p in pages if GUIDE_HINT["en"] in p.body or FAMILY_HINT["en"] in p.body]
