"""What changed in the memory lately, as a page reads it from the cards API.

The dashboard's activity widget and the card browser's "recently" list
read `GET /history` from the memory stacklet. Driven here over real HTTP
(`serve(CardsAPI(...))`) against a real git repository whose commits are
made by the people and bots that make them on an instance, with the
subjects and trailers those writers use. Nothing is patched.

What to assert: what a page gets back. Each change says when, who (and
whether that is a person or a bot), what kind of change it was, and the
cards it touched with the link that opens each. The commit's own wording
is the memory's business; a page reads `kind`.

The data is the Simpsons'.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from datetime import datetime, timedelta

import pytest

from tests.memory.unit.dsl import Memory


from stacklets.memory.cards_api import CardsAPI, serve  # noqa: E402

NOW = datetime.now().astimezone().replace(microsecond=0)


def person(title: str) -> str:
    return f"---\ntype: person\ntitle: {title}\n---\n"


def note(card_id: str, title: str) -> str:
    return f"---\ntype: note\ncard_id: {card_id}\ntitle: {title}\n---\n\n# {title}\n"


SIDECAR = json.dumps({"card_id": "note-7f3a9c21e4", "filed_by": "homer", "sources": []})
CAMPING = "---\ntype: list\ntitle: Camping\n---\n\n- [ ] Tent\n"
TENT = note("note-7f3a9c21e4", "Tent is torn")
WATERCOLOUR = note("note-0b1c2d3e4f", "Bart's watercolour")


@pytest.fixture(scope="module")
def memory(tmp_path_factory) -> Memory:
    """A week of the Simpsons' memory, oldest first, written the way an
    instance writes it: the memory bot adds the people, Homer and Marge
    file and reply, a migration and a refile run as the memory bot."""
    m = Memory.new(tmp_path_factory.mktemp("springfield") / "vault")
    m.changes("people: add a file for each chat account", by="memory-bot", at=NOW - timedelta(days=6),
              files={"family/people/homer.md": person("Homer Simpson"),
                     "family/people/marge.md": person("Marge Simpson")})
    m.changes("chore(lists): homer added Tent to Camping", by="homer", at=NOW - timedelta(days=4),
              files={"family/lists/camping.md": CAMPING})
    m.changes("capture: Tent is torn", by="homer", at=NOW - timedelta(days=1),
              files={"family/camping/tent.md": TENT, "family/camping/tent.json": SIDECAR})
    m.changes("capture: Bart's watercolour", by="marge", at=NOW - timedelta(hours=5),
              files={"marge/notes/watercolour.md": WATERCOLOUR})
    m.changes("cards: give 3 card(s) an id of their own", by="memory-bot", at=NOW - timedelta(hours=3),
              files={"family/people/homer.md": person("Homer Simpson") + "\n",
                     "family/lists/camping.md": CAMPING + "\n",
                     "family/camping/tent.md": TENT + "\n",
                     "family/camping/tent.json": SIDECAR + "\n"},
              trailers=("Migration: memory/0002",))
    m.changes("reply: Tent is torn", by="marge", at=NOW - timedelta(hours=1),
              files={"family/camping/tent.md": TENT + "\n> Use the old tarp.\n",
                     "family/camping/tent.json": SIDECAR + "\n\n"})
    m.changes("reprocess: Tent is torn", by="memory-bot", at=NOW - timedelta(minutes=10),
              files={"family/camping/tent.md": TENT + "\nThe tent needs a new tarp.\n"},
              trailers=("Replaces: 1a2b3c4", "Requested-By: bart"))
    return m


def reader(memory: Memory):
    """`GET /history` of a cards API serving `memory`, and the server to stop."""
    server = serve(CardsAPI(vault=memory.repo), host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def read(**query) -> list[dict]:
        params = "&".join(f"{k}={v}" for k, v in query.items())
        with urllib.request.urlopen(f"{base}/history?{params}") as r:
            return json.loads(r.read())["changes"]

    return read, server


def stop(server) -> None:
    # `shutdown` waits out the server's half-second poll; nothing needs to wait for it.
    threading.Thread(target=server.shutdown, daemon=True).start()


@pytest.fixture(scope="module")
def history(memory):
    read, server = reader(memory)
    yield read
    stop(server)


def glance(changes: list[dict]) -> list[tuple]:
    """Each change as one line of what a page shows: who, what, which cards, how many."""
    return [(c["who"], c["kind"], [card["title"] for card in c["cards"]], c["count"])
            for c in changes]


def test_the_latest_changes_come_newest_first_with_who_what_and_which_cards(history):
    """Given a week of the memory, when a page asks what changed, then it
    gets every change that touched a card, newest first, each saying who
    made it, what kind of change it was in a plain word, and its cards.

    The dashboard tells the family what happened in a sentence each; it
    can only do that from what the change was, not from a commit subject
    whose wording belongs to whichever bot wrote it.
    """
    assert glance(history()) == [
        ("memory-bot", "refiled", ["Tent is torn"], 1),
        ("marge", "replied", ["Tent is torn"], 1),
        ("memory-bot", "migrated", ["Tent is torn", "Camping", "Homer Simpson"], 3),
        ("marge", "filed", ["Bart's watercolour"], 1),
        ("homer", "filed", ["Tent is torn"], 1),
        ("homer", "edited", ["Camping"], 1),
        ("memory-bot", "edited", ["Homer Simpson", "Marge Simpson"], 2),
    ]


def test_a_change_says_when_and_whether_a_person_or_a_bot_made_it(history):
    """Given Homer's filing and the refile Bart asked for, then each says
    when it happened to the second, the person's name as their card has
    it, whether a bot made it, and for a bot's work who asked for it.

    The family reads "Homer filed a note 2 hours ago"; machine work is
    told apart so it does not read as something a person did.
    """
    changes = history()
    filed = changes[4]
    refiled = changes[0]

    assert datetime.fromisoformat(filed["at"]) == NOW - timedelta(days=1)
    assert (filed["who"], filed["who_name"], filed["machine"]) == ("homer", "Homer", False)
    assert (refiled["machine"], refiled["asked_by"]) == (True, "bart")


def test_each_card_comes_with_what_opens_it(history):
    """Given Homer's note, then the change names it by its card id, title
    and type, with its persistent link (`/go` + this), so a page links
    the card itself rather than a file path that moves."""
    [card] = history()[4]["cards"]

    assert {k: card[k] for k in ("id", "title", "type", "link")} == {
        "id": "note-7f3a9c21e4", "title": "Tent is torn", "type": "note", "link": "/note-7f3a9c21e4"}


def test_a_card_and_its_sidecar_are_one_card(history):
    """Given a filing that wrote the note and its sidecar (ADR-018), then
    the change names the card once: the sidecar is part of the card."""
    assert [c["id"] for c in history()[1]["cards"]] == ["note-7f3a9c21e4"]


def test_a_migration_over_many_cards_is_one_change_with_a_count(tmp_path):
    """Given a migration that touched 30 cards, then it is one change with
    a count of 30, how many of each type, and only the first few cards,
    not 30 entries.

    A migration on a real memory touches hundreds of cards; listing them
    would bury everything a person did that day.
    """
    memory = Memory.new(tmp_path / "vault")
    memory.changes("cards: give 30 card(s) their kind", by="memory-bot", at=NOW,
                   files={f"family/lists/list-{i}.md": f"---\ntype: list\ntitle: List {i}\n---\n"
                          for i in range(30)},
                   trailers=("Migration: memory/0006",))
    history, server = reader(memory)

    [migration] = history()
    stop(server)

    assert (migration["kind"], migration["count"], migration["types"]) == ("migrated", 30, {"list": 30})
    assert len(migration["cards"]) < 10


def test_a_change_to_someone_elses_own_card_is_left_out_for_another_audience(history):
    """Given Marge's watercolour in her own bucket, when the page is viewed
    as Bart, then the filing is not there at all; viewed as Marge, it is.

    "View as" must not tell Bart that Marge filed something, or its title.
    """
    as_bart = glance(history(audience="bart"))
    as_marge = glance(history(audience="marge"))

    assert ("marge", "filed", ["Bart's watercolour"], 1) not in as_bart
    assert len(as_bart) == 6
    assert ("marge", "filed", ["Bart's watercolour"], 1) in as_marge


def test_a_limit_keeps_the_newest(history):
    assert glance(history(limit=2)) == glance(history())[:2]
