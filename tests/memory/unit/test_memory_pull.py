"""`stack memory pull`, described by what the admin sees it do.

The curator holds the memory's clone and is the one process that changes it
(ADR-019). It pulls a hand edit made in Forgejo when Forgejo tells it of the
push, or at its next cycle; `stack memory pull` asks it to pull now, and says
how the clone stands against Forgejo: up to date, changes waiting to be
pushed, or a divergence waiting for a person.

The real entry point, through core's card door to the curator's API and its
card store, on memory's test instance (`running_memory`); Forgejo is a bare
git repository the store pushes to. The data is the Simpsons'.
"""

from __future__ import annotations

import pytest

from stack.http_json import JsonClient
from tests.memory.unit.instance import TOKEN, running_memory

LIST = "family/lists/camping.md"
CAMPING = "---\ntype: list\ncard_id: list-camping\ntitle: Camping\n---\n\n- [ ] Tent\n"


@pytest.fixture
def memory(tmp_path):
    """The memory, holding Homer's camping list."""
    with running_memory(tmp_path, {LIST: CAMPING}, seeded_by="homer",
                        seeded_as="chore(lists): homer added Camping") as running:
        yield running


@pytest.fixture
def homer(memory):
    return memory.person("homer")


def test_an_edit_made_in_forgejo_is_in_the_memory_at_once(memory, homer):
    """Given Marge's edit of the camping list in Forgejo's web editor, when
    Homer pulls, then the curator's clone has it, and he is told the memory
    is up to date.

    The wiki, the agent and every `stack memory` command read the clone;
    an admin who just edited in Forgejo can have it there now.
    """
    memory.edited_in_forgejo({LIST: CAMPING.replace("Tent", "Torn tent")}, by="marge",
                             subject="Tent is torn", pulled=False)

    answer = homer.runs("memory", "pull", [])

    assert (memory.vault / LIST).read_text(encoding="utf-8") == CAMPING.replace("Tent", "Torn tent")
    assert answer["state"] == "ok"
    assert homer.was_told("up to date with Forgejo")


def test_changes_made_while_forgejo_was_down_are_named_until_they_are_pushed(memory, homer):
    """Given a change written while Forgejo was down, when Homer pulls with
    Forgejo still down, then he is told it waits in the curator's clone and
    goes out once Forgejo is back; once it is back, a pull pushes it.

    Nothing is lost while Forgejo restarts, and the admin can see what is
    still on its way.
    """
    curator = JsonClient(memory.curator, token=TOKEN)
    version = curator.get(f"/files/{LIST}").body["version"]
    with memory.forgejo_down():
        curator.post("/commits", {"kind": "chore(lists)", "title": "marge changed Camping",
                                  "by": "marge", "changes": [{
                                      "path": LIST, "text": CAMPING.replace("Tent", "Tent for four"),
                                      "base": version}]})
        waiting = homer.runs("memory", "pull", [])

    assert waiting["state"] == "unreachable"
    assert waiting["waiting"] == ["chore(lists): marge changed Camping"]
    assert homer.was_told("1 change waits", "chore(lists): marge changed Camping")

    pushed = homer.runs("memory", "pull", [])

    assert pushed["state"] == "ok" and pushed["waiting"] == []
    assert memory.page(LIST) == CAMPING.replace("Tent", "Tent for four")
