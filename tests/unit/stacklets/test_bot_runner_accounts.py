"""The bot runner creates a missing bot account and leaves an existing one alone.

On startup the bot runner sets up a chat account for each bot that has no
saved session. It asks Synapse whether the account exists, and only a
"not found" makes it create the account with the stored password.

Synapse's admin API is an upsert: a `PUT /_synapse/admin/v2/users/<id>`
that carries `password` sets that password and signs out every device of
the account. So any other answer to the check (a 5xx from a busy server,
a timeout) must change nothing, and the runner says that it could not
check the account.

Synapse is stood in on localhost by `tests/synapse.py`. The bot runner's
`accounts` module is the real one, loaded as the framework loads
stacklet code.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger

from stack.stacklet_code import load
from tests.synapse import ADMIN_PASSWORD, Synapse

SERVER = "simpson"
CORE = Path(__file__).resolve().parents[3] / "stacklets" / "core"
BOT = {"id": "archivist-bot", "name": "Archivist", "password": "stored-bot-pass"}
FULL = f"@archivist-bot:{SERVER}"


@pytest.fixture
def synapse(httpserver):
    """A stand-in Synapse with only the tech admin's account."""
    return Synapse(httpserver, server=SERVER)


@pytest.fixture
def warnings():
    """The warnings the bot runner logs while the test runs."""
    records: list[str] = []
    sink = logger.add(lambda m: records.append(str(m)), level="WARNING")
    yield records
    logger.remove(sink)


def _start_bot_runner(httpserver):
    """Set up the bot accounts as the bot runner does on startup."""
    accounts = load(CORE, CORE / "bot-runner" / "accounts.py")
    return accounts.setup_bot_accounts(
        [BOT], httpserver.url_for("/"), SERVER, "stackadmin", ADMIN_PASSWORD)


def test_a_missing_bot_account_is_created_with_the_stored_password(httpserver, synapse):
    """Given Synapse answers 404 for the bot, the runner creates it with its stored password."""
    _start_bot_runner(httpserver)

    assert synapse.passwords[FULL] == BOT["password"]


def test_an_existing_bot_account_keeps_its_password(httpserver, synapse):
    """Given the bot's account exists, the runner sends it no password and signs out nothing."""
    synapse.has_account("archivist-bot", "password-of-the-live-session")

    _start_bot_runner(httpserver)

    assert synapse.passwords[FULL] == "password-of-the-live-session"
    assert FULL not in synapse.logouts


def test_a_failed_account_check_changes_nothing_and_is_reported(httpserver, synapse, warnings):
    """Given Synapse answers the account check with 500, the runner changes no account and names the bot.

    A busy Synapse at startup gives no answer. Taking that for "no
    account" would reset the bot's password and sign out its live session.
    """
    synapse.has_account("archivist-bot", "password-of-the-live-session")
    synapse.fails_account_reads()

    _start_bot_runner(httpserver)

    assert synapse.passwords[FULL] == "password-of-the-live-session"
    assert FULL not in synapse.logouts, "the bot's session was signed out"
    assert any("archivist-bot" in w for w in warnings), "the failed check is not reported"
