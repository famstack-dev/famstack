"""`stack memory card edit`, described by what a caller sees it do.

Marge runs the command as `./stack memory card edit` does, through
`Stack.run_cli_command`. It reaches the card write endpoint the way it
does on an instance: through core's card door on this Mac, then the
memory stacklet's endpoint in the curator, which checks the change
against the card type's schema and commits it as her through the card
store, which pushes it to Forgejo.

What is real, from memory's test instance (`running_memory`): the command
and its loader, the memory stacklet's endpoint (`card_edit.CardEditor`
served by `cards_api.serve`, as the curator serves it), its schemas, the
card store and its clone, and core's routing of the door (`cards_page`:
which paths go through and which headers go with them). What stands in:
Forgejo, a bare git repository the store pushes to, and the door's
transport, which is a small server in the test instead of core's
container. The instance names its port as `[core] cards_port`, so nothing
reaches the card door a running instance publishes on this Mac.

What to assert: what Marge was told, and the commits and pages of the
memory repo afterwards (`dsl`). The data is the Simpsons'.
"""

from __future__ import annotations

import pytest

from stack.frontmatter import parse
from tests.memory.unit.instance import running_memory
from tests.springfield import REPO
from tests.unit.stacklets.simpsons_cards import ONTOLOGY, VAULT

from .dsl import commit

BART = "family/people/bart.md"
NOTE = "marge/notes/2026/10/art-supplies.md"


# ── The memory ───────────────────────────────────────────────────────────

@pytest.fixture
def memory(tmp_path, request):
    """The memory, holding the Simpsons' cards, or the files a test names
    with `@pytest.mark.parametrize("memory", [files], indirect=True)`."""
    files = getattr(request, "param", None) or {**VAULT, "ontology.toml": ONTOLOGY}
    with running_memory(tmp_path, files, seeded_by="homer", seeded_as="the Simpsons") as running:
        yield running


@pytest.fixture
def marge(memory):
    return memory.person("marge")


# ── Changing a card ──────────────────────────────────────────────────────

def test_a_field_is_changed_in_one_commit_by_the_person(memory, marge):
    """Given Bart's card, when Marge sets his description, then the memory
    has one new commit by her that names the card, the card holds the
    description, and she is told what changed.

    A change to a card is a person's commit (ADR-015): the history says
    who changed what.
    """
    marge.edits("person-bart", "--set", "description=Rides a skateboard.")

    assert memory.new_commits() == [commit(by="marge", saying="edit: Bart Simpson")]
    assert parse(memory.page(BART))["description"] == "Rides a skateboard."
    assert marge.was_told("Changed Bart Simpson: description: Rides a skateboard.")


def test_an_item_is_added_to_a_list_and_another_taken_out(memory, marge):
    """Given Marge's note tagged `watercolour`, when she adds `paint` and
    removes `watercolour` in one call, then the note holds the other tags
    and `paint`, in one commit.

    A list field is changed item by item from the command line, without
    retyping the items that stay.
    """
    marge.edits("note-7f3a9c21e4", "--add", "tags=paint", "--remove", "tags=watercolour")

    assert memory.new_commits() == [commit(by="marge", saying="edit: Art supplies for next time")]
    assert parse(memory.page(NOTE))["tags"] == ["itchy-scratchy-land", "paint"]


def test_a_value_the_card_type_does_not_hold_is_refused_with_the_ones_it_does(memory, marge):
    """Given the calendar list, when Marge sets its kind to `agenda`, then
    nothing is committed and she is told the field and the kinds a list has.

    The endpoint decides; the command passes its refusal on in words a
    person can act on.
    """
    marge.edits("list-termine", "--set", "kind=agenda")

    assert memory.new_commits() == []
    assert marge.was_told("kind", "checklist", "calendar")


def test_a_change_that_changes_nothing_commits_nothing(memory, marge):
    """Given Bart's card, when Marge sets his title to what it is, then
    nothing is committed and she is told there was nothing to change.

    The agent retries a change it is not sure of; a retry must not fill
    the history with empty commits.
    """
    marge.edits("person-bart", "--set", "title=Bart Simpson")

    assert memory.new_commits() == []
    assert marge.was_told("Nothing to change on person-bart")


# ── A correspondent written by hand ──────────────────────────────────────
#
# Before correspondents were cards, the memory stacklet documented a shape
# for a correspondent's file (`lib.py`) that no writer produced: `kind`,
# `canonical` for its name, and the household's own `address`, `website`
# and `topics`, with `phone` and `email` read as well. A household that
# followed it has such files. What a person wrote there is kept as fields
# of the card; `canonical`, the name before cards had a title, becomes the
# title.

PLANT = "family/correspondents/springfield-nuclear-power-plant.md"
PLANT_ID = "correspondent-springfield-nuclear-power-plant"
PLANT_BY_HAND = """---
kind: correspondent
canonical: Springfield Nuclear Power Plant
aliases:
  - "Springfield Nuclear Power Plant, Sector 7G"
topics: [employment, utility]
address: "100 Industrial Way, Springfield"
phone: "+1 555 0100"
website: "https://www.snpp.example"
---

# Springfield Nuclear Power Plant

Homer works in sector 7G. Mr. Burns owns it.
"""

HAND_WRITTEN = {
    "family/people/homer.md": VAULT["family/people/homer.md"],
    "family/people/marge.md": VAULT["family/people/marge.md"],
    "ontology.toml": ONTOLOGY + """
[topic.employment]
names = { de = "Arbeit", en = "Employment" }

[topic.utility]
names = { de = "Versorger", en = "Utilities" }
""",
    PLANT: PLANT_BY_HAND,
}


def _memory_migrations_run(stack) -> dict:
    """Every memory migration, run by the framework's runner as `stack up
    memory` runs them: through the curator, at core's card door the
    instance's stack.toml names. Not `Stack.migrate`: that renders the env
    of the instance on this Mac."""
    from stack.migrations import MigrationContext, Migrations

    migrations = Migrations("memory", REPO / "stacklets" / "memory", stack.data / "memory")
    told: list[str] = []
    outcome = migrations.run(
        lambda m: MigrationContext(m, stack=stack, stacklet_id="memory",
                                   env={}, step_fn=told.append),
        told.append)
    assert "failed" not in outcome, "\n".join([str(outcome["failed"]), *told])
    return outcome


@pytest.mark.parametrize("memory", [HAND_WRITTEN], indirect=True)
def test_a_correspondent_written_by_hand_is_migrated_then_meets_its_type_and_can_be_edited(
        memory, marge):
    """Given the Springfield Nuclear Power Plant's file in the shape the
    memory stacklet once documented, when the memory migrations run, then
    the file is a correspondent card whose title is its name and which
    keeps its address, phone, website and topics; the format check finds
    nothing to fix; and Marge can change its website and phone, in one
    commit by her.

    A card with a field its type does not name refuses every change, so a
    household's own words about a correspondent must be fields of the card
    or the card could never be edited again.

    About two seconds: every memory migration runs, each reading the
    curator's clone, since 0004 and 0006 shape the file before 0009 names it.
    That a second run changes nothing is the migration chain test's.
    """
    _memory_migrations_run(memory.stack)

    card = parse(memory.page(PLANT))
    assert card["title"] == "Springfield Nuclear Power Plant" and "canonical" not in card
    assert (card["address"], card["phone"], card["website"], card["topics"]) == (
        "100 Industrial Way, Springfield", "+1 555 0100", "https://www.snpp.example",
        ["employment", "utility"])
    assert memory.trailers("Migration")[-1] == "memory/0009"

    marge.checks_the_format()
    assert marge.was_told("Nothing to fix")

    before = len(memory.new_commits())
    marge.edits(PLANT_ID, "--set", "website=https://www.snpp.example/visitors",
                "--set", "phone=+1 555 0199")

    assert memory.new_commits()[before:] == [
        commit(by="marge", saying="edit: Springfield Nuclear Power Plant")]
    edited = parse(memory.page(PLANT))
    assert (edited["website"], edited["phone"]) == ("https://www.snpp.example/visitors", "+1 555 0199")
    assert marge.was_told("Changed Springfield Nuclear Power Plant:", "phone: +1 555 0199",
                          "website: https://www.snpp.example/visitors")
