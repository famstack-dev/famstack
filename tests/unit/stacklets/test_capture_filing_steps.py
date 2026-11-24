"""The steps that file a capture, each pinned where a person sees its result.

A capture files through one sequence of steps: the card's place, the people,
the tags, the write, the after-steps, and the event the room reads. Most of
them are pinned by the tests of what a person sends (`test_archivist_files_what_marge_sends.py`)
and of the mirror (`test_archivist_files_into_the_memory.py`). These tests pin
the rest, one step each:

- an email's correction is the corrector's commit;
- a correction puts a note's to-do on its topic's list once, linked to the note;
- a to-do the correction's words do not name stays off the list;
- a correction's event carries what the person wrote;
- an email keeps the names it was read with, because its sender is the mail bot.

They drive the capture pipeline as the archivist builds it, with its real
mirror, into the curator of memory's test instance (`running_memory`). The
model and the web are stand-ins (`tests/archivist_chat.py`): what they say
is not under test. The household is the Simpsons.
"""

from __future__ import annotations

import pytest

from stack.frontmatter import parse
from stack.lists import list_path
from stack.memory import MemoryAPI
from stacklets.docs.bot.capture_pipeline import CapturePipeline
from stacklets.docs.bot.extractors import TextExtractor
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.vault_context import VaultContext
from tests.archivist_chat import ModelAnswers, Pages
from tests.memory.unit.instance import running_memory

HOMER, MARGE, MAIL_BOT = "@homer:simpson", "@marge:simpson", "@mail-bot:simpson"
PEOPLE = {f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
          for pid, name in (("homer", "Homer Simpson"), ("marge", "Marge Simpson"),
                            ("bart", "Bart Simpson"), ("lisa", "Lisa Simpson"))}
TENT = "https://krusty.example/tents/two-person"
TENT_PAGE = "The two-person tent packs to 40 cm and weighs 2.1 kg."
TRIP = {"title": "Field trip to the plant", "persons": ["Bart", "Principal Skinner"],
        "tags": ["school"], "summary": "The class visits the plant on Friday."}


class Notifier:
    """The room's side of a capture: the pipeline acknowledges and says how it goes."""

    async def acknowledge(self):
        pass

    async def status(self, key, **_):
        pass


@pytest.fixture
def memory(tmp_path, monkeypatch):
    with running_memory(tmp_path / "data", PEOPLE, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        # The archivist reads people, lists and topics from the curator's
        # clone, as the bot runner mounts it.
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        yield memory


def a_pipeline(tmp_path, memory, model: ModelAnswers, *, pages: Pages | None = None) -> CapturePipeline:
    """The archivist's capture pipeline and mirror, as the bot builds them."""
    vault = VaultContext(language="en", shared_bucket="family")
    mirror = GitMirror(data_dir=tmp_path / "archivist", vault=vault,
                       memory=MemoryAPI.from_container(memory.environ))
    return CapturePipeline(
        url_extractor=pages or Pages(), text_extractor=TextExtractor(), classifier=model,
        mirror=mirror, paperless=None, bot_name="archivist-bot", classify_max_chars=10_000,
        capture_keep_body=False, vault=vault)


def author_of_last_commit(memory) -> str:
    """Who the newest commit in Forgejo is by, as `name <email>`."""
    return memory.forgejo.new_commits()[-1].split(" | ")[0]


# ── An email ──────────────────────────────────────────────────────────


async def an_email(pipeline: CapturePipeline):
    """The school's email about the field trip, as the mail bot posts it."""
    return await pipeline.capture_email(
        subject="Field trip to the plant", body="The class visits the plant on Friday.",
        message_id="trip-1@springfield-elementary.example", sender_mxid=MAIL_BOT,
        thread_root="trip-1@springfield-elementary.example",
        from_addr="office@springfield-elementary.example", captured_at="2026-11-05",
        bucket="family", from_name="Springfield Elementary office")


async def test_an_email_keeps_the_names_it_was_read_with(tmp_path, memory):
    """Given the model reads "Bart" and "Principal Skinner" in an email,
    when it is filed, then the outcome names both as written. Its sender is
    the mail bot, so the household's people are not looked up for it, and a
    name nobody in the family goes by is not dropped."""
    outcome = await an_email(a_pipeline(tmp_path, memory, ModelAnswers(TRIP)))

    assert outcome.status == "captured"
    assert outcome.classification["persons"] == ["Bart", "Principal Skinner"]


async def test_an_emails_correction_is_the_commit_of_the_person_who_corrected_it(tmp_path, memory):
    """Given the school's email filed by the mail bot, when Homer corrects
    it, then the commit that rewrites its card is Homer's: `git log --author`
    names who changed a card."""
    pipeline = a_pipeline(tmp_path, memory, ModelAnswers(TRIP, {**TRIP, "tags": ["school", "trip"]}))
    filed = await an_email(pipeline)

    outcome = await pipeline.reprocess(vault_path=filed.vault_path, user_hint="it is a trip",
                                       sender_mxid=HOMER, reply_text="it is a trip")

    assert outcome.status == "reclassified"
    assert author_of_last_commit(memory).startswith("homer ")


# ── A correction ──────────────────────────────────────────────────────


async def test_a_corrections_event_carries_what_the_person_wrote(tmp_path, memory):
    """Given Marge's saved link, when she corrects it, then the event the
    other bots read says what she wrote: a bot that follows the room learns
    why the card changed."""
    pipeline = a_pipeline(tmp_path, memory, ModelAnswers({"title": "Two-person tent", "summary": "A tent."}),
                          pages=Pages(**{TENT: TENT_PAGE}))
    filed = await pipeline.capture_url(url=TENT, sender_mxid=MARGE, notifier=Notifier())

    outcome = await pipeline.reprocess(vault_path=filed.vault_path, user_hint="it is for camping",
                                       sender_mxid=MARGE)

    assert outcome.envelope["type"] == "capture.reclassified"
    assert outcome.envelope["data"]["user_hint"] == "it is for camping"


# ── A note's to-dos ───────────────────────────────────────────────────


async def test_a_correction_puts_the_notes_todo_on_the_list_once_linked_to_the_note(tmp_path, memory):
    """Given Marge's note in the camping room with nothing to do in it, when
    a correction makes it say "Call the campsite", then the camping list
    gets that item, linked to the note's card where it is; when a second
    correction words it differently, the list still holds it once, and a
    third that names nothing to do takes nothing off. The model words an
    item differently each time, and the family may have ticked it."""
    first = {"title": "Campsite", "summary": "", "facts": [], "action_items": []}
    model = ModelAnswers(first)
    model.corrections += [{"clean": f"Campsite before Friday. {todo}.", "action_items": [todo]}
                          for todo in ("Call the campsite", "Phone the campsite")]
    model.corrections.append({"date": "2026-11-27"})
    pipeline = a_pipeline(tmp_path, memory, model)
    filed = await pipeline.capture_text(text="campsite b4 fri", sender_mxid=MARGE,
                                        capture_id="$campsite", bucket="family/camping",
                                        seed_topics=["camping"])

    for at, words in (("09:00", "call them"), ("10:00", "phone them"), ("11:00", "it is the 27th")):
        await pipeline.reprocess(vault_path=filed.vault_path, user_hint=words, sender_mxid=MARGE,
                                 capture_id="$campsite", reply_text=words, reply_id=f"$reply-{at}",
                                 reply_at=f"2026-11-22T{at}:00+00:00")

    camping = memory.text(list_path("camping", bucket="family"))
    assert [line for line in camping.splitlines() if line.startswith("- [ ]")] == [
        f"- [ ] Call the campsite [Campsite](../{filed.vault_path.removeprefix('family/')})"]
    assert "Call the campsite" in model.conversations[-1][-1]["content"], "a later one is shown the list"
    assert parse(memory.text(filed.vault_path))["tags"][0] == "camping"


async def test_a_todo_the_correction_does_not_name_stays_off_the_list(tmp_path, memory):
    """Given Marge's bake-sale note in the school room, when Lisa corrects
    the number of cups and the model answers a to-do about Bart, then the
    school list stays as it was: no word of that item stands in the
    correction. A wrong item on the family's list is worse than a missing one."""
    first = {"title": "Bake sale", "summary": "", "facts": [], "action_items": []}
    model = ModelAnswers(first)
    model.corrections.append({"clean": "Bake sale on Friday. We need 50 cups.",
                              "action_items": ["Bart helps at the stand"]})
    pipeline = a_pipeline(tmp_path, memory, model)
    filed = await pipeline.capture_text(text="bake sale fri, we need 30 cups", sender_mxid=MARGE,
                                        capture_id="$bake", bucket="family/school",
                                        seed_topics=["school"])

    await pipeline.reprocess(vault_path=filed.vault_path, user_hint="it's 50 cups",
                             sender_mxid=MARGE, capture_id="$bake", reply_text="it's 50 cups",
                             reply_id="$reply-cups", reply_at="2026-11-22T09:00:00+00:00")

    school = memory.text(list_path("school", bucket="family")) or ""
    assert "Bart helps" not in school


async def test_a_corrected_fact_from_a_voice_message_lands_in_the_text_and_the_rest_stays(
        tmp_path, memory):
    """Given Marge's voice message about Bart's school trip, which the
    transcript heard as 30 euros, when she replies that it is 13 euros, then
    the card's text says 13 euros and still says to pay by Friday and to
    pack a lunch. Most corrections are such a fact in the content."""
    transcript = "Bart's school trip costs 30 euros, we pay by Friday, and he needs a packed lunch."
    first = {"title": "School trip", "summary": "", "facts": [], "action_items": [],
             "clean": "Bart's school trip costs 30 euros. Pay by Friday. He needs a packed lunch."}
    model = ModelAnswers(first)
    model.corrections.append(
        {"clean": "Bart's school trip costs 13 euros. Pay by Friday. He needs a packed lunch."})
    pipeline = a_pipeline(tmp_path, memory, model)
    filed = await pipeline.capture_text(text=transcript, sender_mxid=MARGE, capture_id="$trip",
                                        bucket="family/school", seed_topics=["school"],
                                        transcribed=True)

    await pipeline.reprocess(vault_path=filed.vault_path, user_hint="it's 13 euros, not 30",
                             sender_mxid=MARGE, capture_id="$trip",
                             reply_text="it's 13 euros, not 30", reply_id="$reply-13",
                             reply_at="2026-11-22T09:00:00+00:00")

    body = (memory.text(filed.vault_path) or "").split("## Sources")[0]
    assert "13 euros" in body and "30 euros" not in body
    assert "Pay by Friday" in body and "packed lunch" in body


THE_FAMILY = ["Homer", "Marge", "Bart", "Lisa"]


@pytest.mark.parametrize(("filed_with", "words", "answered", "persons"), [
    (["Homer"], "Marge was there too", ["Marge"], ["homer", "marge"]),
    (THE_FAMILY, "only me", {"remove": ["Marge", "Bart", "Lisa"]}, ["homer"]),
    (THE_FAMILY, "Marge and Bart were there", {"remove": ["Homer", "Lisa"]}, ["marge", "bart"]),
    (THE_FAMILY, "it was 13 euros, not 30", {"remove": ["Marge"]}, ["homer", "marge", "bart", "lisa"]),
])
async def test_a_correction_says_who_was_there_and_only_then_the_people_change(
        tmp_path, memory, filed_with, words, answered, persons):
    """Given Homer's note, when he corrects who was there, then the people
    are the card's with the model's additions and its explicit removals: a
    whole list answered ("Marge" for "Marge was there too") only adds, so
    nobody is lost by the model leaving them out. When he corrects a fact
    and names nobody, the people stay as they were, whatever the model
    answers. The model often puts the whole family on a card, and a
    correction is how it is set right."""
    first = {"title": "Texxon", "summary": "", "facts": [], "action_items": [],
             "persons": filed_with, "clean": "Fuel at Texxon, 30 euros."}
    model = ModelAnswers(first)
    model.corrections.append({"persons": answered})
    pipeline = a_pipeline(tmp_path, memory, model)
    filed = await pipeline.capture_text(text="fuel at texxon 30 euros", sender_mxid="@homer:simpson",
                                        capture_id="$fuel", bucket="family")
    assert len(parse(memory.text(filed.vault_path))["persons"]) == len(filed_with)

    await pipeline.reprocess(vault_path=filed.vault_path, user_hint=words,
                             sender_mxid="@homer:simpson", capture_id="$fuel", reply_text=words,
                             reply_id="$who", reply_at="2026-11-22T09:00:00+00:00")

    assert sorted(parse(memory.text(filed.vault_path))["persons"]) == sorted(persons)
