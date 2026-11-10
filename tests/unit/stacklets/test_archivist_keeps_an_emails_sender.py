"""An email's card keeps who sent it: the name and the address of its thread's first message.

The mail bot posts each email it fetches into a room as a source event
(`dev.famstack.source`), with the sender's name and address among its
fields. The archivist files the email as a card in the family memory, one
card per thread. The card's `from_name` and `from_address` say who sent
the thread's first message, the one its `resource` names, so the card's
page can say "From" without reading the thread's text.

These tests drive the archivist as the bot runner loads it: the source
event goes to its message handler, through its real capture pipeline and
mirror, into the curator of memory's test instance (`running_memory`),
whose card check refuses a field the email schema does not name. What a
test asserts is the card Forgejo then holds. The classifier is a stand-in:
what it says is not under test. The household is the Simpsons; the school
is Springfield Elementary.
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
MAIL_BOT = "@mail-bot:simpson"
FILES = {f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
         for pid, name in (("homer", "Homer Simpson"), ("marge", "Marge Simpson"),
                           ("bart", "Bart Simpson"), ("lisa", "Lisa Simpson"))}
ROOT = "trip-1@springfield-elementary.example"
OFFICE = {"from_name": "Springfield Elementary office",
          "from": "office@springfield-elementary.example"}


def _email(message_id: str, words: str, **sender) -> dict:
    """The source block the mail bot posts for one message of the field trip
    thread, as `post_source_message` builds it from `_source_fields`."""
    return {"source": "email", "raw_content": words, "subject": "Field trip to the plant",
            "message_id": message_id, "thread_root": ROOT, "captured_at": "2026-11-05",
            "account": "family", "folder": "INBOX", **sender}


class Classifier:
    async def classify_capture(self, **_):
        return {"title": "Field trip to the plant", "persons": ["Bart"], "tags": ["school"],
                "summary": "The class visits the plant on Friday."}


class Tags:
    def top(self, n):
        return []

    def record(self, tags, when):
        pass

    def save(self):
        pass


@pytest.fixture
def memory(tmp_path, monkeypatch):
    with running_memory(tmp_path / "data", FILES, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        # The archivist reads people from the curator's clone, as the bot
        # runner mounts it.
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        yield memory


@pytest.fixture
def archivist(tmp_path, memory) -> ArchivistBot:
    """The archivist with its real capture pipeline and mirror; the Matrix
    side records nothing and answers nothing."""
    vault = VaultContext(language="en", shared_bucket="family")
    mirror = GitMirror(data_dir=tmp_path / "archivist", vault=vault,
                       memory=MemoryAPI.from_container(memory.environ))
    capture = CapturePipeline(
        url_extractor=None, text_extractor=None, classifier=Classifier(), mirror=mirror,
        capture_tags=Tags(), paperless=None, bot_name="archivist-bot",
        classify_max_chars=10000, capture_keep_body=False, capture_tag_prompt_size=50,
        vault=vault)
    bot = ArchivistBot(homeserver="http://h", user_id=BOT_ID, password="x",
                       session_dir=tmp_path / "session",
                       services=SimpleNamespace(capture=capture))

    async def _quiet(*_args, **_kwargs):
        return None

    bot._send = bot._react = bot._send_room_welcome_if_needed = _quiet
    return bot


async def _posted(archivist: ArchivistBot, block: dict) -> None:
    """The mail bot's source event, as the archivist receives it in the mail room."""
    room = SimpleNamespace(room_id="!mail:simpson", canonical_alias=None, name=None, users={})
    event = SimpleNamespace(body="**Field trip to the plant**", sender=MAIL_BOT,
                            event_id=f"$${block['message_id']}", server_timestamp=1,
                            source={"content": {"dev.famstack.source": block}})
    await archivist._on_text(room, event)


def _email_card(memory) -> dict:
    [card] = memory.pages("family/emails").values()
    return parse(card)


async def test_a_filed_email_keeps_its_senders_name_and_address(archivist, memory):
    """Given the school office's email in the mail room, when the archivist
    files it, then its card holds the sender's name and address, and the
    curator's card check accepted the card."""
    await _posted(archivist, _email(ROOT, "The class visits the plant on Friday.", **OFFICE))

    card = _email_card(memory)
    assert card["resource"] == f"mid:{ROOT}"
    assert (card["from_name"], card["from_address"]) == (
        "Springfield Elementary office", "office@springfield-elementary.example")


async def test_a_reply_from_someone_else_keeps_the_first_senders_fields(archivist, memory):
    """Given the office's email is filed, when Principal Skinner's reply in
    the same thread is filed, then the card still names the office: the
    sender is the first message's, which the card's `resource` names."""
    await _posted(archivist, _email(ROOT, "The class visits the plant on Friday.", **OFFICE))
    await _posted(archivist, _email("trip-2@springfield-elementary.example", "Bring a lunch.",
                                    from_name="Seymour Skinner",
                                    **{"from": "skinner@springfield-elementary.example"}))

    card = _email_card(memory)
    assert (card["from_name"], card["from_address"]) == (
        "Springfield Elementary office", "office@springfield-elementary.example")


async def test_a_mail_that_names_only_an_address_keeps_the_address(archivist, memory):
    """Given an email whose From header has no name, when it is filed, then
    its card holds the address and no empty name."""
    await _posted(archivist, _email(ROOT, "The class visits the plant on Friday.",
                                    **{"from": OFFICE["from"]}))

    card = _email_card(memory)
    assert card["from_address"] == "office@springfield-elementary.example"
    assert "from_name" not in card


async def test_a_reply_filed_before_its_first_message_waits_for_it(archivist, memory):
    """Given a thread's reply reaches the archivist before its first message,
    when the reply is filed, then the card names no sender, because the
    reply's sender did not start the thread its `resource` names; when the
    first message follows, then the card names the office."""
    await _posted(archivist, _email("trip-2@springfield-elementary.example", "Bring a lunch.",
                                    from_name="Seymour Skinner",
                                    **{"from": "skinner@springfield-elementary.example"}))

    card = _email_card(memory)
    assert "from_name" not in card and "from_address" not in card

    await _posted(archivist, _email(ROOT, "The class visits the plant on Friday.", **OFFICE))

    card = _email_card(memory)
    assert (card["from_name"], card["from_address"]) == (
        "Springfield Elementary office", "office@springfield-elementary.example")
