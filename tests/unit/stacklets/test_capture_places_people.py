# ruff: noqa: F811  (a fixture imported from another test module is the test's parameter)
"""Who a capture's card is about: each name the model gave, placed against the household's people.

A name the model gives for a note or a bookmark is a person of the household
(an id on the card, a link in its About line), a name the household knows
without a person file (a plain name in the About line), or a name nobody goes
by (left off the card, kept in its words, reported in the archivist's log
with the card's id). A known person a note's title, description or summary
names joins the card; a bookmark and an email get nobody that way. With no
one left, the card is about its sender. `test_capture_names_people_and_tags.py`
and `test_capture_vocabulary_edges.py` state the rest; this file states the
edges of that rule.

All tests drive the capture pipeline as the archivist builds it, the mirror and
the real curator over a bare repository (`running_memory`), with one model
standing in over HTTP (`tests.household`). The household is the Simpsons.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from loguru import logger

import stack.ai.models as ai_models
from stack.frontmatter import parse
from stack.memory import MemoryAPI
from stacklets.docs.bot.archivist import ArchivistBot
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.pipeline import Classifier
from stacklets.docs.bot.vault_context import VaultContext
from tests.household import ONTOLOGY
from tests.memory.unit.instance import running_memory
from tests.unit.stacklets.test_capture_names_people_and_tags import (  # noqa: F401  (fixtures)
    ABOUT, LAKE, archivist, files, household, memory, mirror, pipeline, vault,
)

PLANT = "Mr. Burns called about the plant."


# ── A name nobody goes by ────────────────────────────────────────────────

def test_a_note_that_names_only_a_stranger_is_about_its_sender(archivist, memory, household):
    """Given Homer's note "Mr. Burns called about the plant" and a model
    that names only Mr. Burns, then the card is about Homer: the stranger
    is left off, and the sender is who the card is about. Why: a card about
    nobody is lost to every person page and every search by person."""
    household.says(persons=["Mr. Burns"])

    card = files(archivist, memory, PLANT, by="homer")

    assert card["persons"] == ["homer"]
    assert "Mr. Burns" not in card["about"]
    assert PLANT in memory.page(card["path"])


def test_a_name_nobody_goes_by_is_reported_once_with_the_cards_id(archivist, memory, household):
    """Given the model names Mr. Burns for Marge's note, then the archivist's
    log names him once, with the id of the card he was left off, so an
    admin can find the card and the family can add him as a person."""
    household.says(persons=["Homer", "Mr. Burns"])
    lines: list[str] = []
    sink = logger.add(lines.append, format="{message}")
    try:
        card = files(archivist, memory, "We met Mr. Burns at the lake.")
    finally:
        logger.remove(sink)

    reported = [line for line in lines if "Mr. Burns" in line]
    assert len(reported) == 1
    assert card["card_id"] in reported[0]


# ── Names in the words ───────────────────────────────────────────────────

def test_a_word_that_is_nobodys_name_puts_no_one_on_the_card(archivist, memory, household):
    """Given Lisa's note "Will it rain in May?" and a model that names
    nobody, then the card is about Lisa alone: only the household's own
    names count when the words are read, and nobody is called Will or May."""
    household.says(persons=[])

    assert files(archivist, memory, "Will it rain in May?", by="lisa")["persons"] == ["lisa"]


def test_a_capitalised_noun_that_is_nobodys_name_puts_no_one_on_the_card(archivist, memory, household):
    """Given Bart's note "Die Rose im Garten blüht." in a household with no
    Rose and no Garten, and a model that names nobody, then the card is
    about Bart alone: a capitalised noun is no name unless someone in the
    household goes by it. A member named Rose would be named by the flower,
    as on a diary entry; that trade-off is not pinned here."""
    household.says(persons=[])

    assert files(archivist, memory, "Die Rose im Garten blüht.", by="bart")["persons"] == ["bart"]


def bookmark_of(archivist, memory, by: str = "marge") -> dict:
    """`by` sends a link to a room; returns the bookmark card the memory then holds."""
    archivist._url_extractor = _Page()
    outcome = asyncio.run(archivist.capture_url(url="https://krusty.test/news",
                                                sender_mxid=f"@{by}:simpson", notifier=_Quiet()))
    assert outcome.status == "captured", outcome
    page = memory.page(outcome.vault_path)
    about = ABOUT.search(page)
    return {**parse(page), "path": outcome.vault_path, "about": about.group(1) if about else ""}


def test_a_bookmark_gets_no_person_from_its_summary(archivist, memory, household):
    """Given a link whose summary says "Lisa came along." and a model that
    names nobody, then the bookmark is about its sender only: a page's words
    are someone else's, so they name no one of the household."""
    household.says(persons=[], summary="Lisa came along.")

    assert bookmark_of(archivist, memory)["persons"] == ["marge"]


def test_a_bookmark_leaves_a_name_nobody_goes_by_off_the_card(archivist, memory, household):
    """Given a link for which the model names Homer and Mr. Burns, then the
    bookmark's About line links Homer and does not name Mr. Burns."""
    household.says(persons=["Homer", "Mr. Burns"])

    card = bookmark_of(archivist, memory)

    assert card["persons"] == ["homer"]
    assert "Mr. Burns" not in card["about"]


def test_a_corrected_bookmark_that_names_only_a_stranger_stays_its_senders(archivist, memory, household):
    """Given Marge's bookmark, when Homer corrects it and the model now
    names only Mr. Burns, then the bookmark is still about Marge: the one
    who sent it, as for a note, never the one who corrected it."""
    card = bookmark_of(archivist, memory, by="marge")
    household.says(persons=["Mr. Burns"])

    asyncio.run(archivist.reprocess(vault_path=card["path"], user_hint="Mr. Burns sent this",
                                    sender_mxid="@homer:simpson"))

    assert parse(memory.page(card["path"]))["persons"] == ["marge"]


def email_of(archivist, memory) -> str:
    """The school's mail, filed as the archivist files it; returns its card as the memory holds it."""
    outcome = asyncio.run(archivist.capture_email(
        subject="Field trip", body="The field trip is on Friday.",
        message_id="<trip-1@springfield-elementary.test>",
        thread_root="<trip-1@springfield-elementary.test>", sender_mxid="@mail-bot:simpson",
        from_addr="office@springfield-elementary.test", captured_at="2026-11-02", bucket="family"))
    assert outcome.status == "captured", outcome
    return memory.page(outcome.vault_path)


def test_an_email_gets_no_person_from_its_summary(archivist, memory, household):
    """Given a mail whose summary says "Lisa came along." and a model that
    names nobody, then the email's card is about nobody: neither the words
    nor the mail bot that posted it make a person."""
    household.says(persons=[], summary="Lisa came along.")

    assert not parse(email_of(archivist, memory)).get("persons")


def test_an_email_keeps_the_names_as_the_model_gave_them(archivist, memory, household):
    """Given a mail for which the model names Homer and Mr. Burns, then its
    card is as today: Homer by id, and Mr. Burns as a plain name in its
    About line. The rule for notes does not reach email."""
    household.says(persons=["Homer", "Mr. Burns"])

    page = email_of(archivist, memory)

    assert parse(page)["persons"] == ["homer"]
    assert "Mr. Burns" in page.split("<!-- mid:")[0]


# ── Spellings and households ─────────────────────────────────────────────

def test_a_spelling_only_on_a_wiki_person_page_is_that_person(archivist, memory, household, monkeypatch):
    """Given the wiki's page of Homer lists "Homie", a spelling his person
    file does not have, when the model names "Homie", then the card is
    about Homer, as a diary entry naming "Homie" is."""
    page = memory.brain / "homer" / "about.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\nkind: person\ncanonical: Homer\nsynonyms:\n  - Homie\n---\n\n# Homer\n")
    monkeypatch.setenv("BRAIN_REPO_DIR", str(memory.brain))
    household.says(persons=["Homie"])

    assert files(archivist, memory)["persons"] == ["homer"]


@pytest.fixture
def unpeopled(tmp_path, household, monkeypatch):
    """A household that declared no person yet, whose accounts are Homer,
    Marge, Bart and Lisa: its archivist's capture pipeline and its memory."""
    monkeypatch.setenv("FAMILY_NAMES", "Homer,Marge,Bart,Lisa")
    with running_memory(tmp_path / "data", {"ontology.toml": ONTOLOGY}, seeded_by="memory-bot",
                        seeded_as="seed: no people", model=household.url) as running:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(running.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        monkeypatch.setitem(ai_models._MODELS, "archivist-bot", "stand-in")
        vault = VaultContext(language="en", shared_bucket="family")
        mirror = GitMirror(data_dir=tmp_path / "archivist", vault=vault,
                           memory=MemoryAPI.from_container(running.environ))
        yield pipeline(mirror, vault, Classifier(household.llm(), language="en"),
                       paperless=_NoPersonTags()), running


def test_without_person_files_the_family_names_are_plain_names_on_the_card(unpeopled, household):
    """Given a household without person files, when the model names Lisa
    and Mr. Burns for Marge's note, then the About line names Lisa as a
    plain name and not Mr. Burns, and `persons` is empty: there is no id
    to put in it."""
    household.says(persons=["Lisa", "Mr. Burns"])

    card = files(*unpeopled)

    assert not card.get("persons")
    assert card["about"] == "Lisa"


def test_without_person_files_a_note_naming_nobody_is_about_its_sender(unpeopled, household):
    """Given a household without person files, when the model names nobody
    for Marge's note, then its About line names Marge: the sender is
    known in every household."""
    household.says(persons=[])

    assert files(*unpeopled)["about"] == "Marge"


def test_without_person_files_the_reply_and_the_event_name_who_the_card_names(unpeopled, household, tmp_path):
    """Given a household without person files, when the model names Lisa
    for Marge's note, then the answer in her room and the capture event
    name Lisa, as the card's About line does."""
    household.says(persons=["Lisa", "Mr. Burns"])
    archivist, _ = unpeopled

    outcome = asyncio.run(archivist.capture_text(text=LAKE, sender_mxid="@marge:simpson"))

    assert outcome.envelope["data"]["persons"] == ["Lisa"]
    assert "Lisa" in people_line_of(answer_to(outcome, tmp_path))


# ── What the room is told ────────────────────────────────────────────────

def test_the_reply_names_the_cards_people_as_the_family_knows_them(archivist, memory, household, tmp_path):
    """Given the model names "Mr. Simpson" and Mr. Burns for Marge's note,
    when the archivist answers in her room, then the answer names Homer by
    his name, never by his id `homer`, and does not name Mr. Burns."""
    household.says(persons=["Mr. Simpson", "Mr. Burns"])
    outcome = asyncio.run(archivist.capture_text(text="We met Mr. Burns.", sender_mxid="@marge:simpson"))

    people_line = people_line_of(answer_to(outcome, tmp_path))

    assert "Homer" in people_line and "homer" not in people_line
    assert "Burns" not in people_line


def answer_to(outcome, tmp_path) -> str:
    """The archivist's answer to `outcome` in Marge's room, as she reads it."""
    bot = ArchivistBot(homeserver="http://h", user_id="@archivist-bot:simpson", password="x",
                       session_dir=tmp_path / "session",
                       services=SimpleNamespace(vault=VaultContext(language="en", shared_bucket="family")))
    sent: list[str] = []

    async def _send(room_id, text, reply_to=None, **_):
        sent.append(text)

    async def _react(room_id, event_id, key):
        pass

    bot._send, bot._react = _send, _react
    asyncio.run(bot._captures.reply("!marge:simpson", outcome, "$note"))
    [answer] = sent
    return answer


def people_line_of(answer: str) -> str:
    """The line of an answer that lists who the card is about."""
    return next(line for line in answer.splitlines() if "\U0001F464" in line)


class _Page:
    """The page behind a link, read cleanly."""

    async def fetch(self, url):
        return SimpleNamespace(url=url, verdict=None, content=SimpleNamespace(
            text="Krusty Burger opens a new branch.", source_uri=url, title_hint="Krusty Burger"))


class _NoPersonTags:
    """Paperless with no `Person:` tag: the prompt asks it for names when the memory has none."""

    async def get_tags(self):
        return []


class _Quiet:
    """The notifier a link capture acknowledges through."""

    async def acknowledge(self):
        pass

