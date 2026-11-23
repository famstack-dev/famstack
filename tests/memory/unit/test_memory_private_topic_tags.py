"""A document or an email is tagged only with the topics its place may show: a private topic stays with its person.

Marge keeps a personal topic, "Homers 40th" (`marge/surprise-party/`), a
surprise for Homer. A letter goes to the family's bucket, which everyone
reads; an email goes to the bucket of its mailbox. A card there that
carried `surprise-party` would show Marge's topic to Homer wherever the
topics of every bucket are read: on the card's page, in search, in the
document's chat event.

The tests file the way the archivist files: its document pipeline
(`DocumentPipeline.process`) and its email path (`GitMirror.publish_email_message`)
with the real mirror, through the curator of memory's test instance.
Paperless and the model are stand-ins at the boundary. The household is
the Simpsons (`tests.household`).
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

from stack.frontmatter import parse
from stack.memory import MemoryAPI
from stacklets.docs.bot.document_pipeline import DocumentPipeline
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.vault_context import VaultContext
from tests.household import HOUSEHOLD_FILES
from tests.memory.unit.instance import running_memory

PRIVATE = "surprise-party"          # Marge's topic, titled "Homers 40th"
LETTER_TEXT = "Springfield Bowling League. Lane booking for Saturday."


class Paperless:
    """Paperless as the document pipeline talks to it: one letter, its tags kept."""

    def __init__(self):
        self.tags: dict[str, int] = {}

    async def upload(self, filename, data, content_type=None):
        return f"task-{filename}"

    async def wait_task(self, task_id):
        return 45

    async def get_doc(self, doc_id):
        return {"id": doc_id, "title": f"scan-{doc_id}", "content": LETTER_TEXT}

    async def get_tags(self):
        return dict(self.tags)

    async def get_doc_types(self):
        return {}

    async def get_correspondents(self):
        return {}

    async def create_tag(self, name, color=None):
        self.tags[name] = len(self.tags) + 1
        return self.tags[name]

    async def create_doc_type(self, name):
        return 1

    async def create_correspondent(self, name):
        return 1

    async def update_doc(self, doc_id, updates):
        return True

    async def list_notes(self, doc_id):
        return []

    async def add_note(self, doc_id, text):
        return None


class Model:
    """The model: it answers each letter with `topics`, and keeps what it was shown."""

    def __init__(self, topics: list[str]):
        self.topics, self.shown = topics, []

    async def has_vision(self):
        return False

    async def classify(self, *, ocr_text, user_hint=None, **context):
        self.shown.append(context.get("ontology_section") or "")
        return {"title": "Bowling lane booking", "date": date.today().isoformat(),
                "correspondent": "Springfield Bowling League", "document_type": "Booking",
                "persons": ["homer"], "topics": list(self.topics),
                "summary": "Lane booked for Saturday."}


@pytest.fixture
def memory(tmp_path, monkeypatch):
    """The Simpsons' memory with Marge's private topic, as the archivist reads it."""
    with running_memory(tmp_path / "data", HOUSEHOLD_FILES) as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        yield memory


def mirror_of(memory, data: Path) -> GitMirror:
    """The archivist's mirror, writing through the curator's API."""
    return GitMirror(data_dir=data, vault=VaultContext(language="en", shared_bucket="family"),
                     memory=MemoryAPI.from_container(memory.environ))


def files_a_letter(memory, tmp_path, model: Model, paperless: Paperless | None = None) -> dict:
    """Homer sends a letter to the documents room; the parsed head of its card."""
    mirror = mirror_of(memory, tmp_path / "archivist")
    pipeline = DocumentPipeline(
        paperless=paperless or Paperless(), classifier=model, mirror=mirror, bot_name="archivist-bot",
        language="en", classify_enabled=True, reformat_enabled=False, classify_max_chars=10_000,
        vision_max_pdf_pages=5, reformat_max_pdf_pages=5,
        paperless_public_url="http://docs.home.test", link_base_url="http://home.test/go",
        actor="@archivist-bot:simpson", vault=mirror.vault)
    asyncio.run(pipeline.process(filename="booking.txt", display_name="booking.txt",
                                 file_data=b"scan", submitter_mxid="@homer:simpson"))
    [path] = [p for p in memory.pages("family/documents") if p.endswith("-p45.md")]
    return {"path": path, **parse(memory.page(path))}


def files_an_email(memory, tmp_path, *, into: str, tags: list[str]) -> dict:
    """An email filed into the bucket `into` with the model's `tags`; the parsed head of its card."""
    mirror = mirror_of(memory, tmp_path / "archivist")
    filed = asyncio.run(mirror.publish_email_message(
        entity=into, message_id="lanes-1@bowling.test", thread_uri="mid:lanes-1@bowling.test",
        from_addr="lanes@bowling.test", captured_at="2026-11-20",
        body_text="Your lane is booked for Saturday.", title_hint="Lane booking",
        classification={"title": "Lane booking", "persons": ["homer"], "summary": "Lane booked."},
        model=None, tags=tags))
    return {"path": filed.path, **parse(memory.page(filed.path))}


# ── Documents ────────────────────────────────────────────────────────────

def test_a_letter_in_the_familys_bucket_never_carries_marges_private_topic(memory, tmp_path):
    """Given Marge's private topic "Homers 40th", when the model answers its
    title for a letter Homer sends, then the letter's card in the family's
    bucket carries the plain keyword `homers-40th`, never her topic's id."""
    card = files_a_letter(memory, tmp_path, Model(["Homers 40th", "Camper Van"]))

    assert card["path"].startswith("family/")
    assert PRIVATE not in card["tags"]
    assert card["tags"] == ["homers-40th", "van"]


def test_a_letter_the_model_tags_with_marges_topic_id_shows_nothing_of_it(memory, tmp_path):
    """Given Marge's private topic `surprise-party`, when the model answers
    that id for Homer's letter, then neither the id nor her topic's title
    reaches the card: the family's bucket does not know the topic, so the
    id is no word to file."""
    card = files_a_letter(memory, tmp_path, Model([PRIVATE, "Camper Van"]))

    assert card["tags"] == ["van"]


def test_a_letter_is_classified_without_a_word_of_marges_private_topic(memory, tmp_path):
    """Given Marge's private topic, when Homer sends a letter, then the
    topics the model is shown for it hold no word of hers: the letter goes
    to the family's bucket, which everyone reads."""
    model = Model(["Camper Van"])

    files_a_letter(memory, tmp_path, model)

    assert model.shown and all("40th" not in shown and PRIVATE not in shown for shown in model.shown)


def test_a_letter_gives_paperless_no_tag_of_marges_private_topic(memory, tmp_path):
    """Given Marge's private topic "Homers 40th", when the model answers
    "homers 40th" for Homer's letter, then Paperless, which the whole
    family reads, gets the model's word as it is: neither her topic's title
    nor its id, because the family's bucket does not know the topic."""
    paperless = Paperless()

    files_a_letter(memory, tmp_path, Model(["homers 40th", "Camper Van"]), paperless)

    assert "homers 40th" in paperless.tags
    assert "Homers 40th" not in paperless.tags and PRIVATE not in paperless.tags


# ── Email ────────────────────────────────────────────────────────────────

def test_an_email_in_homers_bucket_never_carries_marges_private_topic(memory, tmp_path):
    """Given Marge's private topic, when an email is filed into Homer's
    bucket with the model's tags "Homers 40th" and her topic's id, then the
    card keeps the keyword and drops the id: Homer's bucket is not hers."""
    card = files_an_email(memory, tmp_path, into="homer", tags=["Homers 40th", PRIVATE, "Camper Van"])

    assert card["path"].startswith("homer/")
    assert card["tags"] == ["homers-40th", "van"]


def test_an_email_in_marges_own_bucket_carries_her_topic(memory, tmp_path):
    """Given Marge's private topic, when an email is filed into her own
    bucket and the model answers its title, then her card has her topic's id."""
    card = files_an_email(memory, tmp_path, into="marge", tags=["Homers 40th"])

    assert card["path"].startswith("marge/")
    assert card["tags"] == [PRIVATE]
