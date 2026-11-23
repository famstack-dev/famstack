"""A new topic room's name, as the family wrote it, on the topic's card and list.

The Simpsons open the room "Topic: Itchy & Scratchy Land" and send it a
note with something to do. The archivist binds the room to the topic id
`itchy-scratchy-land` and files the note there. The topic's card and its
list must carry the name the family gave the room, `&` included, because
the memory's pages, the menu and the list show those titles. The id is a
folder name and drops the `&`; a title made from the id loses it for good.

A topic card that is already there is the family's and stays as it is.

The archivist runs as `test_archivist_topic_rename.py` runs it: its room
binding over a stand-in Matrix client, its real capture pipeline and mirror,
filing into memory's test instance (`running_memory`). The classifier is a
stand-in: what it says is not under test.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stack.frontmatter import parse
from stack.memory import MemoryAPI
from stacklets.docs.bot.archivist import ArchivistBot
from stacklets.docs.bot.capture_pipeline import CapturePipeline
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.vault_context import VaultContext
from tests.memory.unit.instance import running_memory

BOT_ID = "@archivist-bot:simpson"
MEMBERS = [BOT_ID, "@homer:simpson", "@marge:simpson", "@bart:simpson"]
PEOPLE = {f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
          for pid, name in (("homer", "Homer Simpson"), ("marge", "Marge Simpson"),
                            ("bart", "Bart Simpson"))}


class Classifier:
    """A note's classification, with one thing to do."""

    async def classify_capture(self, **_):
        return {"title": "Tickets", "tags": [], "persons": ["Marge"], "summary": "s",
                "action_items": [{"action": "Buy the tickets"}]}


class Words:
    async def extract(self, text):
        return SimpleNamespace(text=text, source_uri=None, title_hint=None)




class Room:
    """The room's Matrix side: no binding yet, and the bindings posted to it."""

    def __init__(self):
        self.bound, self.rooms = None, {}

    async def room_messages(self, room_id, **_):
        return SimpleNamespace(chunk=[SimpleNamespace(source={"content": self.bound})]
                               if self.bound else [])

    async def room_send(self, room_id, message_type, content, **_):
        self.bound = content


@pytest.fixture
def memory(tmp_path, monkeypatch, request):
    files = getattr(request, "param", {})
    with running_memory(tmp_path / "data", {**PEOPLE, **files}, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        yield memory


class Archivist:
    """The archivist as one process of the bot runner builds it, over `client`.

    `client` is the room's Matrix side, shared between processes: a binding
    one process posted is there for the next. `forgejo=False` is a process
    started before Forgejo was set up: it has no mirror.
    """

    def __init__(self, memory, tmp_path, client: Room, *, forgejo: bool = True):
        self.vault = VaultContext(language="en", shared_bucket="family")
        self.mirror = GitMirror(data_dir=tmp_path / "archivist", vault=self.vault,
                                memory=MemoryAPI.from_container(memory.environ))
        self.bot = ArchivistBot(homeserver="http://h", user_id=BOT_ID, password="x",
                                session_dir=tmp_path / "session",
                                services=SimpleNamespace(vault=self.vault,
                                                         mirror=self.mirror if forgejo else None))
        self.bot._client = client

    async def binding(self, room_name: str, members=MEMBERS):
        """The binding of the room named `room_name`, as a capture there resolves it."""
        room = SimpleNamespace(room_id="!room:simpson", name=room_name, display_name=room_name,
                               users={uid: object() for uid in members})
        return await self.bot._rooms.topic_binding(room, "@marge:simpson")

    async def files(self, room_name: str, words: str, members=MEMBERS):
        """`words` sent by Marge to the room `room_name`, filed as the archivist files a note."""
        binding = await self.binding(room_name, members)
        pipeline = CapturePipeline(
            url_extractor=None, text_extractor=Words(), classifier=Classifier(), mirror=self.mirror,
            paperless=None, bot_name="archivist-bot", classify_max_chars=10000,
            capture_keep_body=False, vault=self.vault)
        return await pipeline.capture_text(text=words, sender_mxid="@marge:simpson",
                                           bucket=binding.bucket, seed_topics=binding.seed_topics)


ITCHY = "Topic: Itchy & Scratchy Land"
CARD, LIST = "family/itchy-scratchy-land/topic.md", "family/lists/itchy-scratchy-land.md"


@pytest.mark.parametrize("room_name, topic, name", [
    (ITCHY, "itchy-scratchy-land", "Itchy & Scratchy Land"),
    ("Topic: Bart's Zimmer", "bart-s-zimmer", "Bart's Zimmer"),
    ("Thema: Grünes Haus", "grunes-haus", "Grünes Haus"),
    ('Topic: "Krusty" Burger', "krusty-burger", '"Krusty" Burger'),
])
async def test_a_new_topic_room_names_its_card_and_list_as_the_room_is_named(
        memory, tmp_path, room_name, topic, name):
    """Given a new topic room whose name has an `&`, an apostrophe, an
    umlaut or opens with a quote, when Marge sends it a note with something
    to do, then the topic's card and its list are titled with the room's
    name as written. The memory's pages and the menu read the card's title."""
    outcome = await Archivist(memory, tmp_path, Room()).files(room_name, "We must buy the tickets")

    assert outcome.vault_path.startswith(f"family/{topic}/notes/")
    assert parse(memory.text(f"family/{topic}/topic.md"))["title"] == name
    assert parse(memory.text(f"family/lists/{topic}.md"))["title"] == name


async def test_a_personal_topic_room_names_its_card_as_the_room_is_named(memory, tmp_path):
    """Given Marge alone in "Topic: Itchy & Scratchy Land", a personal topic
    whose card is in her own bucket, when she sends it a note, then the
    card is titled with the room's name."""
    outcome = await Archivist(memory, tmp_path, Room()).files(
        ITCHY, "We must buy the tickets", members=[BOT_ID, "@marge:simpson"])

    assert outcome.vault_path.startswith("marge/itchy-scratchy-land/notes/")
    assert parse(memory.text("marge/itchy-scratchy-land/topic.md"))["title"] == "Itchy & Scratchy Land"


@pytest.mark.parametrize("memory", [{
    CARD: "---\ntype: topic\ncard_id: topic-itchy-scratchy-land\ntitle: Itchy and Scratchy\n---\n",
}], indirect=True)
async def test_a_topic_card_that_is_there_keeps_its_title(memory, tmp_path):
    """Given the topic's card already there with the family's title, when
    the room is bound and a note filed, then the card keeps that title and
    the list takes it: a card, once written, is the family's."""
    await Archivist(memory, tmp_path, Room()).files(ITCHY, "We must buy the tickets")

    assert parse(memory.text(CARD))["title"] == "Itchy and Scratchy"
    assert parse(memory.text(LIST))["title"] == "Itchy and Scratchy"


async def test_a_room_bound_before_its_card_could_be_written_names_it_on_the_next_filing(
        memory, tmp_path):
    """Given the room bound by an archivist that could not write the card
    (no Forgejo yet), when the archivist runs again with Forgejo and Marge
    sends a note, then the card is titled with the room's name, not made
    from the topic's id by the filing."""
    room = Room()
    await Archivist(memory, tmp_path, room, forgejo=False).binding(ITCHY)
    assert room.bound and memory.text(CARD) == ""

    await Archivist(memory, tmp_path, room).files(ITCHY, "We must buy the tickets")

    assert parse(memory.text(CARD))["title"] == "Itchy & Scratchy Land"
    assert parse(memory.text(LIST))["title"] == "Itchy & Scratchy Land"


async def test_renaming_a_bound_room_leaves_the_topics_name(memory, tmp_path):
    """Given the room bound and its card written, when the family renames
    the room and the archivist, restarted, files a note there, then the
    note goes to the same topic and the card keeps its title. Renaming the
    topic is `stack memory topic rename`, or an edit of the card's title."""
    room = Room()
    await Archivist(memory, tmp_path, room).files(ITCHY, "We must buy the tickets")

    outcome = await Archivist(memory, tmp_path, room).files(
        "Topic: Krusty Land", "We must book the hotel")

    assert outcome.vault_path.startswith("family/itchy-scratchy-land/notes/")
    assert parse(memory.text(CARD))["title"] == "Itchy & Scratchy Land"
