"""Creating a card and adding a source through the cards API, from the client's side.

A writer hands the memory a card as data: its type, its fields, its text
and its first source, and later a source more or a new text. The memory
stacklet names the file, writes the card and its sidecar in the notes'
format, checks them against the type's schema and commits them as the
person the call is by (ADR-015, ADR-018). A writer renders no file and
builds no commit subject.

These tests are that client. They send the calls over HTTP to the
curator's API over its card store, which pushes to a bare repository as
Forgejo (`running_memory`, memory's test instance), and look at what
Forgejo then holds. A note created this way is compared with the note the
archivist files today for the same words (`publish_capture`), byte for
byte, because the archivist moves onto this call next. The household is
the Simpsons.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.memory_client import MemoryClient
from stacklets.docs.bot.vault_context import VaultContext
from tests.memory.unit.instance import TOKEN, running_memory, version_of
from tests.memory.unit.simpsons_cards import ONTOLOGY, PEOPLE

from stack.cards.sources import Source, interpretation_of, quotes_of, sidecar_path
from stack.frontmatter import parse

TOPIC = {"family/itchy-scratchy-land/topic.md": (
    "---\ntype: topic\ncard_id: topic-itchy-scratchy-land\ntitle: Itchy & Scratchy Land\n---\n")}
FILES = {**PEOPLE, **TOPIC, "ontology.toml": ONTOLOGY}

WORDS = "get duff 4 the bbq, bart eats 2 burgers"
CLEAN = "Get Duff for the barbecue. Bart eats two burgers."
TITLE = "Duff for the barbecue"
SENT = Source("original", "homer", "2026-10-13T08:00:00+00:00", "chat", "text", "$duff",
              quote=WORDS)
MADE = {"at": "2026-10-13T08:00:05+00:00", "model": "m", "code_commit": "9a5bdc9",
        "prompt_template": "3f9a1c2b7d4e", "requested_by": "homer"}
ARCHIVIST = "archivist-bot"


@pytest.fixture
def memory(tmp_path, monkeypatch):
    """The Simpsons' memory: their people with accounts, one topic."""
    with running_memory(tmp_path / "data", FILES, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        yield memory


# ── The client ───────────────────────────────────────────────────────────

def _send(base: str, method: str, path: str, body: object = None, *, actor: str = "homer",
          token: str = TOKEN, headers: dict | None = None) -> tuple[int, dict, dict]:
    sent = {"Content-Type": "application/json", "Famstack-Actor": actor, **(headers or {})}
    if token:
        sent["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{base}{path}", method=method, headers=sent,
                                     data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(request) as r:
            status, answered, raw = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, answered, raw = e.code, dict(e.headers), e.read()
    return status, answered, json.loads(raw) if raw else {}


def a_note(*, belongs_to: str = "person-homer", source: Source = SENT, **overrides) -> dict:
    """Homer's note about the barbecue, as a create call carries it."""
    return {"type": "note", "in": belongs_to, "via": ARCHIVIST,
            "fields": {"title": TITLE, "date": "2026-10-13", "persons": ["bart"],
                       "tags": ["barbecue"]},
            "text": CLEAN, "source": source.shown(), "made": MADE, **overrides}


def create(memory, card: dict, **kwargs):
    return _send(memory.curator, "POST", "/cards", card, **kwargs)


async def filed_by_the_archivist(memory, tmp_path, entity: str):
    """The same note, filed as the archivist's capture pipeline files it today."""
    archivist = GitMirror(data_dir=tmp_path / "archivist",
                          vault=VaultContext(language="en", shared_bucket="family"),
                          memory=MemoryClient.from_environ(memory.environ))
    return await archivist.publish_capture(
        entity=entity, kind="note", source_uri=None, title_hint=None, body_text=WORDS,
        classification={"title": TITLE, "summary": "", "facts": [], "action_items": [],
                        "persons": ["Bart"]},
        captured_at="2026-10-13", model="m", tags=["barbecue"], capture_id="$duff",
        submitter="@homer:simpson", clean=CLEAN, sources=[SENT], made=MADE)


def what_landed(memory, path: str, card_id: str) -> tuple[str, str, str, str]:
    """The card, its sidecar, the commit line and message, with the random card id taken out."""
    def plain(text: str) -> str:
        return text.replace(card_id, "<card-id>")
    return (plain(memory.text(path)), plain(memory.text(sidecar_path(path))),
            memory.forgejo.new_commits()[0], memory.forgejo.message())


# ── Creating a note ──────────────────────────────────────────────────────

@pytest.mark.parametrize("belongs_to, entity", [("person-homer", "homer"),
                                                ("topic-itchy-scratchy-land", "family/itchy-scratchy-land")])
async def test_a_created_note_is_what_the_archivist_files_today(tmp_path, monkeypatch, belongs_to,
                                                               entity):
    """Given the memory without the note, when Homer creates it through the
    cards API, then the memory holds its card and sidecar in one commit by
    Homer, byte for byte what the archivist files for the same note, with
    the same commit. The answer names the card's id and where it lies."""
    with running_memory(tmp_path / "api", FILES, seeded_by="memory-bot") as memory:
        status, headers, answer = create(memory, a_note(belongs_to=belongs_to))
        assert status == 201
        assert headers["Location"] == f"/cards/{answer['card_id']}"
        assert memory.written == [sorted([answer["path"], sidecar_path(answer["path"])])]
        created = what_landed(memory, answer["path"], answer["card_id"])

    with running_memory(tmp_path / "archivist", FILES, seeded_by="memory-bot") as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        filed = await filed_by_the_archivist(memory, tmp_path, entity)
        assert filed.path == answer["path"]
        assert created == what_landed(memory, filed.path, filed.card_id)


async def test_a_note_filed_already_is_refused_with_its_id_and_nothing_is_written(memory, tmp_path):
    """The archivist's identity rule: the same words in the same place are the same note."""
    filed = await filed_by_the_archivist(memory, tmp_path, "homer")
    before = memory.forgejo.head()

    status, _, answer = create(memory, a_note())

    assert (status, answer["card_id"]) == (409, filed.card_id)
    assert memory.forgejo.head() == before


def test_the_same_link_is_the_same_note(memory):
    link = {"resource": "https://springfield.example/duff-recall"}
    first = create(memory, a_note(**link))[2]
    other = Source("original", "homer", "2026-10-14T08:00:00+00:00", "chat", "text", "$again",
                   quote="the duff recall again")

    status, _, answer = create(memory, a_note(source=other, **link))

    assert (status, answer["card_id"]) == (409, first["card_id"])


def test_a_type_other_than_note_is_not_supported_yet(memory):
    before = memory.forgejo.head()

    status, headers, answer = create(memory, a_note(type="bookmark"))

    assert status == 422 and headers["Content-Type"] == "application/problem+json"
    assert [e["field"] for e in answer["errors"]] == ["type"]
    assert memory.forgejo.head() == before


def test_a_note_that_fails_its_schema_is_refused(memory):
    before = memory.forgejo.head()
    note = a_note()
    note["fields"]["persons"] = ["grampa"]

    status, _, answer = create(memory, note)

    assert status == 422
    assert [e["field"] for e in answer["errors"]] == ["persons"]
    assert memory.forgejo.head() == before


def test_a_create_without_the_write_token_is_refused(memory):
    assert create(memory, a_note(), token="")[0] == 401


def test_a_create_names_no_file(memory):
    status, _, answer = create(memory, a_note(path="homer/notes/mine.md"))

    assert status == 400
    assert memory.text("homer/notes/mine.md") == ""


# ── Adding a source ──────────────────────────────────────────────────────

REPLY = Source("reply", "marge", "2026-10-13T09:00:00+00:00", "chat", "text", "$marge",
               quote="and lisa wants a veggie burger")


def test_a_source_added_by_marge_is_the_last_and_changes_nothing_else(memory):
    """Given a note, when Marge adds a source, then it is the last one under
    `## Sources` and in the sidecar, the fields and the text are as they
    were, and the commit is Marge's."""
    note = create(memory, a_note())[2]
    card_before = memory.text(note["path"])

    status, _, _ = _send(memory.curator, "POST", f"/cards/{note['card_id']}/sources",
                         {"source": REPLY.shown()}, actor="marge")

    card = memory.text(note["path"])
    sidecar = json.loads(memory.text(sidecar_path(note["path"])))
    assert status == 200
    assert quotes_of(card) == [WORDS, REPLY.quote]
    assert sidecar["sources"] == [SENT.record(), REPLY.record()]
    assert (parse(card), interpretation_of(card)) == (parse(card_before), interpretation_of(card_before))
    assert card == card_before.removesuffix("\n") + f"\n\n> {REPLY.quote}\n"
    assert memory.forgejo.new_commits()[-1] == \
        f"marge <marge@simpson> | memory-bot <memory-bot@local> | reply: {TITLE}"


def test_a_source_for_a_card_that_does_not_exist_is_not_found(memory):
    status, _, _ = _send(memory.curator, "POST", "/cards/note-0000000000/sources",
                         {"source": REPLY.shown()}, actor="marge")

    assert status == 404


# ── Updating the text ────────────────────────────────────────────────────

def test_an_updated_text_replaces_the_text_and_keeps_the_sources(memory):
    note = create(memory, a_note())[2]
    card_before = memory.text(note["path"])
    sidecar_before = memory.text(sidecar_path(note["path"]))
    new_text = "Get Duff and veggie burgers for the barbecue."

    status, _, _ = _send(memory.curator, "PATCH", f"/cards/{note['card_id']}", {"text": new_text},
                         actor="marge", headers={"Content-Type": "application/merge-patch+json",
                                                 "If-Match": f'"{version_of(card_before)}"'})

    card = memory.text(note["path"])
    assert status == 200
    assert interpretation_of(card) == new_text
    assert card == card_before.replace(CLEAN, new_text)
    assert memory.text(sidecar_path(note["path"])) == sidecar_before
