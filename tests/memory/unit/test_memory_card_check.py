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

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

from stacklets.memory.card_check import CardCheck  # noqa: E402
from stacklets.memory.source_check import changed_sources  # noqa: E402
from stacklets.memory.cards import CardInvalid, CardStore, Put, Remove  # noqa: E402
from tests.memory.unit.instance import (  # noqa: E402
    Forgejo, TOKEN, clone_of, commit_as, curator_serving)

from stack.http_json import JsonClient  # noqa: E402

PEOPLE = {f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
          for pid, name in (("homer", "Homer Simpson"), ("bart", "Bart Simpson"))}
NOTE = "family/notes/2026/10/tent-3f2a1b.md"
SIDECAR = "family/notes/2026/10/tent-3f2a1b.json"
TENT = "---\ntype: note\ncard_id: note-3f2a1b9c0d\ntitle: Tent\npersons:\n  - homer\n---\n\n# Tent\n"

# Homer's camping note with its sources (ADR-018): his message and Bart's
# reply, quoted under `## Sources`, each with its entry in the sidecar.
CAMPING = "family/notes/2026/10/camping-7903f2.md"
CAMPING_SIDECAR = "family/notes/2026/10/camping-7903f2.json"
SENT = "we leave fri at 4\nbring 3 sleeping bags"
REPLY = "i pack the flashlights"
HOMERS = {"role": "original", "person": "homer", "time": "2026-10-20T08:00:00+00:00",
          "channel": "chat", "kind": "text", "id": "$camping:simpson"}
BARTS = {"role": "reply", "person": "bart", "time": "2026-10-20T08:05:00+00:00",
         "channel": "chat", "kind": "text", "id": "$flashlights:simpson"}


def _camping(*quotes: str, card_id: str = "note-7903f2aa01") -> str:
    """The camping note as the archivist files it, with `quotes` under its sources."""
    sources = "\n\n".join("\n".join(f"> {line}" for line in q.split("\n")) for q in quotes)
    return (f"---\ntype: note\ncard_id: {card_id}\ntitle: Camping trip\npersons:\n  - homer\n"
            f"---\n\n# Camping trip\n\nWe leave on Friday at 4.\n\n## Sources\n\n{sources}\n")


def _sidecar(*entries: dict, card_id: str = "note-7903f2aa01") -> str:
    return json.dumps({"card_id": card_id, "filed_by": "homer", "sources": list(entries),
                       "made": {"at": "2026-10-20T08:00:05+00:00"}}, indent=2) + "\n"


@pytest.fixture
def forgejo(tmp_path) -> Forgejo:
    forgejo = Forgejo(tmp_path)
    forgejo.seed("memory-bot", "seed: the Simpsons", {
        **PEOPLE, NOTE: TENT, CAMPING: _camping(SENT, REPLY), CAMPING_SIDECAR: _sidecar(HOMERS, BARTS)})
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


# ── Sources are only appended (ADR-018 §4) ─────────────────────────────


def _writes(store, card: str | None = None, sidecar: str | None = None, **commit):
    """One write of the camping note and its sidecar, each made from what the clone holds."""
    changes = [Put(path, text, base=store.read(path).version)
               for path, text in ((CAMPING, card), (CAMPING_SIDECAR, sidecar)) if text is not None]
    return store.commit(changes, **{"by": "archivist-bot", "kind": "reprocess", "title": "Camping trip",
                                    **commit})


def _refused_fields(refused) -> list[tuple[str, str]]:
    return [(e["path"], e["field"]) for e in refused.value.errors]


def test_a_write_that_appends_a_source_goes_in(store, forgejo):
    """Given the camping note with Homer's message and Bart's reply, when
    Lisa's reply is filed after them, then it goes in: appending is what a
    source is for."""
    lisas = {**BARTS, "person": "lisa", "id": "$tofu:simpson"}

    _writes(store, _camping(SENT, REPLY, "and tofu dogs for me"), _sidecar(HOMERS, BARTS, lisas))

    assert forgejo.file(CAMPING) == _camping(SENT, REPLY, "and tofu dogs for me")


def test_a_write_that_changes_the_words_of_a_quote_is_refused(store, forgejo):
    """Given the camping note, when a write changes the words Homer sent,
    then nothing is written and the refusal names the source: a quote keeps
    what a person sent, and a writer with a bug must not rewrite it."""
    with pytest.raises(CardInvalid) as refused:
        _writes(store, _camping(SENT.replace("4", "5"), REPLY))

    assert _refused_fields(refused) == [(CAMPING, "sources[0]")]
    assert forgejo.new_commits() == []


def test_a_write_that_blanks_the_words_of_a_quote_is_refused(store, forgejo):
    """Given the camping note nobody edited by hand, when a write leaves
    Homer's quote empty, then it is refused: a writer that lost a source's
    text must not pass as one that put a quote back."""
    with pytest.raises(CardInvalid) as refused:
        _writes(store, _camping(SENT, REPLY).replace("> we leave fri at 4\n> bring 3 sleeping bags", ">"))

    assert _refused_fields(refused) == [(CAMPING, "sources[0]")]
    assert forgejo.new_commits() == []


def test_a_write_that_removes_a_source_is_refused(store, forgejo):
    with pytest.raises(CardInvalid) as refused:
        _writes(store, _camping(SENT), _sidecar(HOMERS))

    assert _refused_fields(refused) == [(CAMPING, "sources[1]")]
    assert forgejo.new_commits() == []


def test_a_write_that_reorders_the_sources_is_refused(store, forgejo):
    with pytest.raises(CardInvalid) as refused:
        _writes(store, _camping(REPLY, SENT), _sidecar(BARTS, HOMERS))

    assert _refused_fields(refused) == [(CAMPING, "sources[0]"), (CAMPING, "sources[1]")]
    assert forgejo.new_commits() == []


def test_a_write_that_changes_an_entry_of_the_sidecar_is_refused(store, forgejo):
    """Given Bart's reply, when a write says it was Lisa's, then it is refused."""
    with pytest.raises(CardInvalid) as refused:
        _writes(store, sidecar=_sidecar(HOMERS, {**BARTS, "person": "lisa"}))

    assert _refused_fields(refused) == [(CAMPING_SIDECAR, "sources[1]")]
    assert forgejo.new_commits() == []


def test_a_write_that_inserts_a_source_before_the_first_is_refused(store, forgejo):
    """Sources are only appended: a source put before Homer's moves every
    other one, so the bridge for old notes may not do it either."""
    lisas = {**BARTS, "role": "original", "person": "lisa", "id": "$earlier:simpson"}

    with pytest.raises(CardInvalid) as refused:
        _writes(store, _camping("earlier", SENT, REPLY), _sidecar(lisas, HOMERS, BARTS))

    assert (CAMPING, "sources[0]") in _refused_fields(refused)
    assert forgejo.new_commits() == []


def test_removing_the_sidecar_of_a_card_that_stays_is_refused(store, forgejo):
    with pytest.raises(CardInvalid) as refused:
        store.commit([Remove(CAMPING_SIDECAR, base=store.read(CAMPING_SIDECAR).version)],
                     by="marge", kind="edit", title="Camping trip")

    assert [f for _, f in _refused_fields(refused)] == ["sources[0]", "sources[1]"]
    assert forgejo.file(CAMPING_SIDECAR) is not None


def test_the_refusal_says_how_to_correct_a_source(store):
    """The refusal is the rule's own, the same for every writer: it names
    the two ways a person corrects what was sent."""
    with pytest.raises(CardInvalid) as refused:
        _writes(store, _camping(SENT.replace("4", "5"), REPLY))

    assert "Forgejo" in str(refused.value)
    assert "stack memory reprocess --msg" in str(refused.value)


def test_an_entry_written_again_without_its_empty_keys_goes_in(store, forgejo):
    """Given an entry stored with `"reconstructed": false` and `"text": {}`,
    when a rebuild writes it again without them, then it goes in: an entry
    is compared as a source, not as raw JSON."""
    padded = {**BARTS, "text": {}, "reconstructed": False}
    _writes(store, sidecar=_sidecar(HOMERS, padded))

    _writes(store, sidecar=_sidecar(HOMERS, BARTS))

    assert json.loads(forgejo.file(CAMPING_SIDECAR))["sources"][1] == BARTS


def test_a_reconstructed_source_may_be_replaced_by_one_of_the_same_role(store, forgejo):
    """Given a note whose reply an old card recorded badly (`reconstructed`),
    when the refile reads it again from the chat, then the reply is replaced
    in place and the write goes in (the bridge of ADR-018 §8). A source of
    another role in its place is refused."""
    guessed = {**BARTS, "time": "unknown", "id": "unknown", "reconstructed": True}
    _writes(store, _camping(SENT, "I pack the flashlights"), _sidecar(HOMERS, guessed),
            trailers={"Migration": "memory/0008"})

    with pytest.raises(CardInvalid):
        _writes(store, _camping(SENT, REPLY), _sidecar(HOMERS, {**BARTS, "role": "note"}))
    _writes(store, _camping(SENT, REPLY), _sidecar(HOMERS, BARTS))

    assert json.loads(forgejo.file(CAMPING_SIDECAR))["sources"][1] == BARTS


def test_a_quote_a_person_edited_may_be_put_back_to_the_words_as_sent(store, forgejo, tmp_path):
    """Given Marge edited Homer's quote in Forgejo, when a rebuild puts it
    back to the words he sent and appends her edit as her note, then it goes
    in (ADR-018 §4, D1a). Putting it back to anything else is refused."""
    edited = SENT.replace("3 sleeping", "4 sleeping")
    commit_as(clone_of(forgejo, tmp_path / "marge"), "marge", "Update camping",
              {CAMPING: _camping(edited, REPLY)})
    store.sync()
    marges = {"role": "note", "person": "marge", "time": "2026-10-21T09:00:00+00:00",
              "channel": "cards", "kind": "text", "id": "cards:0b6e2d91aa77c3e1"}

    with pytest.raises(CardInvalid):
        _writes(store, _camping(SENT.replace("4", "5"), REPLY, edited), _sidecar(HOMERS, BARTS, marges))
    _writes(store, _camping(SENT, REPLY, edited), _sidecar(HOMERS, BARTS, marges))

    assert forgejo.file(CAMPING) == _camping(SENT, REPLY, edited)


def test_a_migrations_commit_may_change_sources(store, forgejo):
    """A migration brings old cards into the format, its sources included."""
    _writes(store, _camping(SENT.lower().replace("fri", "friday"), REPLY),
            by="memory-bot", kind="cards", title="sources", trailers={"Migration": "memory/0009"})

    assert "we leave friday at 4" in forgejo.file(CAMPING)


def test_a_card_without_a_sidecar_is_checked_as_today(store, forgejo):
    """A card that keeps no sources yet (a document, a diary card) has no
    existing sources a write could change."""
    _put(store, NOTE, TENT + "\n## Sources\n\n> anything\n")

    assert forgejo.file(NOTE).endswith("> anything\n")


# ── A moved card keeps its sources ───────────────────────────────────────

MOVED = "family/camping/notes/2026/10/camping-7903f2.md"
MOVED_SIDECAR = "family/camping/notes/2026/10/camping-7903f2.json"


def _moves(store, card: str, sidecar: str | None):
    """The camping note moved under the camping topic: removed at its old
    path, put at the new one, as a refile into a topic writes it."""
    gone = [Remove(path, base=store.read(path).version) for path in (CAMPING, CAMPING_SIDECAR)]
    put = [Put(MOVED, card, base="")] + ([Put(MOVED_SIDECAR, sidecar, base="")] if sidecar else [])
    return store.commit(gone + put, by="archivist-bot", kind="reprocess", title="Camping trip")


def test_a_moved_card_with_its_sources_as_they_were_goes_in(store, forgejo):
    _moves(store, _camping(SENT, REPLY), _sidecar(HOMERS, BARTS))

    assert forgejo.file(MOVED) == _camping(SENT, REPLY)
    assert forgejo.file(CAMPING) is None


def test_a_moved_card_is_judged_against_the_card_it_was(store, forgejo):
    """Given the camping note, when a write moves it and changes Homer's
    words on the way, then it is refused at the new path: the card there is
    the same card, found by its id, so its sources are the ones it had."""
    with pytest.raises(CardInvalid) as refused:
        _moves(store, _camping(SENT.replace("4", "5"), REPLY), _sidecar(HOMERS, BARTS))

    assert _refused_fields(refused) == [(MOVED, "sources[0]")]
    assert forgejo.new_commits() == []


def test_a_moved_card_found_by_a_former_id_is_judged_against_it(store, forgejo):
    retyped = _camping(SENT.replace("4", "5"), REPLY, card_id="note-7903f2bb02").replace(
        "card_id: note-7903f2bb02", "card_id: note-7903f2bb02\nformer_card_ids:\n  - note-7903f2aa01")

    with pytest.raises(CardInvalid) as refused:
        _moves(store, retyped, _sidecar(HOMERS, BARTS, card_id="note-7903f2bb02"))

    assert (MOVED, "sources[0]") in _refused_fields(refused)


def test_a_moved_card_without_its_sidecar_is_refused(store, forgejo):
    with pytest.raises(CardInvalid) as refused:
        _moves(store, _camping(SENT, REPLY), None)

    assert [f for _, f in _refused_fields(refused)] == ["sources[0]", "sources[1]"]
    assert forgejo.new_commits() == []


def test_a_topic_rename_that_moves_many_cards_reads_each_removed_file_once():
    """Given 40 notes with sources, when one write moves them all under a
    renamed topic, then the rule reads each removed card and sidecar at
    most once. Why: the check runs under the store's lock, each read is a
    git process, and a rename of a topic of a hundred notes must answer
    within the client's 30 s; a search per moved card read them all again
    for each."""
    memory, puts, removes = {}, [], []
    for n in range(40):
        card_id = f"note-7903f2{n:04x}"
        old = f"family/notes/2026/10/camping-{n}.md"
        new = f"family/camping/notes/2026/10/camping-{n}.md"
        card, sidecar = _camping(SENT, REPLY, card_id=card_id), _sidecar(HOMERS, BARTS, card_id=card_id)
        memory.update({old: card, old.replace(".md", ".json"): sidecar})
        puts += [(new, card), (new.replace(".md", ".json"), sidecar)]
        removes += [old, old.replace(".md", ".json")]
    reads: dict[str, int] = {}

    def read(path):
        reads[path] = reads.get(path, 0) + 1
        return memory.get(path)

    assert changed_sources(puts, read, None, removes=removes) == []
    assert max(reads.get(path, 0) for path in removes) == 1
