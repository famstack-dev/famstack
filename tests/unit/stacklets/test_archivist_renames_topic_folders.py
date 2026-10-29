"""The archivist moves a topic room's folder to the spelling its name has now.

Before German letters were spelled out, the room "Thema: Nähprojekte" was
bound to the folder `nahprojekte`. On every start the archivist compares
each topic room's binding with the folder its name spells now; where they
differ it moves the topic in one commit through the memory's curator (its
folder, its list, the topic id on their cards, the topic card's title) and
posts the room's new binding. A second start finds nothing to do.

Driven as the archivist runs it: `on_ready` after the first sync, against
the real path to the memory (the curator's API over a bare repository as
Forgejo, `running_memory`), with a stand-in only for Matrix, which records
the bindings the archivist reads and posts. The household is the Simpsons.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from loguru import logger

from stack.frontmatter import parse
from stacklets.docs.bot.archivist import ArchivistBot
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.memory_client import MemoryClient
from stacklets.docs.bot.vault_context import VaultContext
from tests.memory.unit.instance import git, running_memory

BOT_ID = "@archivist-bot:server"
ROOM = "!sewing:server"

OLD_BINDING = {
    "kind": "topic", "bucket": "family/nahprojekte", "slug": "nahprojekte",
    "display_name": "Nähprojekte", "default_topics": ["nahprojekte"], "scope": "shared",
    "extract_knowledge": True, "bootstrapped_at": "2026-09-01T10:00:00+00:00",
    "bootstrapped_by": "@marge:server",
}
NOTE = "family/nahprojekte/notes/2026/10/schnittmuster-a1b2c3.md"
SEWING = {
    "family/nahprojekte/topic.md":
        "---\ntype: topic\ncard_id: topic-nahprojekte\ntitle: Nahprojekte\n---\n\n# Nahprojekte\n",
    NOTE: "---\ntype: note\ncard_id: note-0a1b2c3d4e\ntitle: Schnittmuster\n"
          "tags:\n  - nahprojekte\n  - stoff\n---\n\n# Schnittmuster\n\nDas Kleid für Lisa.\n",
    "family/lists/nahprojekte.md":
        "---\ntype: list\ncard_id: list-nahprojekte\ntitle: Nähprojekte\n"
        "topics:\n  - nahprojekte\n---\n\n- [ ] Stoff kaufen\n",
    # Written by hand: a link into the folder the archivist does not rewrite.
    "family/notes/2026/10/plan-d4e5f6.md":
        "---\ntype: note\ncard_id: note-9f8e7d6c5b\ntitle: Plan\n---\n\n"
        "See [the pattern](../../../nahprojekte/notes/2026/10/schnittmuster-a1b2c3.md).\n",
}


class FakeMatrix:
    """The archivist's Matrix client as far as bindings go: the newest
    `dev.famstack.capture` event it posted in a room is the binding read back."""

    def __init__(self, binding: dict | None, name: str = "Thema: Nähprojekte"):
        self.bindings = [binding] if binding else []
        self.rooms = {ROOM: SimpleNamespace(room_id=ROOM, name=name, display_name=name,
                                            users={BOT_ID: 0, "@marge:server": 0, "@homer:server": 0})}

    async def room_messages(self, room_id, limit=10, message_filter=None, **_):
        return SimpleNamespace(chunk=[SimpleNamespace(source={
            "type": "dev.famstack.capture", "sender": BOT_ID, "content": binding})
            for binding in self.bindings[-1:]])

    async def room_send(self, room_id, message_type, content, **_):
        self.bindings.append(content)


@pytest.fixture
def memory(tmp_path, request, monkeypatch):
    files = {**SEWING, **(getattr(request, "param", None) or {})}
    with running_memory(tmp_path / "data", files, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("LANGUAGE", "de")
        yield memory


@pytest.fixture
def archivist(memory, tmp_path):
    vault = VaultContext(language="de", shared_bucket="family")
    mirror = GitMirror(data_dir=tmp_path / "archivist", vault=vault,
                       memory=MemoryClient.from_environ(memory.environ))
    bot = ArchivistBot(homeserver="http://h", user_id=BOT_ID, password="x",
                       session_dir=tmp_path / "session",
                       services=SimpleNamespace(mirror=mirror, vault=vault))
    bot._client = FakeMatrix(OLD_BINDING)
    return bot


@pytest.fixture
def logged():
    """What the archivist logs during a test, one line per message."""
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="INFO")
    yield lines
    logger.remove(sink)


def files_in(memory, pattern: str = "") -> list[str]:
    listed = git(memory.forgejo.repo, "ls-tree", "-r", "--name-only", "main").splitlines()
    return [p for p in listed if re.search(pattern, p)]


async def test_the_topic_moves_to_the_folder_its_name_spells(archivist, memory):
    """Given the sewing room bound to `nahprojekte`, when the archivist
    starts, then the folder, its list and the topic on their cards are
    `naehprojekte`, in one commit by the archivist; the cards keep their
    ids, and the topic card has the room's name as its title."""
    await archivist.on_ready()

    assert files_in(memory, "nahprojekte") == []
    assert files_in(memory, "naehprojekte") == [
        "family/lists/naehprojekte.md",
        "family/naehprojekte/notes/2026/10/schnittmuster-a1b2c3.md",
        "family/naehprojekte/topic.md",
    ]
    assert memory.forgejo.new_commits() == [
        "archivist-bot <archivist-bot@local> | archivist-bot <archivist-bot@local> | "
        "rename: Nähprojekte, from family/nahprojekte to family/naehprojekte"]

    note = parse(memory.text(NOTE.replace("nahprojekte", "naehprojekte")))
    assert note["card_id"] == "note-0a1b2c3d4e" and note["tags"] == ["naehprojekte", "stoff"]
    assert parse(memory.text("family/lists/naehprojekte.md"))["topics"] == ["naehprojekte"]
    topic = memory.text("family/naehprojekte/topic.md")
    assert parse(topic) == {"type": "topic", "card_id": "topic-nahprojekte", "title": "Nähprojekte"}
    assert topic.endswith("\n# Nähprojekte\n")


async def test_the_room_is_bound_to_the_new_folder_and_files_there(archivist, memory):
    """Given the rename, when a note is sent to the room afterwards, then
    the room's binding names the new folder and the note lands in it."""
    await archivist.on_ready()

    assert archivist.client.bindings[-1] == {
        **OLD_BINDING, "bucket": "family/naehprojekte", "slug": "naehprojekte",
        "default_topics": ["naehprojekte"]}
    binding = await archivist._topic_binding(archivist.client.rooms[ROOM], "@marge:server")
    await archivist._services.mirror.publish_capture(
        entity=binding.bucket, kind="note", source_uri=None, title_hint=None,
        body_text="Reißverschluss besorgen", captured_at="2026-10-29", model=None,
        classification={"title": "Reißverschluss", "summary": "s"}, tags=binding.seed_topics)

    [filed] = files_in(memory, r"reissverschluss.*\.md$")
    assert filed.startswith("family/naehprojekte/notes/2026/10/")


async def test_a_second_start_changes_nothing(archivist, memory):
    await archivist.on_ready()
    commits, bindings = memory.forgejo.new_commits(), list(archivist.client.bindings)

    await archivist.on_ready()

    assert memory.forgejo.new_commits() == commits
    assert archivist.client.bindings == bindings


async def test_a_room_whose_folder_moved_but_was_not_rebound_is_rebound(archivist, memory):
    """A start that moved the folder and failed to post the binding leaves
    the room bound to an empty folder; the next start binds it and moves nothing."""
    await archivist.on_ready()
    archivist.client.bindings.pop()
    commits = memory.forgejo.new_commits()

    await archivist.on_ready()

    assert memory.forgejo.new_commits() == commits
    assert archivist.client.bindings[-1]["bucket"] == "family/naehprojekte"


@pytest.mark.parametrize("memory", [{
    "family/naehprojekte/topic.md":
        "---\ntype: topic\ncard_id: topic-naehprojekte\ntitle: Naehprojekte\n---\n\n# Naehprojekte\n",
}], indirect=True)
async def test_a_folder_that_exists_already_is_not_overwritten(archivist, memory, logged):
    """Given both `nahprojekte` and `naehprojekte`, the archivist cannot
    tell which is the topic: it moves nothing, keeps the binding, and says so."""
    before = files_in(memory)

    await archivist.on_ready()

    assert files_in(memory) == before
    assert memory.forgejo.new_commits() == []
    assert archivist.client.bindings == [OLD_BINDING]
    assert any("family/naehprojekte" in line and "exists" in line for line in logged)


async def test_a_link_written_by_hand_into_the_old_folder_is_named(archivist, memory, logged):
    await archivist.on_ready()

    assert any("family/notes/2026/10/plan-d4e5f6.md" in line and "nahprojekte" in line
               for line in logged)


async def test_a_room_bound_to_the_folder_its_name_spells_is_left_alone(archivist, memory):
    archivist.client.bindings = [{**OLD_BINDING, "display_name": "Nahprojekte"}]

    await archivist.on_ready()

    assert memory.forgejo.new_commits() == []
    assert len(archivist.client.bindings) == 1


async def test_a_new_topic_rooms_card_has_the_rooms_name(archivist, memory):
    """Given a new room "Thema: Gemüsegarten", when the archivist binds it,
    then its folder is `gemuesegarten` and its topic card is titled with the
    room's name, which the folder no longer spells."""
    archivist._client = FakeMatrix(None, name="Thema: Gemüsegarten")

    binding = await archivist._topic_binding(archivist.client.rooms[ROOM], "@marge:server")

    assert binding.bucket == "family/gemuesegarten"
    card = memory.text("family/gemuesegarten/topic.md")
    assert parse(card)["title"] == "Gemüsegarten" and card.endswith("\n# Gemüsegarten\n")

