"""The Simpsons' memory and the reads of its overview, shared by the overview's tests.

    from tests.memory.unit.overview_world import ask, overview    # `simpsons` is in conftest.py

    page = overview(simpsons, "topic/topic-camping")      # the answer of `GET /overview/<view>`
    status, page = ask(simpsons, "topic/topic-bowling")   # (404, {}) for a page Marge may not read

The card builders (`person`, `topic`, `card`, `correspondent`) write a
card's file as the memory holds it. `simpsons` is the fabricated family's
memory, seeded a month ago, one per test module that uses it. The tests are
split by topic: `test_memory_overview.py` (the sections and who may see
what), `test_memory_overview_parts.py` (names, parts, an empty memory) and
`test_memory_overview_topic.py` (a topic's answer).

"Today" is 2026-10-01 (`STACK_TODAY`, the household clock's setting).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from tests.memory.unit.instance import RunningMemory, running_memory

TODAY = "2026-10-01"
SEEDED = "2026-09-01T10:00:00"


def person(slug: str, title: str) -> str:
    return f"---\ntype: person\ncard_id: person-{slug}\ntitle: {title}\naccount: {slug}\n---\n\n# {title}\n"


def topic(slug: str, title: str) -> str:
    return f"---\ntype: topic\ncard_id: topic-{slug}\ntitle: {title}\n---\n\n# {title}\n"


def card(kind: str, card_id: str, title: str, day: str, persons: list[str], **fields: str) -> str:
    people = "".join(f"\n  - {p}" for p in persons)
    extra = "".join(f"\n{k}: \"{v}\"" for k, v in fields.items())
    return (f"---\ntype: {kind}\ncard_id: {card_id}\ntitle: {title}\ndate: \"{day}\"\n"
            f"persons:{people or ' []'}{extra}\n---\n\n# {title}\n")


def correspondent(slug: str, title: str) -> str:
    return f"---\ntype: correspondent\ncard_id: correspondent-{slug}\ntitle: {title}\n---\n\n# {title}\n"


GROCERIES = ("---\ntype: list\ncard_id: list-groceries\ntitle: Groceries\n---\n\n"
             "- [ ] Milk\n- [ ] Pumpkin for the party 📅 2026-10-05\n- [ ] Eggs\n- [x] Duff\n")
# One list, items for two people: each one's items are theirs alone.
PARTY = ("---\ntype: list\ncard_id: list-party\ntitle: Party\n---\n\n"
         "- [ ] Cake [Marge](../people/marge.md) 📅 2026-10-08\n"
         "- [ ] Balloons [Bart](../people/bart.md) 📅 2026-10-09\n")
CHORES = ("---\ntype: list\ncard_id: list-chores\ntitle: Bart's chores\n---\n\n"
          "- [ ] Clean the room [Bart](../people/bart.md)\n")

SIMPSONS = {
    "family/people/homer.md": person("homer", "Homer Simpson"),
    "family/people/marge.md": person("marge", "Marge Simpson"),
    "family/people/bart.md": person("bart", "Bart Simpson"),
    "family/people/lisa.md": person("lisa", "Lisa Simpson"),
    "family/camping/topic.md": topic("camping", "Camping"),
    "family/camping/notes/tent.md": card("note", "note-tent", "The tent is torn", "2026-09-03", ["marge"]),
    "family/camping/notes/stove.md": card("note", "note-stove", "Gas for the stove", "2026-09-04", ["homer"]),
    "family/lists/groceries.md": GROCERIES,
    "family/lists/chores.md": CHORES,
    "family/lists/party.md": PARTY,
    "family/notes/school-play.md": card("note", "note-play", "Lisa's school play", "2026-10-20",
                                        ["lisa", "marge"]),
    "family/documents/insurance.md": card("document", "document-insurance", "Car insurance",
                                          "2026-09-02", ["marge"], document_type="Policy",
                                          correspondent="Springfield Insurance"),
    "family/documents/receipt.md": card("document", "document-receipt", "Groceries receipt",
                                        "2026-09-05", ["marge"], document_type="Receipt",
                                        correspondent="Kwik-E-Mart"),
    "family/correspondents/springfield-insurance.md": correspondent("springfield-insurance",
                                                                    "Springfield Insurance"),
    "family/correspondents/kwik-e-mart.md": correspondent("kwik-e-mart", "Kwik-E-Mart"),
    "family/correspondents/power-plant.md": correspondent("power-plant", "Power Plant"),
    "family/bookmarks/pie.md": card("bookmark", "bookmark-pie", "Pumpkin pie", "2026-09-06", ["marge"],
                                    resource="https://example.org/pie"),
    "marge/garden/topic.md": topic("garden", "Garden"),
    "marge/garden/notes/roses.md": card("note", "note-roses", "Roses need cutting", "2026-09-07", []),
    # Homer's own folder: never Marge's, though a card names her.
    "homer/bowling/topic.md": topic("bowling", "Bowling"),
    "homer/bowling/notes/league.md": card("note", "note-league", "Bowling league", "2026-09-08",
                                          ["homer", "marge"]),
    "homer/documents/payslip.md": card("document", "document-payslip", "Payslip September",
                                       "2026-09-09", ["homer", "marge"], document_type="Payslip",
                                       correspondent="Power Plant"),
}
# A note without a date of its own: the graph gives it the day it last changed.
UNDATED = ("---\ntype: note\ncard_id: note-undated\ntitle: Call the plumber\n"
           "persons:\n  - marge\n---\n\n# Call the plumber\n")
SURPRISE = {"homer/notes/surprise.md": card("note", "note-surprise", "Surprise party for Marge",
                                            "2026-10-10", ["marge"])}
# What of Homer's own folder must never reach anyone else, by id and by title.
HOMERS_OWN = ["note-surprise", "Surprise party", "note-league", "Bowling league",
              "document-payslip", "Payslip September", "topic-bowling", "Power Plant"]

VIEWS = ["me", "family", "person/marge", "person/bart", "person/homer", "topic/topic-camping",
         "sender/correspondent-kwik-e-mart", "documents",
         "documents/Payslip", "everything", "kind/note", "kind/document", "senders"]


@pytest.fixture(scope="module")
def simpsons(tmp_path_factory) -> Iterator[RunningMemory]:
    """The Simpsons' memory, seeded a month ago. Marge edited the tent note
    three days ago, Homer filed the surprise party in his own folder two
    days ago. Yesterday the memory bot tidied the chores, and rebuilt
    Marge's roses note in her name. Today Marge ticked the milk and wrote
    down an undated note."""
    with pytest.MonkeyPatch.context() as clock, \
            running_memory(tmp_path_factory.mktemp("overview"), SIMPSONS, seeded_at=SEEDED) as memory:
        clock.setenv("STACK_TODAY", TODAY)
        memory.edited_in_forgejo({"family/camping/notes/tent.md":
                                  SIMPSONS["family/camping/notes/tent.md"] + "\nIt rained in.\n"},
                                 by="marge", at="2026-09-28T10:00:00")
        memory.edited_in_forgejo(SURPRISE, by="homer", at="2026-09-29T10:00:00")
        memory.edited_in_forgejo({"family/lists/chores.md": CHORES + "\n"},
                                 by="memory-bot", subject="cards: tidy", at="2026-09-30T10:00:00")
        memory.edited_in_forgejo({"marge/garden/notes/roses.md":
                                  SIMPSONS["marge/garden/notes/roses.md"] + "\nRebuilt.\n"},
                                 by="marge", committer="memory-bot",
                                 subject="update: diary entry rebuilt", at="2026-09-30T11:00:00")
        memory.edited_in_forgejo({"family/lists/groceries.md": GROCERIES.replace("- [ ] Milk", "- [x] Milk"),
                                  "family/notes/undated.md": UNDATED},
                                 by="marge", at=f"{TODAY}T09:00:00")
        yield memory


def ask(memory: RunningMemory, view: str, audience: str = "marge") -> tuple[int, dict]:
    """(status, answer) of `GET /overview/<view>?audience=<audience>`;
    `view` may carry its own query (`kind/note?part=2`)."""
    joined = "&" if "?" in view else "?"
    url = f"{memory.curator}/overview/{view}" + (f"{joined}audience={audience}" if audience else "")
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def overview(memory: RunningMemory, view: str, audience: str = "marge") -> dict:
    status, answer = ask(memory, view, audience)
    assert status == 200, answer
    return answer


def ids(part: dict | list) -> list[str]:
    """The card ids of a section's items, in order."""
    items = part["items"] if isinstance(part, dict) else part
    return [i["id"] for i in items]
