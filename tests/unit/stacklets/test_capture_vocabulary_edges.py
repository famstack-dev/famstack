# ruff: noqa: F811  (a fixture imported from another test module is the test's parameter)
"""The edges of a capture's vocabulary: who may see what, what the reply shows, what a correction does.

`test_capture_names_people_and_tags.py` states which people and tags a note
gets. This file states what surrounds it, one test per case, all through the
real path (the capture pipeline as the archivist builds it, the mirror, the
real curator over a bare repository) and one model for both engines
(`tests.household`). The household is the Simpsons.

A test of behavior that is wrong today carries a marker of `tests.household`
(a strict expected failure whose reason states the wanted behavior); the
mark goes when the behavior is built. A test without a marker protects
behavior that is right.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone


import stack.ai.models as ai_models
from stack.frontmatter import parse
from stack.memory import MemoryAPI
from stacklets.docs.bot.capture_tags import CaptureTagCache
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.pipeline import Classifier
from stacklets.docs.bot.vault_context import VaultContext
from tests.household import ONTOLOGY, audience_vocabulary, note_people, renamed_topics
from tests.memory.unit.instance import running_memory
from tests.unit.stacklets.test_capture_names_people_and_tags import (  # noqa: F401  (fixtures)
    ABOUT, CAMPING, archivist, files, household, memory, mirror, pipeline, tag_cache, vault,
    )


# ── Who sees what ────────────────────────────────────────────────────────

def test_homers_prompt_shows_nothing_of_marges_personal_topic(archivist, memory, household):
    """Given Marge's personal topic card "Homers 40th", when Homer files a
    note into the family's bucket, then the prompt the model reads for Homer shows no word of it."""
    household.says(persons=["Homer"], tags=["Camper Van"])

    files(archivist, memory, by="homer", bucket="family")

    assert "40th" not in household.prompts[-1]


@audience_vocabulary
def test_homers_card_never_carries_marges_personal_topic(archivist, memory, household):
    """Given Marge's personal topic card `surprise-party`, titled "Homers
    40th", when the model answers "Homers 40th" for Homer's note, then
    Homer's card is not tagged `surprise-party`: his bucket is not hers.

    Today the write maps the title onto her topic, because every topic card
    in the memory is a topic for every writer."""
    household.says(persons=["Homer"], tags=["Homers 40th", "Camper Van"])

    card = files(archivist, memory, by="homer", bucket="family")

    assert "surprise-party" not in card["tags"]


# ── Who a note is about ──────────────────────────────────────────────────

def test_a_spelling_from_a_persons_page_is_that_person(archivist, memory, household):
    """Given Homer's page lists "Mr. Simpson", when the model names
    "Mr. Simpson", then the card is about Homer."""
    household.says(persons=["Mr. Simpson"])

    assert files(archivist, memory)["persons"] == ["homer"]


@note_people
def test_a_person_the_text_names_joins_the_card_though_the_model_left_her_out(archivist, memory, household):
    """Given a note whose text names Lisa and a model that names only Homer,
    then Lisa is on the card as well, as on a diary entry, whose people are
    those the model listed and those its title names.

    Today only the model's list counts."""
    household.says(persons=["Homer"])

    card = files(archivist, memory, "Lisa baked a cake for Homer")

    assert card["persons"] == ["homer", "lisa"]


# ── What the reply shows ─────────────────────────────────────────────────

def filed_with_unknown_name(archivist, memory, household):
    """Marge files a note for which the model names Homer, Mr. Burns (whom
    the household does not know) and the keywords "LLMs" and "llm"; returns
    the outcome and the card the memory holds."""
    household.says(persons=["Homer", "Mr. Burns"], tags=["LLMs", "llm"])
    outcome = asyncio.run(archivist.capture_text(text=CAMPING, sender_mxid="@marge:simpson"))
    return outcome, parse(memory.page(outcome.vault_path))


@note_people
def test_the_reply_is_built_from_the_cards_people_and_tags(archivist, memory, household):
    """Given the model names "Mr. Burns", whom the household does not know,
    when the note is filed, then the classification the reply reads holds
    the card's person ids and tags, not the model's raw names.

    Today it holds the model's answer as it came."""
    outcome, card = filed_with_unknown_name(archivist, memory, household)

    assert outcome.classification["persons"] == card["persons"]
    assert outcome.classification["tags"] == card["tags"]


@note_people
def test_the_capture_event_carries_the_cards_people_and_tags(archivist, memory, household):
    """Given the same note, then the event the archivist emits has the card's
    people and tags in `data.persons` and `data.tags`.

    Today they are the model's names and words."""
    outcome, card = filed_with_unknown_name(archivist, memory, household)

    assert outcome.envelope["data"]["persons"] == card["persons"]
    assert outcome.envelope["data"]["tags"] == card["tags"]


def about_line(page: str) -> str:
    """The line that says who a document is about (`**About:**` in its header line)."""
    return next(line for line in page.splitlines() if "About" in line)


# ── A document ───────────────────────────────────────────────────────────

def test_a_document_keeps_its_people_and_tags(mirror, memory):
    """Given a document Paperless read, with the people Homer and Mr. Burns
    and the tags "Camper Van", "Health" and "Invoice", when the archivist
    files it, then its card has Homer's id, the ids of the two topics and
    the keyword, and the About line keeps the name nobody goes by as
    written. Pinned to protect the documents path while captures change."""
    filed = asyncio.run(mirror.publish(
        paperless_id=301, body_text="Two cases of Duff.", processing="ai_formatted", model=None,
        paperless_url="http://docs.home.test", summary=None, tags=["Camper Van", "Health", "Invoice"],
        classification={"title": "Duff Beer invoice", "date": "2026-10-13",
                        "persons": ["Homer", "Mr. Burns"]}))
    page = memory.page(next(iter(memory.pages("family/documents"))))

    assert filed.card_id.startswith("document-")
    assert parse(page)["persons"] == ["homer"]
    assert parse(page)["tags"] == ["van", "health", "invoice"]
    assert "Mr. Burns" in about_line(page)


# ── A correction of an old note ──────────────────────────────────────────

@note_people
def test_a_reply_that_corrects_a_note_leaves_the_card_in_the_vocabulary(archivist, memory, household):
    """Given an old note, when someone replies in its thread and the model
    names "Mr. Burns", then the corrected card's people are the declared
    ones and its tags are the topic card's id and the keywords as spelled.

    Today the correction writes the name Mr. Burns on the card."""
    household.says(persons=["Homer"], tags=["Camper Van"])
    note = files(archivist, memory)
    household.says(persons=["Homer", "Mr. Burns"], tags=["Camper Van", "LLMs", "llm"])

    asyncio.run(archivist.reprocess(
        vault_path=note["path"], user_hint="Mr. Burns drove too", sender_mxid="@lisa:simpson",
        reply_text="Mr. Burns drove too", reply_id="$reply:simpson",
        reply_at=datetime.now(timezone.utc).isoformat(timespec="seconds")))

    card = parse(memory.page(note["path"]))
    assert card["persons"] == ["homer"]
    assert card["tags"] == ["van", "llms", "llm"]
    assert "Mr. Burns" not in memory.page(note["path"]).split("## Sources")[0]


# ── A household with no person files ─────────────────────────────────────

class _Paperless:
    """Paperless with no `Person:` tag: the archivist asks it for names when the memory has none."""

    async def get_tags(self):
        return []


def test_without_person_files_the_sender_stays_the_cards_person(tmp_path, household, monkeypatch):
    """Given a household that declared no person yet, when Marge files a
    note, then the card is about Marge: her name is in the About line. (Its
    `persons` field is empty, as no id exists to put in it.)"""
    with running_memory(tmp_path / "data", {"ontology.toml": ONTOLOGY}, seeded_by="memory-bot",
                        seeded_as="seed: no people", model=household.url) as running:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(running.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        monkeypatch.setitem(ai_models._MODELS, "archivist-bot", "stand-in")
        vault = VaultContext(language="en", shared_bucket="family")
        cache = CaptureTagCache(tmp_path / "tags.json")
        cache.load()
        mirror = GitMirror(data_dir=tmp_path / "archivist", vault=vault,
                           memory=MemoryAPI.from_container(running.environ))
        archivist = pipeline(mirror, vault, Classifier(household.llm(), language="en"), cache,
                             paperless=_Paperless())

        card = files(archivist, running)

    assert "marge" in card["about"].lower()


# ── A renamed topic ──────────────────────────────────────────────────────

@renamed_topics
def test_a_tag_naming_a_renamed_topic_gets_the_new_id(archivist, memory, household):
    """Given the topic "Quilt for Lisa" was renamed "Lisa's Quilt" (id
    `lisas-quilt`), when the model still answers the old title and the old id,
    then the card is tagged with the new id once.

    Today the old id is kept as a keyword, and the tag names a topic that no
    longer exists."""
    renamed = memory.person("marge").runs("memory", "topic", ["rename", "quilt-for-lisa", "lisas-quilt",
                                                                "Lisa's Quilt"])
    assert renamed.get("committed") is True, renamed
    household.says(persons=["Lisa"], tags=["Quilt for Lisa", "quilt-for-lisa"])

    assert files(archivist, memory, "Fabric for the quilt")["tags"] == ["lisas-quilt"]
