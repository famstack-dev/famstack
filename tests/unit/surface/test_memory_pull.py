"""`stack memory pull`, described by what the admin sees it do.

The curator holds the memory's clone and is the one process that changes it
(ADR-019). It pulls a hand edit made in Forgejo when Forgejo tells it of the
push, or at its next cycle; `stack memory pull` asks it to pull now, and says
how the clone stands against Forgejo: up to date, changes waiting to be
pushed, or a divergence waiting for a person.

The real entry point, through core's card door to the curator's API and its
card store (`conftest`); Forgejo is a git repository the store pushes to. The
data is the Simpsons'.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from stack.http_json import JsonClient

from .conftest import CARDS_WRITE_TOKEN, make_stack, write_instance
from .dsl import Memory, Person

LIST = "family/lists/camping.md"
CAMPING = "---\ntype: list\ncard_id: list-camping\ntitle: Camping\n---\n\n- [ ] Tent\n"


@pytest.fixture
def memory(tmp_path) -> Memory:
    """family/memory on Forgejo, holding Homer's camping list."""
    memory = Memory.new(tmp_path / "forgejo" / "memory")
    memory.changes("chore(lists): homer added Camping", by="homer",
                   at=datetime(2026, 10, 20, 8, tzinfo=timezone.utc), files={LIST: CAMPING})
    return Memory(memory.repo)


@pytest.fixture
def springfield(tmp_path, core_door, as_stack_runs_it):
    instance = write_instance(tmp_path)
    toml = instance / "stack.toml"
    toml.write_text(toml.read_text().replace("[core]\n", f"[core]\ncards_port = {core_door}\n", 1))
    (instance / ".stack" / "secrets.toml").write_text(
        f'global__ADMIN_PASSWORD = "donuts"\nmemory__CARDS_WRITE_TOKEN = "{CARDS_WRITE_TOKEN}"\n')
    return make_stack(instance)


@pytest.fixture
def homer(springfield, tmp_path) -> Person:
    return Person("homer", springfield, tmp_path)


def test_an_edit_made_in_forgejo_is_in_the_memory_at_once(memory, homer, curator_clone):
    """Given Marge's edit of the camping list in Forgejo's web editor, when
    Homer pulls, then the curator's clone has it, and he is told the memory
    is up to date.

    The wiki, the agent and every `stack memory` command read the clone;
    an admin who just edited in Forgejo can have it there now.
    """
    memory.changes("Tent is torn", by="marge", at=datetime(2026, 10, 21, 8, tzinfo=timezone.utc),
                   files={LIST: CAMPING.replace("Tent", "Torn tent")})

    answer = homer.runs("memory", "pull", [])

    assert (curator_clone / LIST).read_text(encoding="utf-8") == CAMPING.replace("Tent", "Torn tent")
    assert answer["state"] == "ok"
    assert homer.was_told("up to date with Forgejo")


def test_changes_made_while_forgejo_was_down_are_named_until_they_are_pushed(
        memory, homer, curator_api):
    """Given a change written while Forgejo was down, when Homer pulls with
    Forgejo still down, then he is told it waits in the curator's clone and
    goes out once Forgejo is back; once it is back, a pull pushes it.

    Nothing is lost while Forgejo restarts, and the admin can see what is
    still on its way.
    """
    curator = JsonClient(curator_api, token=CARDS_WRITE_TOKEN)
    version = curator.get(f"/files/{LIST}").body["version"]
    down = Path(str(memory.repo) + ".down")
    memory.repo.rename(down)
    curator.post("/commits", {"kind": "chore(lists)", "title": "marge changed Camping", "by": "marge",
                              "changes": [{"path": LIST, "text": CAMPING.replace("Tent", "Tent for four"),
                                           "base": version}]})

    waiting = homer.runs("memory", "pull", [])

    assert waiting["state"] == "unreachable"
    assert waiting["waiting"] == ["chore(lists): marge changed Camping"]
    assert homer.was_told("1 change waits", "chore(lists): marge changed Camping")

    down.rename(memory.repo)
    pushed = homer.runs("memory", "pull", [])

    assert pushed["state"] == "ok" and pushed["waiting"] == []
    assert memory.page(LIST) == CAMPING.replace("Tent", "Tent for four")
