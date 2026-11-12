"""Running `stack messages setup` again keeps each person's chat password.

The setup runs again whenever the admin adds a person to the family, so a
rerun is a normal step. It creates the accounts that are missing, with the
password the stack stored for them, and leaves every existing account
alone: its password and its signed-in devices stay as they are. The output
lists a password only for an account this run created.

Synapse's admin API is an upsert: a `PUT /_synapse/admin/v2/users/<id>`
that carries `password` sets that password and signs out every device of
the account, even when it is the same password. So "leave it alone" means
the PUT for an existing person carries no password.

Synapse is stood in on localhost by `tests/synapse.py`. The command and
its Matrix client are the real ones.
"""

from __future__ import annotations

import pytest

from stacklets.messages.cli import setup
from tests.synapse import ADMIN_PASSWORD, Synapse

SERVER = "simpson"
BOT_PASSWORD = "stacker-pass"

FAMILY = [
    {"id": "homer", "name": "Homer", "role": "admin"},
    {"id": "marge", "name": "Marge", "role": "member", "stacklets": ["messages"]},
]
STORED = {
    "global__ADMIN_PASSWORD": ADMIN_PASSWORD,
    "core__STACKER_BOT_PASSWORD": BOT_PASSWORD,
    "global__USER_HOMER_PASSWORD": "donut-duff-couch-nap",
    "global__USER_MARGE_PASSWORD": "blue-hair-pork-chop",
}


# ── Running the command ──────────────────────────────────────────────────

@pytest.fixture
def synapse(httpserver):
    """A stand-in Synapse with only the tech admin's account."""
    return Synapse(httpserver, server=SERVER)


def _setup_again(httpserver, tmp_path, users=FAMILY):
    """Run `stack messages setup` against the stand-in; its result rows by item."""
    config = {
        "instance_dir": str(tmp_path),
        "stack": {"messages": {"server_name": SERVER}},
        "manifest": {"ports": {"synapse": httpserver.port}},
        "users": users,
        "secrets": dict(STORED),
    }
    result = setup.run([], None, config)
    assert result.get("ok"), result
    return {row["item"]: row["action"] for row in result["results"]}


# ── Behaviour ────────────────────────────────────────────────────────────

def test_an_existing_account_keeps_its_password_and_devices(httpserver, synapse, tmp_path):
    """Given Homer's account exists, a rerun sends him no password and says it exists."""
    synapse.has_account("homer", STORED["global__USER_HOMER_PASSWORD"])

    rows = _setup_again(httpserver, tmp_path)

    assert synapse.passwords["@homer:simpson"] == STORED["global__USER_HOMER_PASSWORD"]
    assert "@homer:simpson" not in synapse.logouts, "Homer's devices were signed out"
    assert "exists" in rows["@homer:simpson"]
    assert STORED["global__USER_HOMER_PASSWORD"] not in rows["@homer:simpson"]


def test_a_password_changed_in_element_is_kept(httpserver, synapse, tmp_path):
    """Given Homer chose his own password in Element, a rerun keeps it."""
    synapse.has_account("homer", "mmm-forbidden-donut")

    _setup_again(httpserver, tmp_path)

    assert synapse.passwords["@homer:simpson"] == "mmm-forbidden-donut"
    assert not synapse.logouts


def test_a_new_person_gets_the_stored_password_listed_once(httpserver, synapse, tmp_path):
    """Given Marge was added since the last run, she gets her stored password, the only one shown."""
    synapse.has_account("homer", STORED["global__USER_HOMER_PASSWORD"])

    rows = _setup_again(httpserver, tmp_path)

    marge = STORED["global__USER_MARGE_PASSWORD"]
    assert synapse.passwords["@marge:simpson"] == marge
    assert marge in rows["@marge:simpson"]
    listed = [action for action in rows.values() if "password" in action]
    assert listed == [rows["@marge:simpson"]], "only the account made in this run shows a password"


def test_the_bot_and_the_rooms_are_as_before(httpserver, synapse, tmp_path):
    """Resting state: the bot keeps its session and the four rooms are there, none made twice."""
    synapse.has_account("homer", STORED["global__USER_HOMER_PASSWORD"])
    synapse.has_account("stacker-bot", BOT_PASSWORD)

    rows = _setup_again(httpserver, tmp_path)

    assert rows["@stacker-bot:simpson"] == "ready"
    assert "@stacker-bot:simpson" not in synapse.logouts
    assert set(synapse.rooms) == {f"#{a}:simpson" for a in ("family", "famchat", "memories", "famstack")}
