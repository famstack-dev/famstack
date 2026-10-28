"""The dashboard welcomes the family, by name, in the browser's language.

Whoever set the stack up opens the dashboard and is greeted as a
household: "Welcome home, Simpsons." The name is the family's
(`[core] stack_owner`), which the tools server reads through the host
API's `family` on every page, so a name set a moment ago is on the next
page without a restart. English addresses a family by its plural, German
as "Familie Simpson". Below the greeting, one sentence on what the Mac
now is, and three lines on how the stack works.

Driven through the tools server's FastAPI app and its access rule, as a
phone on the home network opens the page (the Browser DSL). The host API
is the stand-in, at its edge: `stack.live.client.call` answers `family`
as `stack family --json` would. The page reads complete without
JavaScript, so its text is read as served.
"""

from __future__ import annotations

import sys

import pytest

from .conftest import LIB, STACKLETS
from .dsl import Browser

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import PUBLIC_PORT  # noqa: E402
from stack.live import client  # noqa: E402


def family(name: str) -> dict:
    """What `stack family --json` shows for a household named `name`."""
    return {"name": name, "domain": "", "language": "en", "people": [
        {"id": "homer", "name": "Homer Simpson", "relation": "parent", "words": "he",
         "role": "admin", "email": "homer@home.local"}]}


@pytest.fixture
def opens(monkeypatch):
    """The dashboard as a phone opens it, for a household named `owner`."""
    import server

    def at(owner: str, language: str = "en"):
        monkeypatch.setattr(client, "call", lambda request, timeout: family(owner)
                            if request == {"cmd": "family"} else {"error": "not stood in"})
        phone = Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000", language=language)
        return phone.opens("/dashboard")

    return at


def test_the_family_is_welcomed_home_by_name(opens):
    assert opens("Simpson").texts("greeting") == ["Welcome home, Simpsons."]


def test_in_german_the_family_is_welcomed_as_german_addresses_it(opens):
    """German says "Familie Simpson", not "Simpsons"."""
    assert opens("Simpson", "de").texts("greeting") == ["Willkommen zu Hause, Familie Simpson."]


def test_a_surname_given_in_the_plural_is_not_pluralised_again(opens):
    assert opens("Simpsons").texts("greeting") == ["Welcome home, Simpsons."]


@pytest.mark.parametrize("language, greeting", [("en", "Welcome home."),
                                                ("de", "Willkommen zu Hause.")])
def test_without_a_name_the_welcome_has_no_hole_in_it(opens, language, greeting):
    """Instances installed before `stack_owner` existed have no name."""
    assert opens("", language).texts("greeting") == [greeting]


def test_the_welcome_says_what_the_mac_is_and_how_the_stack_works(opens):
    page = opens("Simpson")

    assert page.texts("lede") == [
        "This Mac is now your family's own server: the chat runs on it, and what you "
        "add next (photos, letters, memories) stays in this house too."]
    assert page.texts("how-title") == ["Talk in the chat", "Hand things over", "Find it here"]


def test_in_german_the_how_lines_are_german_too(opens):
    assert opens("Simpson", "de").texts("how-title") == ["Im Chat reden", "Dinge abgeben",
                                                         "Hier finden"]


def test_a_name_set_a_moment_ago_is_on_the_next_page(opens):
    """The name is read from the stack on every page, not from core's env,
    which only a restart of core would bring up to date."""
    assert opens("Simpson").texts("greeting") == ["Welcome home, Simpsons."]
    assert opens("Flanders").texts("greeting") == ["Welcome home, Flanders."]
