"""A correction by reply drops no tag and no person it does not name.

Homer's receipt is filed, and a reply in its thread says: the title shall be
"Tankquittung Camper van März", and the tag Einkauf is wrong, it belongs to
Reise. The card then has the model's title, Reise in place of Einkauf, and
still Homer and the tag Fahrzeug, which the model dropped and nobody named.
A note behaves the same.

The tests start where the family does: Marge's reply in her room, given to the archivist as the
bot runner loads it (`ChatRoom`, `tests/archivist_chat.py`). The receipt is
filed through the archivist's real document pipeline and mirror, the note
through its capture pipeline, both into the real curator of memory's test
instance. Paperless and the model stand in at the boundary. The model
stand-in answers the correction as the 16 GB model did on the rig: another
title, in English, Homer dropped, and Fahrzeug removed with Einkauf.
"""

from __future__ import annotations

import pytest

from stack.frontmatter import parse
from stacklets.docs.bot.document_pipeline import DocumentPipeline
from tests.archivist_chat import MARGE, ChatRoom, ModelAnswers
from tests.memory.unit.instance import running_memory

PEOPLE = {f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
          for pid, name in (("homer", "Homer Simpson"), ("marge", "Marge Simpson"),
                            ("bart", "Bart Simpson"), ("lisa", "Lisa Simpson"))}

RECEIPT = ("Texxon Tankstelle Springfield\n12.11.2026 14:32\nSuper E10 32,4 l x 1,378 EUR\n"
           "Summe 44,64 EUR\nAutowäsche Premium 8,00 EUR\nGesamt 52,64 EUR")
FILED = {"title": "Texxon Tankquittung", "date": "2026-11-12", "correspondent": "Texxon",
         "document_type": "Receipt", "topics": ["Fahrzeug", "Einkauf"], "persons": ["Homer"],
         "summary": "Fuel and a car wash at Texxon."}
CORRECTION = ('Der Titel soll "Tankquittung Camper van März" heißen, und das Tag Einkauf ist falsch, '
              'das gehört zu Reise.')

# What the 16 GB model answered on the rig, as a whole new filing and as changes.
DRIFTED = {**FILED, "title": "Texxon Fuel Camper van March", "topics": ["Reise"], "persons": []}
DRIFTED_CHANGES = {"title": "Texxon Fuel Camper van March",
                   "tags": {"add": ["Reise"], "remove": ["Einkauf", "Fahrzeug"]},
                   "persons": {"remove": ["Homer"]}}


class Paperless:
    """Paperless as the document pipeline talks to it: one receipt, its tags and its fields."""

    def __init__(self):
        self.tags = {"Fahrzeug": 1, "Einkauf": 2, "Reise": 3, "Person: Homer": 4}
        self.fields: dict = {}

    async def upload(self, filename, data, content_type=None):
        return "task-1"

    async def wait_task(self, task_id):
        return 47

    async def get_doc(self, doc_id):
        return {"id": doc_id, "title": self.fields.get("title", "scan-47"), "content": RECEIPT}

    async def get_tags(self):
        return dict(self.tags)

    async def get_doc_types(self):
        return {"Receipt": 1}

    async def get_correspondents(self):
        return {"Texxon": 1}

    async def create_tag(self, name, color=None):
        self.tags[name] = len(self.tags) + 1
        return self.tags[name]

    async def create_doc_type(self, name):
        return 2

    async def create_correspondent(self, name):
        return 2

    async def update_doc(self, doc_id, updates):
        self.fields.update(updates)
        return True

    async def list_notes(self, doc_id):
        return []

    async def add_note(self, doc_id, text):
        return None

    def tag_names(self) -> set[str]:
        return {name for name, tid in self.tags.items() if tid in self.fields.get("tags", [])}


class DocumentModel:
    """The model behind the document pipeline: the receipt's filing, and a
    correction answered as the 16 GB model did."""

    def __init__(self):
        self.calls = 0
        self.correction = DRIFTED_CHANGES

    async def has_vision(self):
        return False

    async def classify(self, *, user_hint=None, **_):
        return dict(DRIFTED if user_hint else FILED)

    async def correct_card(self, messages, *, text, todos=False):
        self.calls += 1
        return dict(self.correction)

    async def reformat(self, text, **_):
        return text


@pytest.fixture
def memory(tmp_path, monkeypatch):
    with running_memory(tmp_path / "data", PEOPLE, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("MEDIA_ARCHIVE_DIR", str(memory.media))
        monkeypatch.setenv("LANGUAGE", "de")
        yield memory


@pytest.fixture
def paperless():
    return Paperless()


@pytest.fixture
def model():
    return DocumentModel()


@pytest.fixture
async def chat(tmp_path, memory, httpserver, paperless, model):
    def documents(mirror, vault):
        return DocumentPipeline(
            paperless=paperless, classifier=model, mirror=mirror, bot_name="archivist-bot",
            language="de", classify_enabled=True, reformat_enabled=False, classify_max_chars=10_000,
            vision_max_pdf_pages=5, reformat_max_pdf_pages=5,
            paperless_public_url="http://docs.home.test", link_base_url="http://home.test/go",
            actor="@archivist-bot:simpson", vault=vault)

    room = ChatRoom(tmp_path, memory, httpserver, ModelAnswers(), documents=documents)
    yield room
    await room.close()


def card_of(chat, memory, notice) -> dict:
    """The frontmatter of the card a notice links to, as Forgejo holds it."""
    return parse(memory.text(chat.envelope(notice)["data"]["vault_path"]))


async def test_a_receipts_correction_keeps_homer_and_fahrzeug_it_does_not_name(
        chat, memory, paperless):
    """Given a receipt filed with Homer and the tags Fahrzeug and
    Einkauf, when Marge replies in its thread with a quoted title and that
    Einkauf is wrong, it belongs to Reise, and the model answers another
    title and drops Homer and Fahrzeug, then the card and Paperless have
    the model's title, Fahrzeug and Reise, and still Homer."""
    notice = await chat.has_a_document(MARGE, "texxon.txt")

    await chat.says(MARGE, CORRECTION, event_id="$fix", thread=chat.thread_of(notice))

    corrected = chat.notices()[-1]
    data = chat.envelope(corrected)["data"]
    assert data["title"] == DRIFTED["title"]
    assert sorted(data["topics"]) == ["Fahrzeug", "Reise"]
    assert data["persons"] == ["Homer"]
    assert paperless.fields["title"] == DRIFTED["title"]
    assert paperless.tag_names() == {"Fahrzeug", "Reise", "Person: Homer"}


NOTE = ("Homer hat am Samstag bei Texxon für den Camper getankt, 44,64 Euro für das Benzin und "
        "8 Euro für die Autowäsche, zusammen 52,64 Euro. Die Quittung liegt im Handschuhfach.")
NOTE_FILED = {"title": "Tanken bei Texxon", "persons": ["Homer"], "tags": ["fahrzeug", "einkauf"]}


async def test_a_notes_correction_keeps_homer_and_fahrzeug_it_does_not_name(chat, memory):
    """Given a note filed with Homer and the tags fahrzeug and
    einkauf, when Marge replies in its thread with a quoted title and that
    einkauf is wrong, it belongs to reise, and the model answers another
    title and drops Homer and fahrzeug, then the card has the model's
    title, the topics Fahrzeug and Reise, and still Homer."""
    chat.model.answers[:] = [NOTE_FILED, {**NOTE_FILED, "title": "Texxon Fuel Camper van March",
                                          "persons": [], "tags": ["reise"]}]
    chat.model.corrections.append({**DRIFTED_CHANGES, "clean": NOTE,
                                   "tags": {"add": ["reise"], "remove": ["einkauf", "fahrzeug"]}})
    await chat.says(MARGE, NOTE, event_id="$tanken")
    [notice] = chat.notices()

    await chat.says(MARGE, CORRECTION.replace("Einkauf", "einkauf").replace("Reise", "reise"),
                    event_id="$fix", thread="$tanken")

    head = card_of(chat, memory, chat.notices()[-1])
    assert head["title"] == DRIFTED["title"]
    assert sorted(head["tags"]) == ["travel", "vehicle"], "Reise and Fahrzeug, by their topic ids"
    assert head["persons"] == ["homer"]
    assert notice


async def test_a_receipts_correction_the_model_answers_unreadably_changes_nothing(
        chat, memory, paperless, model):
    """Given the filed receipt, when the model's answer to Marge's
    correction is no object, then Paperless keeps the filing's title and
    tags, the card gets no commit, the reply is no reclassification, and
    it says why."""
    notice = await chat.has_a_document(MARGE, "texxon.txt")
    filed_fields = dict(paperless.fields)
    commits = memory.new_commits()
    model.correction = {}

    await chat.says(MARGE, CORRECTION, event_id="$fix", thread=chat.thread_of(notice))

    assert paperless.fields == filed_fields
    assert memory.new_commits() == commits, "the card is not rewritten"
    answer = chat.notices()[-1]
    assert chat.envelope(answer) == {}
    assert answer.body != notice.body


async def test_a_receipts_correction_adds_no_person_the_family_does_not_know(
        chat, memory, paperless, model):
    """Given the filed receipt, when Marge says Lenny is on it too and the
    model adds Lenny, then the card keeps Homer alone and the reply names
    Lenny, whom the family does not know."""
    notice = await chat.has_a_document(MARGE, "texxon.txt")
    model.correction = {"persons": {"add": ["Lenny"]}}

    await chat.says(MARGE, "Lenny war auch dabei.", event_id="$fix", thread=chat.thread_of(notice))

    corrected = chat.notices()[-1]
    assert chat.envelope(corrected)["data"]["persons"] == ["Homer"]
    assert "Lenny" in corrected.body
    assert paperless.tag_names() == {"Fahrzeug", "Einkauf", "Person: Homer"}


async def test_a_notes_correction_the_model_answers_unreadably_leaves_the_card(chat, memory):
    """Given the filed note, when the model's answer to Marge's correction
    is no object, then the card keeps its title, tags and people."""
    chat.model.answers[:] = [NOTE_FILED]
    chat.model.corrections.append({})
    await chat.says(MARGE, NOTE, event_id="$tanken")
    filed = card_of(chat, memory, chat.notices()[-1])

    await chat.says(MARGE, CORRECTION, event_id="$fix", thread="$tanken")

    head = parse(memory.text(chat.envelope(chat.notices()[0])["data"]["vault_path"]))
    assert {k: head[k] for k in ("title", "tags", "persons")} == \
        {k: filed[k] for k in ("title", "tags", "persons")}
    assert chat.envelope(chat.notices()[-1]) == {}


async def test_a_notes_correction_adds_no_person_the_family_does_not_know(chat, memory):
    """Given the filed note, when Marge says Lenny was there too and the
    model adds Lenny, then the card keeps Homer alone and the reply names
    Lenny."""
    chat.model.answers[:] = [NOTE_FILED]
    chat.model.corrections.append({"persons": {"add": ["Lenny"]}})
    await chat.says(MARGE, NOTE, event_id="$tanken")

    await chat.says(MARGE, "Lenny war auch dabei.", event_id="$fix", thread="$tanken")

    corrected = chat.notices()[-1]
    assert card_of(chat, memory, corrected)["persons"] == ["homer"]
    assert "Lenny" in corrected.body


async def test_a_receipts_card_changed_while_the_model_answers_keeps_that_change(
        chat, memory, model):
    """Given the filed receipt, when another correction changes its card
    while the model answers Marge's, then her write is refused as on a
    changed card: the card keeps the later change, not hers on the old
    version, because hers was read before the other one landed."""
    notice = await chat.has_a_document(MARGE, "texxon.txt")
    [card] = memory.vault.rglob("*-p47.md")
    path = str(card.relative_to(memory.vault))
    later = memory.text(path).replace("title: Texxon Tankquittung", "title: Tankbeleg Homer")
    answer = model.correct_card

    async def a_change_lands_meanwhile(messages, **kwargs):
        memory.edited_in_forgejo({path: later}, by="homer", subject="a later correction")
        return await answer(messages, **kwargs)

    model.correct_card = a_change_lands_meanwhile
    await chat.says(MARGE, CORRECTION, event_id="$fix", thread=chat.thread_of(notice))

    assert parse(memory.text(path))["title"] == "Tankbeleg Homer"
