"""Whose change a commit is, read from its subject, author and committer.

Every bot that writes to the memory starts its commit subject with a
prefix from one closed table (ADR-018 §6). The history reads the prefix
as the kind of change, and decides from the kind, the author and the
committer whether a person made the change or the machine did. That
decision matters twice: the dashboard names the person for their
changes, and a rebuild keeps a person's changes to a card (the card
conversation). The machine's work read as a person's would be shown as
something they did, and replayed as their edit.

Driven over `GET /history` of a real cards API, against a real git
repository whose commits carry the subjects and accounts the writers
use. The data is the Simpsons'.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from datetime import datetime, timedelta

import pytest

from .conftest import LIB, STACKLETS
from .dsl import Memory

for _path in (LIB, STACKLETS / "memory"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from stack.card_commits import SUBJECTS, commit_subject, kind_of  # noqa: E402
from cards_api import CardsAPI, serve  # noqa: E402

NOW = datetime.now().astimezone().replace(microsecond=0)

TENT = "---\ntype: note\ncard_id: note-7f3a9c21e4\ntitle: Tent is torn\n---\n\n# Tent is torn\n"
CAMPING = "---\ntype: list\ntitle: Camping\n---\n\n- [ ] Tent\n"
KWIK_E_MART = "---\ntype: correspondent\ntitle: Kwik-E-Mart\n---\n"


@pytest.fixture
def memory(tmp_path) -> Memory:
    m = Memory.new(tmp_path / "vault")
    m.changes("capture: Tent is torn", by="homer", committed_by="archivist-bot",
              at=NOW - timedelta(hours=2), files={"family/camping/tent.md": TENT})
    return m


def latest(memory: Memory) -> dict:
    """The newest change `GET /history` reports for `memory`."""
    server = serve(CardsAPI(vault=memory.repo), host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/history") as r:
            return json.loads(r.read())["changes"][0]
    finally:
        # `shutdown` waits out the server's half-second poll; nothing needs to wait for it.
        threading.Thread(target=server.shutdown, daemon=True).start()


def test_list_items_the_model_drew_from_a_note_are_the_machines(memory):
    """Given Homer's note about the tent, when the archivist puts its
    action items on the camping list in his name, then the change is the
    machine's: Homer did not edit the list, the model read his note."""
    memory.changes("lists: add action items to camping", by="homer", committed_by="archivist-bot",
                   at=NOW - timedelta(hours=1), files={"family/lists/camping.md": CAMPING})

    assert latest(memory)["machine"] is True


def test_a_correspondent_the_archivist_declared_is_the_machines(memory):
    """Given a document from the Kwik-E-Mart, when the archivist declares
    the correspondent's card, then the change is the machine's, of a kind
    of its own."""
    memory.changes("declare: family/correspondents/kwik-e-mart.md", by="archivist-bot",
                   at=NOW - timedelta(hours=1),
                   files={"family/correspondents/kwik-e-mart.md": KWIK_E_MART})

    change = latest(memory)
    assert (change["kind"], change["machine"]) == ("declared", True)


def test_a_new_transcript_is_the_machines(memory):
    """Given Homer's note, when a better transcriber replaces its quote's
    text, then the change is the machine's, though the card is Homer's."""
    memory.changes("transcribe: Tent is torn", by="homer", committed_by="memory-bot",
                   at=NOW - timedelta(hours=1), files={"family/camping/tent.md": TENT + "\n"})

    change = latest(memory)
    assert (change["kind"], change["machine"]) == ("transcribed", True)


def test_a_hand_edit_is_the_persons(memory):
    """Given Homer's note, when Marge edits it in Forgejo's web editor,
    which writes its own subject, then the change is hers: a person's
    commit with a subject outside the table is a hand edit (ADR-018 §4)."""
    memory.changes("Update family/camping/tent.md", by="marge",
                   at=NOW - timedelta(hours=1), files={"family/camping/tent.md": TENT + "\nOld tarp.\n"})

    change = latest(memory)
    assert (change["who"], change["kind"], change["machine"]) == ("marge", "changed", False)


def test_every_subject_a_bot_can_write_has_a_kind_of_its_own():
    """A bot builds its subject from the table, so whatever a writer
    produces, the history can place it; only a hand edit reads as
    `changed`."""
    assert all(kind_of(commit_subject(prefix, "Tent is torn"), {}) != "changed" for prefix in SUBJECTS)


def test_a_subject_outside_the_table_is_refused():
    """A writer that invents a prefix fails where it writes, not later,
    when a rebuild reads its commit as somebody's edit."""
    with pytest.raises(ValueError):
        commit_subject("tidy", "Tent is torn")
