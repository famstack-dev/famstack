"""The card store checks every card it writes, whoever writes it (ADR-019 §4).

The curator's card store is every writer's way into the memory: the
archivist's filings, the diary, the host commands, the card editor, the
migrations. So it is where a card is checked, once, for all of them
(`card_check.CardCheck`). These tests write through a store with the
curator's check, against a real clone that pushes to a bare repository as
Forgejo, and read what Forgejo then holds. The household is the Simpsons:
Homer and Bart are declared, the ontology is the shipped one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

from stacklets.memory.card_check import CardCheck  # noqa: E402
from stacklets.memory.cards import CardInvalid, CardStore, Put  # noqa: E402
from tests.memory.unit.instance import Forgejo, TOKEN, clone_of, curator_serving  # noqa: E402

from stack.http_json import JsonClient  # noqa: E402

PEOPLE = {f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
          for pid, name in (("homer", "Homer Simpson"), ("bart", "Bart Simpson"))}
NOTE = "family/notes/2026/10/tent-3f2a1b.md"
SIDECAR = "family/notes/2026/10/tent-3f2a1b.json"
TENT = "---\ntype: note\ncard_id: note-3f2a1b9c0d\ntitle: Tent\npersons:\n  - homer\n---\n\n# Tent\n"


@pytest.fixture
def forgejo(tmp_path) -> Forgejo:
    forgejo = Forgejo(tmp_path)
    forgejo.seed("memory-bot", "seed: the Simpsons", {**PEOPLE, NOTE: TENT})
    return forgejo


@pytest.fixture
def store(forgejo, tmp_path) -> CardStore:
    """The curator's card store, with the check the curator gives it."""
    (tmp_path / "curator").mkdir()
    return CardStore(clone_of(forgejo, tmp_path / "curator" / "vault"), remote="origin",
                     server_name="simpson", check=CardCheck())


def _put(store, path, text, *, base=None, **commit):
    version = store.read(path).version if base is None and store.read(path) else (base or "")
    return store.commit([Put(path, text, base=version)],
                        **{"by": "marge", "kind": "edit", "title": "Tent", **commit})


def test_a_card_that_misses_its_types_schema_is_refused_naming_the_field(store, forgejo):
    """Given a note naming Mr. Burns, who is no declared person, when it is
    written, then nothing is written and the refusal names the file and the
    field: the card would break every reader that resolves its people."""
    with pytest.raises(CardInvalid) as refused:
        _put(store, NOTE, TENT.replace("  - homer", "  - mr-burns"))

    assert [(e["path"], e["field"]) for e in refused.value.errors] == [(NOTE, "persons")]
    assert "mr-burns" in refused.value.errors[0]["message"]
    assert forgejo.new_commits() == []


def test_a_card_of_no_card_type_is_refused(store):
    with pytest.raises(CardInvalid) as refused:
        _put(store, NOTE, TENT.replace("type: note", "type: recipe"))

    assert [e["field"] for e in refused.value.errors] == ["type"]


def test_a_page_written_by_hand_goes_in(store, forgejo):
    """A page without a `type` is somebody's page; the curator makes it a
    note and gives it its id afterwards."""
    _put(store, "homer/donut-shops.md", "# Donut shops\n\n- Lard Lad\n", base="")

    assert forgejo.file("homer/donut-shops.md") == "# Donut shops\n\n- Lard Lad\n"


def test_a_change_never_makes_one_card_another(store, forgejo):
    """Given the tent note, when a write over it carries another card's id,
    then it is refused: a refile changes what a card says, never which card
    it is, and links and the chat name the card by its id."""
    with pytest.raises(CardInvalid) as refused:
        _put(store, NOTE, TENT.replace("note-3f2a1b9c0d", "note-0b6e2d91aa"))

    assert [(e["path"], e["field"]) for e in refused.value.errors] == [(NOTE, "card_id")]
    assert forgejo.file(NOTE) == TENT


def test_a_copy_of_a_card_may_get_an_id_of_its_own(store, forgejo):
    """Given a copy of the tent note that carries the note's id, when a write
    gives the copy a new id, then it goes in: the id stays with the tent
    note, and the copy becomes a card of its own (ADR-016 §2)."""
    copy = "family/notes/2026/10/tent-copy.md"
    _put(store, copy, TENT, base="")

    _put(store, copy, TENT.replace("note-3f2a1b9c0d", "note-0b6e2d91aa"))

    assert forgejo.file(copy) == TENT.replace("note-3f2a1b9c0d", "note-0b6e2d91aa")
    assert forgejo.file(NOTE) == TENT


def test_a_card_of_a_new_type_keeps_its_old_id_among_its_former_ones(store, forgejo):
    retyped = TENT.replace("card_id: note-3f2a1b9c0d",
                           "card_id: bookmark-7f3a9c21e4\nformer_card_ids:\n  - note-3f2a1b9c0d") \
        .replace("type: note", "type: bookmark") \
        .replace("title: Tent", "title: Tent\nresource: https://camping.example/tent\n"
                                "timestamp: \"2026-10-13T08:00:00.000Z\"")

    _put(store, NOTE, retyped)

    assert forgejo.file(NOTE) == retyped


def test_a_sidecar_carries_its_notes_card_id(store, forgejo):
    with pytest.raises(CardInvalid) as refused:
        _put(store, SIDECAR, '{"card_id": "note-0b6e2d91aa", "sources": []}\n', base="")

    assert [(e["path"], e["field"]) for e in refused.value.errors] == [(SIDECAR, "card_id")]
    assert forgejo.file(SIDECAR) is None


def test_a_migrations_card_between_formats_goes_in_but_keeps_its_identity(store, forgejo):
    """A migration brings old cards into the format in steps: its card may
    be between formats until the chain has run (`test_memory_migration_chain`
    checks the end). Which card it is is checked all the same."""
    between = TENT.replace("title: Tent\n", "title: Tent\nkind: correspondent\n")

    _put(store, NOTE, between, by="memory-bot", kind="cards", title="types",
         trailers={"Migration": "memory/0004"})
    assert forgejo.file(NOTE) == between

    with pytest.raises(CardInvalid):
        _put(store, NOTE, between.replace("note-3f2a1b9c0d", "note-0b6e2d91aa"),
             by="memory-bot", kind="cards", title="types", trailers={"Migration": "memory/0004"})


def test_with_an_ontology_that_does_not_parse_a_card_is_refused_naming_it(tmp_path):
    """Given an `ontology.toml` a person broke by hand in Forgejo, when a
    card is written, then it is refused, naming the file and what is wrong
    with it, and nothing is written. The topics a card may name come from
    that file; the person who can fix it reads where to look, and the
    curator goes on running."""
    forgejo = Forgejo(tmp_path)
    forgejo.seed("homer", "seed: the Simpsons", {**PEOPLE, "ontology.toml": "[topic.camping\n"})
    (tmp_path / "curator").mkdir()
    store = CardStore(clone_of(forgejo, tmp_path / "curator" / "vault"), remote="origin",
                      server_name="simpson", check=CardCheck())

    with pytest.raises(CardInvalid) as refused:
        _put(store, NOTE, TENT, base="")

    assert [(e["path"], e["field"]) for e in refused.value.errors] == [("ontology.toml", "ontology")]
    assert "Forgejo" in refused.value.errors[0]["message"]
    assert forgejo.new_commits() == []


def test_an_ontology_that_does_not_parse_is_not_written(store, forgejo):
    with pytest.raises(CardInvalid) as refused:
        _put(store, "ontology.toml", "[topic.camping\n", base="")

    assert [(e["path"], e["field"]) for e in refused.value.errors] == [("ontology.toml", "ontology")]
    assert forgejo.new_commits() == []


def test_through_the_curators_api_a_refusal_names_each_field(store):
    """The archivist, the diary and the host commands see the refusal as the
    curator's answer: 422, `invalid`, and the fields, so a person can be told
    what to change."""
    note = store.read(NOTE)
    with curator_serving(store, TOKEN) as curator:
        answer = JsonClient(curator, token=TOKEN).post("/commits", {
            "kind": "edit", "title": "Tent", "by": "marge",
            "changes": [{"path": NOTE, "text": TENT.replace("  - homer", "  - mr-burns"),
                         "base": note.version}]})

    assert (answer.status, answer.body["reason"]) == (422, "invalid")
    assert [(e["path"], e["field"]) for e in answer.body["errors"]] == [(NOTE, "persons")]
