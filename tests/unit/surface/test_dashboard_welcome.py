"""The dashboard welcomes the family, by name, in the browser's language.

Whoever set the stack up opens the dashboard and is greeted as a
household: "Welcome home, Simpsons." The name is the surname the
installer asked for (`[core] stack_owner`), which reaches core's tools
server as `STACK_OWNER` in its env. English addresses a family by its
plural, German as "Familie Simpson". Below the greeting, one sentence
on what the Mac now is, and three lines on how the stack works.

Driven through the tools server's FastAPI app and its access rule, as a
phone on the home network opens the page (the Browser DSL). The page
reads complete without JavaScript, so its text is read as served.
"""

from __future__ import annotations

import sys

import pytest

from .conftest import LIB, STACKLETS, make_stack, write_instance
from .dsl import Browser

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import PUBLIC_PORT  # noqa: E402


@pytest.fixture
def opens(monkeypatch):
    """The dashboard as a phone opens it, for a household named `owner`."""
    import server

    def at(owner: str, language: str = "en"):
        if owner:
            monkeypatch.setenv("STACK_OWNER", owner)
        else:
            monkeypatch.delenv("STACK_OWNER", raising=False)
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


def test_the_name_reaches_the_tools_server_from_stack_toml(tmp_path):
    """`[core] stack_owner` is rendered into core's env, which the tools
    server reads; the household's surname as the installer asked for it."""
    instance = write_instance(tmp_path)
    toml = instance / "stack.toml"
    toml.write_text(toml.read_text().replace("[core]\n", '[core]\nstack_owner = "Simpson"\n', 1))

    assert make_stack(instance).env("core")["STACK_OWNER"] == "Simpson"
