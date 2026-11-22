"""How a topic room files: from a message in the room to the topic's card.

A Matrix room named "Topic: Quilt for Lisa" is bound to a topic. What
people send there is filed in the topic's folder, tagged with the topic's
id, and its to-dos go on the topic's list. The Simpsons use two kinds:

- a personal topic, with one person in the room: Marge's "Topic: Quilt for
  Lisa" files under `marge/quilt-for-lisa`;
- a shared topic, with two or more: "Topic: Camper van" files under
  `family/camper-van`.

These tests hand the archivist's handlers (`_on_text`, `_on_file`,
`_on_reaction`) what the bot runner would: a Matrix event, a room, a
reaction. They read what Forgejo then holds. The bot is built directly
with `ArchivistBot` and an `ArchivistServices` made here, as the other
archivist tests build it, not by the bot runner. Its capture pipeline and
mirror are real, over the curator of memory's test instance
(`running_memory`). Stand-ins: the classifier (it answers from a script),
the page a link leads to, the room's Matrix client (it keeps the binding
event the archivist posts), and the bot's chat port (`_send`, `_react`,
`_set_typing`, the media download, the welcome).

The route for a shared topic's plain chat is off: a short line from Homer
in "Topic: Camper van" files nothing, and pinning it with 📌 keeps it.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from stack.bot import voice
from stack.frontmatter import parse
from stack.memory import MemoryAPI
from stack.web.content import SourceContent
from stacklets.docs.bot.archivist import ArchivistBot
from stacklets.docs.bot.archivist_services import ArchivistServices
from stacklets.docs.bot.capture_pipeline import CapturePipeline
from stacklets.docs.bot.extractors import TextExtractor
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.vault_context import VaultContext
from tests.memory.unit.instance import git, running_memory

BOT = "@archivist-bot:simpson"
HOMER, MARGE = "@homer:simpson", "@marge:simpson"
ALONE = [BOT, MARGE]
TOGETHER = [BOT, HOMER, MARGE]
QUILT, CAMPER = "!quilt:simpson", "!camper:simpson"

PEOPLE = {f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
          for pid, name in (("homer", "Homer Simpson"), ("marge", "Marge Simpson"),
                            ("lisa", "Lisa Simpson"))}

PAGE = SourceContent(text="How to piece a quilt top from scraps.", mime="text/html",
                     title_hint="Quilt top from scraps", source_uri="https://quilts.example/scraps")
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


# ── Stand-ins: the model, the web, the room's Matrix side ────────────────


class Model:
    """The classifier's script: a title from the words, a to-do when asked for.

    `asked_for_items` holds, for each answer, whether the pipeline asked
    for to-dos."""

    def __init__(self):
        self.asked_for_items: list[bool] = []

    async def classify_capture(self, *, text, extract_action_items=False, **_):
        self.asked_for_items.append(extract_action_items)
        answer = {"title": text.strip().split("\n")[0][:40] or "Capture", "summary": "s",
                  "tags": [], "persons": []}
        if extract_action_items:
            answer["action_items"] = ["Buy storm straps for the awning"]
        return answer


class Web:
    """The page every link leads to."""

    async def fetch(self, url):
        return SimpleNamespace(content=PAGE, url=url, verdict=SimpleNamespace(name="ok", ok=True),
                               ok=True)


class Tags:
    def top(self, n):
        return []

    def record(self, tags, when):
        pass

    def save(self):
        pass


class Matrix:
    """The Matrix client: the rooms it knows, the binding events posted to them,
    and the events a reaction can point at."""

    def __init__(self):
        self.rooms: dict[str, SimpleNamespace] = {}
        self.bound: dict[str, dict] = {}
        self.events: dict[str, SimpleNamespace] = {}

    async def room_messages(self, room_id, **_):
        content = self.bound.get(room_id)
        return SimpleNamespace(chunk=[SimpleNamespace(source={"content": content})]
                               if content else [])

    async def room_send(self, room_id, message_type, content, **_):
        self.bound[room_id] = content

    async def room_get_event(self, room_id, event_id):
        return SimpleNamespace(event=self.events[event_id])

    async def room_get_state_event(self, *_a, **_k):
        return SimpleNamespace()


def _room(room_id: str, name: str, members: list[str]) -> SimpleNamespace:
    return SimpleNamespace(room_id=room_id, name=name, display_name=name, canonical_alias=None,
                           users={uid: object() for uid in members})


def _event(event_id: str, sender: str, body: str, *, content: dict | None = None):
    return SimpleNamespace(event_id=event_id, sender=sender, body=body,
                           server_timestamp=1_700_000_000_000,
                           source={"content": {"msgtype": "m.text", "body": body, **(content or {})}})


# ── The archivist, as the bot runner builds it ───────────────────────────


@pytest.fixture
def memory(tmp_path, monkeypatch):
    with running_memory(tmp_path / "data", dict(PEOPLE), seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        yield memory


def build_archivist(memory, tmp_path, matrix: Matrix, name: str = "archivist") -> ArchivistBot:
    """An archivist over the memory and `matrix`; a second one over the same
    `matrix` is the bot after a restart: no binding cached in the process."""
    vault = VaultContext(language="en", shared_bucket="family")
    mirror = GitMirror(data_dir=tmp_path / name, vault=vault,
                       memory=MemoryAPI.from_container(memory.environ))
    model = Model()
    capture = CapturePipeline(
        url_extractor=Web(), text_extractor=TextExtractor(), classifier=model, mirror=mirror,
        capture_tags=Tags(), paperless=None, bot_name="archivist-bot", classify_max_chars=10000,
        capture_keep_body=False, capture_tag_prompt_size=50, vault=vault)
    services = ArchivistServices(paperless=None, classifier=model, url_extractor=Web(),
                                 text_extractor=TextExtractor(), capture_tags=Tags(), mirror=mirror,
                                 vault=vault, pipeline=None, search=None, capture=capture)
    bot = ArchivistBot(homeserver="http://h", user_id=BOT, password="x",
                       session_dir=tmp_path / f"{name}-session", services=services)
    bot._client = matrix
    bot.model = model

    async def nothing(*_a, **_k):
        return None

    async def media(_url):
        return PNG

    bot._send, bot._react, bot._set_typing, bot._download_media = nothing, nothing, nothing, media
    bot._rooms.welcome_if_needed = nothing
    return bot


@pytest.fixture
async def archivist(memory, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ARCHIVE_DIR", str(tmp_path / "media"))
    bots = [build_archivist(memory, tmp_path, Matrix())]
    yield bots[0]
    for bot in bots:
        if bot._http is not None:
            await bot._http.close()


def _join(bot, room_id: str, name: str, members: list[str]) -> SimpleNamespace:
    room = _room(room_id, name, members)
    bot._client.rooms[room_id] = room
    return room


def files_in(memory, pattern: str) -> list[str]:
    listed = git(memory.forgejo.repo, "ls-tree", "-r", "--name-only", "main").splitlines()
    return [p for p in listed if re.search(pattern, p)]


def card(memory, path: str) -> dict:
    return parse(memory.text(path))


# ── What a topic room files ──────────────────────────────────────────────


async def test_a_link_in_a_personal_topic_room_is_a_bookmark_in_the_topics_folder(archivist, memory):
    """Given Marge alone in "Topic: Quilt for Lisa", when she sends a link,
    then a bookmark is filed in her topic's folder, and the topic's id is
    its first tag."""
    room = _join(archivist, QUILT, "Topic: Quilt for Lisa", ALONE)

    await archivist._on_text(room, _event("$link", MARGE, "https://quilts.example/scraps"))

    [path] = files_in(memory, r"^marge/quilt-for-lisa/bookmarks/.*\.md$")
    assert card(memory, path)["tags"][0] == "quilt-for-lisa"


async def test_a_photo_with_a_caption_in_a_personal_topic_room_is_a_note_with_the_photo(archivist, memory):
    """Given Marge alone in "Topic: Quilt for Lisa", when she sends a photo
    with a caption, then a note is filed in her topic's folder, and it
    shows the photo and keeps her caption."""
    room = _join(archivist, QUILT, "Topic: Quilt for Lisa", ALONE)
    photo = _event("$photo", MARGE, "scraps.png", content={
        "msgtype": "m.image", "url": "mxc://simpson/scraps", "info": {"mimetype": "image/png"},
        "filename": "scraps.png"})
    photo.body = photo.source["content"]["body"] = "Scraps for Lisa's quilt"

    await archivist._on_file(room, photo)

    [path] = files_in(memory, r"^marge/quilt-for-lisa/notes/.*\.md$")
    written = memory.text(path)
    assert "![[" in written
    assert "Scraps for Lisa's quilt" in written
    assert card(memory, path)["tags"][0] == "quilt-for-lisa"


async def test_a_pasted_text_in_a_personal_topic_room_is_a_note_in_the_topics_folder(archivist, memory):
    """Given Marge alone in "Topic: Quilt for Lisa", when she pastes a long
    text, then a note is filed in her topic's folder."""
    room = _join(archivist, QUILT, "Topic: Quilt for Lisa", ALONE)
    pasted = "Cut the scraps into squares of ten centimetres.\n" + "Press each seam open. " * 20

    await archivist._on_text(room, _event("$paste", MARGE, pasted))

    [path] = files_in(memory, r"^marge/quilt-for-lisa/notes/.*\.md$")
    assert card(memory, path)["tags"][0] == "quilt-for-lisa"


async def test_a_short_line_in_a_shared_topic_room_files_nothing(archivist, memory):
    """Given Homer and Marge in "Topic: Camper van", when Homer sends a
    short line, then nothing is filed: two people talking is not a
    capture."""
    room = _join(archivist, CAMPER, "Topic: Camper van", TOGETHER)

    await archivist._on_text(room, _event("$line", HOMER, "storm straps are in the shed"))

    assert memory.forgejo.new_commits() == []


async def test_a_pinned_line_in_a_shared_topic_room_is_a_note_in_the_topics_folder_by_its_author(
        archivist, memory):
    """Given Homer's short line in "Topic: Camper van", when Homer pins it
    with 📌, then a note is filed in the shared topic's folder, in a commit
    by Homer."""
    room = _join(archivist, CAMPER, "Topic: Camper van", TOGETHER)
    line = _event("$line", HOMER, "storm straps are in the shed")
    archivist._client.events["$line"] = line

    await archivist._on_reaction(room, SimpleNamespace(sender=HOMER, key="📌", reacts_to="$line"))

    [path] = files_in(memory, r"^family/camper-van/notes/.*\.md$")
    assert card(memory, path)["tags"][0] == "camper-van"
    assert any(c.startswith("homer <homer@simpson> |") for c in memory.forgejo.new_commits())


async def test_a_voice_message_in_a_shared_topic_room_is_a_note_and_a_to_do_on_the_topics_list(
        archivist, memory):
    """Given Homer and Marge in "Topic: Camper van", when Homer sends a voice
    message "buy storm straps for the awning", then a note is filed in the
    topic's folder, and the to-do is on the camper's list, linked to the
    note."""
    room = _join(archivist, CAMPER, "Topic: Camper van", TOGETHER)
    words = "buy storm straps for the awning"
    spoken = _event("$voice", HOMER, words, content={voice.TRANSCRIPT_KEY: {"id": "t1"}})

    await archivist._on_text(room, spoken)

    [note] = files_in(memory, r"^family/camper-van/notes/.*\.md$")
    [todos] = files_in(memory, r"^family/lists/camper-van\.md$")
    assert re.search(rf"- \[ \] Buy storm straps for the awning \[.*\]\(\.\./{re.escape(note.removeprefix('family/'))}\)",
                     memory.text(todos))


async def test_a_capture_after_the_room_is_renamed_files_into_the_topics_folder(archivist, memory):
    """Given a shared topic room with a note filed, when the room is renamed
    "Topic: Camper van 2027", then the next capture files into the folder
    the topic has: the room keeps the binding it was given, and no second
    topic starts under the new name."""
    room = _join(archivist, CAMPER, "Topic: Camper van", TOGETHER)
    first = _event("$first", HOMER, "check the awning")
    archivist._client.events["$first"] = first
    await archivist._on_reaction(room, SimpleNamespace(sender=HOMER, key="📌", reacts_to="$first"))
    room.name = room.display_name = "Topic: Camper van 2027"

    second = _event("$second", HOMER, "order the straps")
    archivist._client.events["$second"] = second
    await archivist._on_reaction(room, SimpleNamespace(sender=HOMER, key="📌", reacts_to="$second"))

    assert len(files_in(memory, r"^family/camper-van/notes/.*\.md$")) == 2
    assert files_in(memory, r"^family/camper-van-2027/") == []


async def test_a_restarted_archivist_files_a_renamed_room_into_the_topics_folder(
        archivist, memory, tmp_path):
    """Given a shared topic room with a note filed, when the room is renamed
    and the archivist restarts, then the next capture files into the folder
    the topic has: the binding the room keeps says where, not the name."""
    room = _join(archivist, CAMPER, "Topic: Camper van", TOGETHER)
    archivist._client.events["$first"] = _event("$first", HOMER, "check the awning")
    await archivist._on_reaction(room, SimpleNamespace(sender=HOMER, key="📌", reacts_to="$first"))
    room.name = room.display_name = "Topic: Camper van 2027"
    restarted = build_archivist(memory, tmp_path, archivist._client, name="restarted")

    restarted._client.events["$second"] = _event("$second", HOMER, "order the straps")
    await restarted._on_reaction(room, SimpleNamespace(sender=HOMER, key="📌", reacts_to="$second"))

    assert len(files_in(memory, r"^family/camper-van/notes/.*\.md$")) == 2
    assert files_in(memory, r"^family/camper-van-2027/") == []


async def test_a_capture_in_a_personal_topic_room_touches_no_list(archivist, memory):
    """Given Marge alone in "Topic: Quilt for Lisa", when she sends a voice
    message with a to-do, then the note is filed and no list is written: a
    personal topic has none."""
    room = _join(archivist, QUILT, "Topic: Quilt for Lisa", ALONE)
    spoken = _event("$voice", MARGE, "buy batting for the quilt",
                    content={voice.TRANSCRIPT_KEY: {"id": "t2"}})

    await archivist._on_text(room, spoken)

    assert files_in(memory, r"^marge/quilt-for-lisa/notes/.*\.md$")
    assert archivist.model.asked_for_items == [True]  # a to-do was extracted
    assert files_in(memory, r"/lists/") == []


async def test_a_bot_posting_into_a_shared_topic_room_files_into_the_topics_folder(archivist, memory):
    """Given Homer and Marge in "Topic: Camper van", when the mail bot
    posts an email into the room, then it is filed in the shared topic's
    folder, not under the bot's own name."""
    room = _join(archivist, CAMPER, "Topic: Camper van", TOGETHER + ["@mail-bot:simpson"])
    source = {"source": "email", "subject": "Awning parts", "message_id": "<1@x>",
              "raw_content": "Your awning parts have shipped.", "from": "shop@example.org",
              "from_name": "Awning Shop", "captured_at": "2026-10-13"}
    posted = _event("$mail", "@mail-bot:simpson", "Awning parts",
                    content={"dev.famstack.source": source})

    await archivist._on_text(room, posted)

    assert files_in(memory, r"^family/camper-van/emails/.*\.md$")
    assert files_in(memory, r"^mail-bot/") == []


async def test_a_bot_posting_into_a_room_without_a_topic_files_under_the_rooms_scope_bucket(
        archivist, memory):
    """Given Marge alone with the mail bot in a plain room, when the mail
    bot posts an email, then it is filed under Marge, whose room it is, and
    not under the bot's own name."""
    room = _join(archivist, "!marge:simpson", "Marge's mail", ALONE + ["@mail-bot:simpson"])
    source = {"source": "email", "subject": "Fabric order", "message_id": "<2@x>",
              "raw_content": "Your fabric has shipped.", "from": "shop@example.org",
              "from_name": "Fabric Shop", "captured_at": "2026-10-13"}

    await archivist._on_text(room, _event("$mail", "@mail-bot:simpson", "Fabric order",
                                          content={"dev.famstack.source": source}))

    assert files_in(memory, r"^marge/emails/.*\.md$")
    assert files_in(memory, r"^mail-bot/") == []


async def test_a_bot_posting_first_into_a_personal_topic_room_files_under_the_person(
        archivist, memory):
    """Given Marge alone with the mail bot in "Topic: Quilt for Lisa", and
    no capture before, when the mail bot posts an email first, then it is
    filed in Marge's topic folder, and nothing is filed under the bot."""
    room = _join(archivist, QUILT, "Topic: Quilt for Lisa", ALONE + ["@mail-bot:simpson"])
    source = {"source": "email", "subject": "Batting sale", "message_id": "<3@x>",
              "raw_content": "Batting is half price.", "from": "shop@example.org",
              "from_name": "Fabric Shop", "captured_at": "2026-10-13"}

    await archivist._on_text(room, _event("$mail", "@mail-bot:simpson", "Batting sale",
                                          content={"dev.famstack.source": source}))

    assert files_in(memory, r"^marge/quilt-for-lisa/emails/.*\.md$")
    assert files_in(memory, r"^mail-bot/") == []
