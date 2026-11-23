"""The steps that file a capture, each pinned where a person sees its result.

A capture files through one sequence of steps: the card's place, the people,
the tags, the write, the after-steps, and the event the room reads. Most of
them are pinned by the tests of what a person sends (`test_archivist_files_what_marge_sends.py`)
and of the mirror (`test_archivist_files_into_the_memory.py`). These tests pin
the rest, one step each:

- an email's correction is the corrector's commit;
- a correction puts a note's to-do on its topic's list once, linked to the note;
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
    correction words it differently, the list still holds it once. The model
    words an item differently each time, and the family may have ticked it."""
    first = {"title": "Campsite", "summary": "", "facts": [], "action_items": []}
    pipeline = a_pipeline(tmp_path, memory, ModelAnswers(
        first, {**first, "action_items": ["Call the campsite"]},
        {**first, "action_items": ["Phone the campsite"]}))
    filed = await pipeline.capture_text(text="campsite b4 fri", sender_mxid=MARGE,
                                        capture_id="$campsite", bucket="family/camping",
                                        seed_topics=["camping"])

    for at, words in (("09:00", "call them"), ("10:00", "phone them")):
        await pipeline.reprocess(vault_path=filed.vault_path, user_hint=words, sender_mxid=MARGE,
                                 capture_id="$campsite", reply_text=words, reply_id=f"$reply-{at}",
                                 reply_at=f"2026-11-22T{at}:00+00:00")

    camping = memory.text(list_path("camping", bucket="family"))
    assert [line for line in camping.splitlines() if line.startswith("- [ ]")] == [
        f"- [ ] Call the campsite [Campsite](../{filed.vault_path.removeprefix('family/')})"]
    assert parse(memory.text(filed.vault_path))["tags"][0] == "camping"
