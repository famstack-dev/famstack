"""The messages stacklet's one setup question: the Matrix server name.

Every Matrix user ID ends in the server name (`@homer:simpson`), and it
cannot change once Synapse has started. So its form explains that, refuses
a name that is not a host name, and in a terminal asks once more before
keeping it. These tests run `./stack` in this process on an instance that
has no server name yet.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.parametrize("server_name", [None])


def test_an_answer_given_as_a_flag_is_written(stack_cli):
    ran = stack_cli("configure", "messages", "--answer", "server_name=simpson")

    assert ran.code == 0, ran.err
    assert 'server_name = "simpson"' in stack_cli.toml()


def test_without_the_answer_up_names_the_form_and_the_field(stack_cli):
    """Instead of "run this command interactively"."""
    ran = stack_cli("up", "messages")

    assert ran.code == 1
    assert "messages.server-name" in ran.err and "server_name" in ran.err
    assert "interactively" not in ran.err
    assert "server_name" not in stack_cli.toml()


def test_a_name_that_is_not_a_host_name_is_refused_with_the_reason(stack_cli):
    ran = stack_cli("configure", "messages", "--json", "--answer", "server_name=The Simpsons")

    assert ran.answer["form"]["errors"] == {"server_name": (
        "Use lowercase letters, digits, dots and hyphens, like smiths or home.internal.")}


def test_the_form_says_the_name_is_permanent(stack_cli):
    form = stack_cli("configure", "messages", "--json").answer["form"]

    assert "@homer:familyname" in form["text"]
    assert "cannot be changed later" in form["text"]


def test_in_a_terminal_it_explains_refuses_and_asks_before_keeping_the_name(
        stack_cli, monkeypatch):
    """Given a terminal, it explains, refuses a name with a space and says
    why, and asks before keeping the permanent name; a no asks again."""
    typed = iter(["The Simpsons", "smiths", "n", "simpson", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(typed))

    ran = stack_cli("configure", "messages", terminal=True)

    assert ran.code == 0, ran.err
    assert "this part is permanent" in ran.out
    assert "Use lowercase letters, digits, dots and hyphens" in ran.out
    assert "Your user IDs will look like: @you:simpson" in ran.out
    assert 'server_name = "simpson"' in stack_cli.toml()
