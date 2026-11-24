"""The bot runner puts the right people into each bot's room.

On every start the bot runner makes sure each bot's room exists and that
the bot and the people are in it. A bot's `bot.toml` says who belongs
there: `room_members = "parents"` makes it a room for the admins and the
parents (the Documents Room, where they drop letters); without it the
room is for the admins only (the Server Room). A child, a grandparent, an
`other` and a person without a relation join a parents' room only when an
admin invites them, because anyone in the Documents Room can search and
show every document. The runner only adds people. It removes nobody, so an
invited child stays; a member who left the room or was banned stays out,
and a room whose members are already right gets no new event, because the
runner starts on every restart. The people are the user IDs core renders
into the runner's environment from `users.toml`.

Synapse is stood in on localhost by `tests/synapse.py`. The bot runner's
`accounts` module is the real one, loaded as the framework loads stacklet
code, and the bots are the shipped declarations, read from their
`bot.toml` as the runner reads them.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from stack.stacklet_code import load
from tests.springfield import make_stack, write_instance
from tests.synapse import ADMIN_PASSWORD, Synapse

SERVER = "simpson"
STACKLETS = Path(__file__).resolve().parents[3] / "stacklets"
CORE = STACKLETS / "core"
ADMINS = ["homer"]
# Homer is the admin; Marge is the other parent. Bart, a child, and Grampa,
# a grandparent, have chat accounts too but are not on the parents' list.
PARENTS = ["homer", "marge"]


def _declared(stacklet: str) -> dict:
    """A shipped bot's declaration, with the fields the runner hands on."""
    decl = tomllib.loads((STACKLETS / stacklet / "bot" / "bot.toml").read_text())
    return {"id": decl["id"], "room": decl.get("room"), "room_topic": decl.get("room_topic"),
            "room_members": decl.get("room_members"), "join_rooms": decl.get("join_rooms") or []}


BOTS = [_declared("docs"), _declared("core")]


@pytest.fixture
def synapse(httpserver):
    """A stand-in Synapse with only the tech admin's account."""
    return Synapse(httpserver, server=SERVER)


def _start_bot_runner(httpserver, parents=PARENTS):
    """Set up the rooms as the bot runner does on every start."""
    accounts = load(CORE, CORE / "bot-runner" / "accounts.py")
    accounts.ensure_rooms(BOTS, httpserver.url_for("/"), SERVER, "stackadmin", ADMIN_PASSWORD,
                          admin_user_ids=ADMINS, parent_user_ids=parents)


def test_a_new_documents_room_holds_the_parents_and_the_server_room_the_admins(httpserver, synapse):
    """Given a fresh install, the Documents Room gets the admin and the parent,
    not the child or the grandparent; the Server Room only the admin."""
    _start_bot_runner(httpserver)

    assert synapse.members_of("documents") == {"stackadmin", "archivist-bot", "homer", "marge"}
    assert synapse.members_of("famstack") == {"stackadmin", "stacker-bot", "homer"}


def test_an_admins_only_documents_room_gets_the_missing_parent(httpserver, synapse):
    """Given a Documents Room from 0.4.x with the admin only, the parent is joined and nobody leaves."""
    synapse.has_room("documents", "homer", "archivist-bot")

    _start_bot_runner(httpserver)

    assert synapse.members_of("documents") == {"stackadmin", "homer", "archivist-bot", "marge"}


def test_a_parent_added_later_is_joined_on_the_next_start(httpserver, synapse):
    """Given a second parent is added to the family after the first start, the
    next start joins them to the Documents Room and not to the Server Room."""
    _start_bot_runner(httpserver)

    _start_bot_runner(httpserver, parents=[*PARENTS, "patty"])

    assert "patty" in synapse.members_of("documents")
    assert "patty" not in synapse.members_of("famstack")


def test_an_invited_child_stays_in_the_documents_room(httpserver, synapse):
    """Given an admin invited Bart and he joined, the next start keeps him in
    the room: the runner never removes anyone."""
    synapse.has_room("documents", "homer", "archivist-bot", "marge", "bart")

    _start_bot_runner(httpserver)

    assert "bart" in synapse.members_of("documents")


def test_a_second_start_changes_nothing(httpserver, synapse):
    """Given the rooms are already right, a restart writes no join and no power level."""
    _start_bot_runner(httpserver)
    synapse.writes.clear()

    _start_bot_runner(httpserver)

    assert synapse.writes == []


@pytest.mark.parametrize("membership", ["leave", "ban"])
def test_a_parent_who_left_or_was_banned_stays_out(httpserver, synapse, membership):
    """Given Marge left the Documents Room or was banned from it, the next start does not join her again."""
    _start_bot_runner(httpserver)
    synapse.left("documents", "marge", membership)

    _start_bot_runner(httpserver)

    assert "marge" not in synapse.members_of("documents")


def test_an_admin_who_left_is_joined_again(httpserver, synapse):
    """Given Homer, an admin, left the Documents Room, the next start joins him again, as before."""
    _start_bot_runner(httpserver)
    synapse.left("documents", "homer")

    _start_bot_runner(httpserver)

    assert "homer" in synapse.members_of("documents")


def test_core_renders_the_parents_with_a_chat_account_for_the_runner(tmp_path):
    """Given an admin, a parent, a parent without a chat account, a child, a
    grandparent and a person without a relation, core's environment for the
    runner lists the admin and the parent with a chat account as the parents."""
    instance = write_instance(tmp_path)
    (instance / "users.toml").write_text(
        '[[users]]\nid = "homer"\nname = "Homer"\nrole = "admin"\nrelation = "parent"\n'
        '[[users]]\nid = "marge"\nname = "Marge"\nrole = "member"\nrelation = "parent"\nstacklets = ["messages"]\n'
        '[[users]]\nid = "patty"\nname = "Patty"\nrole = "member"\nrelation = "parent"\nstacklets = ["photos"]\n'
        '[[users]]\nid = "bart"\nname = "Bart"\nrole = "member"\nrelation = "child"\nstacklets = ["messages"]\n'
        '[[users]]\nid = "abe"\nname = "Abe"\nrole = "member"\nrelation = "grandparent"\nstacklets = ["messages"]\n'
        '[[users]]\nid = "ned"\nname = "Ned"\nrole = "member"\nstacklets = ["messages"]\n')

    env = make_stack(instance).env("core")

    assert env["STACK_PARENT_USER_IDS"] == "homer,marge"
    assert env["STACK_ADMIN_USER_IDS"] == "homer"
