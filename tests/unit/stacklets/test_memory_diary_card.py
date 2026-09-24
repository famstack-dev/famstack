"""Diary records in the vault: the card is the source, the pages are views.

A diary entry is a record, so it lives in the vault as one file, a card:
machine frontmatter, the summary and facts a model read out of it, the
family's words as they were recorded, and the files they sent. Every
warm surface (the diary pages, a "Do you remember" card, whatever comes
next) is compiled from cards and from nothing else, so a correction made
on a card is a correction everywhere.

That puts three promises on this module, and each has a test class:

- A card holds the whole entry. Reading one back gives the entry that
  was written, and the diary pages built from cards are the pages built
  from the entries themselves.
- A card lives at one path. Recompiling the same room writes the same
  files, and a generated title never moves one.
- A person's edit wins. The compiler rewrites only cards it wrote and
  nobody touched since; an edited card is left alone on every later run,
  and the pages show the edit.

Entries come from the same corpus as `test_memory_diary.py`
(`tools/family-memories/spec.en.yaml`), so the cards are made from what
the compiler really produces for a messy room.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))

import diary  # noqa: E402
import diary_card  # noqa: E402
from stack.frontmatter import parse as parse_frontmatter, validate  # noqa: E402
from test_memory_diary import _compile, _entry_for  # noqa: E402

ROOM = "!memories:test"
BUCKET = "family"


def _extraction(**kw) -> diary_card.Extraction:
    base = dict(
        title="Bart's report card", description="Marge tells Bart she is proud of him.",
        persons=["Bart", "Marge"], tags=["Topic:School", "Person:Bart"],
        summary="Marge records a message for Bart about his report card.",
        facts=["Bart: helped the new student", "Caller: Principal Skinner"],
        quotes=[],
    )
    base.update(kw)
    return diary_card.Extraction(**base)


def _cards(entries, media=None):
    return [diary_card.to_card(e, _extraction(title=f"Entry {i}"),
                               room_id=ROOM, media=media or {})
            for i, e in enumerate(entries)]


# ── A card holds the whole entry ──────────────────────────────────────


class TestACardHoldsTheWholeEntry:

    def test_every_entry_of_a_messy_room_reads_back_unchanged(self):
        """Joined fragments, replies, captions, a spoken date, an
        undateable memo: the corpus covers each, and each must survive
        the trip into a file and back. What a card cannot give back, the
        diary pages cannot show."""
        entries = _compile()
        for entry in entries:
            card = diary_card.to_card(entry, _extraction(), room_id=ROOM, media={})
            back = diary_card.parse(diary_card.render(card))
            assert diary_card.to_entry(back) == diary_card.to_entry(card), entry.event_ids

    def test_the_diary_pages_from_cards_are_the_pages_from_entries(self):
        """The pages are a view of the cards. Built from cards read back
        off disk, they must match the pages built straight from the
        compiled entries, word for word, media included."""
        entries = _compile()
        media = {e.event_ids[0]: f"/media/2026/09/{e.event_ids[0].lstrip('$')}.m4a"
                 for e in entries if e.kind == "voice"}
        cards = [diary_card.parse(diary_card.render(c)) for c in _cards(entries, media)]

        direct = diary.pages_for(
            [replace(e, gist="", moments=[]) for e in entries],
            room_id=ROOM, media=media)
        from_cards = diary.pages_for(
            [replace(diary_card.to_entry(c), gist="", moments=[]) for c in cards],
            room_id=ROOM, media=diary_card.media_of(cards))
        assert from_cards == direct

    def test_a_long_recording_shows_the_cards_summary_and_its_quotes(self):
        """The distilled view of a long recording (a narrative line, a
        few quotes, the transcript folded away) is drawn from the card:
        its summary and its quotes, each still checked against the
        words on the card before it may render as a quote."""
        spoken = " ".join(["We went to the lake today."] * 40
                          + ["Bart lost a shoe in the water."])
        entry = diary.Entry(on=date(2026, 6, 12), confidence="sent", basis="",
                            kind="voice", sender="marge", body=spoken,
                            event_ids=["$lake"])
        card = diary_card.to_card(entry, _extraction(
            summary="Marge tells of a day at the lake.",
            quotes=["Bart lost a shoe in the water.", "Words nobody said."]),
            room_id=ROOM, media={})
        page = diary.render_month([diary_card.to_entry(diary_card.parse(
            diary_card.render(card)))], room_id=ROOM)

        assert "Marge tells of a day at the lake." in page
        assert "> [!quote] Bart lost a shoe in the water." in page
        assert "Words nobody said." not in page

    def test_a_card_is_an_okf_record_of_type_diary(self):
        """The vault's own validator accepts it, and it carries the OKF
        fields every record has, so search, the agent and an OKF export
        read it like a note or a document."""
        entry = _entry_for(_compile(), "memo-bart-zeugnis")
        text = diary_card.render(diary_card.to_card(entry, _extraction(), room_id=ROOM, media={}))
        fm = parse_frontmatter(text)

        assert validate(fm) == []
        assert fm["type"] == "diary"
        assert fm["date"] == "2026-03-16"
        assert fm["resource"] == f"https://matrix.to/#/{ROOM}/{entry.event_ids[0]}"
        for field in ("title", "description", "persons", "tags", "timestamp", "filed_by"):
            assert fm.get(field), field
        assert "> [!summary]" in text

    def test_a_card_without_a_reading_is_still_a_record(self):
        """When the model gives nothing back, the family's words are still
        worth keeping. The card is written with what the room says and a
        plain title, and a later run can fill in the rest."""
        entry = _entry_for(_compile(), "memo-bart-zeugnis")
        card = diary_card.to_card(entry, diary_card.Extraction(), room_id=ROOM, media={})
        fm = parse_frontmatter(diary_card.render(card))

        assert validate(fm) == []
        assert fm["title"]
        assert entry.body.strip() in diary_card.render(card)


# ── A card lives at one path ──────────────────────────────────────────


class TestACardLivesAtOnePath:

    def test_the_path_is_the_day_and_the_first_message(self):
        """Under `entries/`, apart from the month pages the wiki serves at
        `diary/<year>/<month>`."""
        entry = _entry_for(_compile(), "memo-bart-zeugnis")
        card = diary_card.to_card(entry, _extraction(), room_id=ROOM, media={})
        path = diary_card.card_path(card, bucket=BUCKET)

        assert path.startswith(f"{BUCKET}/diary/entries/2026/03/2026-03-16-")
        assert path.endswith(".md")

    def test_a_new_title_does_not_move_a_card(self):
        entry = _entry_for(_compile(), "memo-bart-zeugnis")
        first = diary_card.to_card(entry, _extraction(title="Report card"), room_id=ROOM, media={})
        again = diary_card.to_card(entry, _extraction(title="Proud of Bart"), room_id=ROOM, media={})
        assert diary_card.card_path(first, bucket=BUCKET) == diary_card.card_path(again, bucket=BUCKET)

    def test_recompiling_the_same_room_writes_the_same_files(self):
        first = {diary_card.card_path(c, bucket=BUCKET): diary_card.render(c)
                 for c in _cards(_compile())}
        again = {diary_card.card_path(c, bucket=BUCKET): diary_card.render(c)
                 for c in _cards(_compile())}
        assert first == again
        assert len(first) == len(_compile())


# ── A person's edit wins ──────────────────────────────────────────────


def _vault(cards) -> dict[str, str]:
    return {diary_card.card_path(c, bucket=BUCKET): diary_card.render(c) for c in cards}


class TestAPersonsEditWins:

    def test_a_card_as_written_is_the_compilers_own(self):
        card = _cards(_compile())[0]
        assert not diary_card.edited_by_hand(diary_card.render(card))

    def test_changing_a_word_makes_it_a_persons_card(self):
        text = diary_card.render(_cards(_compile())[0])
        assert diary_card.edited_by_hand(text.replace("Entry 0", "Entry zero"))

    def test_trailing_whitespace_from_an_editor_is_not_an_edit(self):
        text = diary_card.render(_cards(_compile())[0])
        assert not diary_card.edited_by_hand(text.rstrip("\n") + "\n\n")

    def test_an_unchanged_room_writes_nothing(self):
        cards = _cards(_compile())
        plan = diary_card.plan(_vault(cards), cards, bucket=BUCKET, complete=True)
        assert plan.writes == {} and plan.deletes == [] and plan.kept == []

    def test_a_new_entry_is_written(self):
        cards = _cards(_compile())
        plan = diary_card.plan(_vault(cards[:-1]), cards, bucket=BUCKET, complete=True)
        assert list(plan.writes) == [diary_card.card_path(cards[-1], bucket=BUCKET)]

    def test_a_card_the_compiler_owns_follows_its_source(self):
        """A reply arrives, a message is edited in the room: the card is
        rewritten, because nobody has put anything of their own in it."""
        cards = _cards(_compile())
        changed = replace(cards[0], replies=[("homer", "Mmm, report card.")])
        plan = diary_card.plan(_vault(cards), [changed, *cards[1:]], bucket=BUCKET, complete=True)
        path = diary_card.card_path(changed, bucket=BUCKET)
        assert plan.writes == {path: diary_card.render(changed)}

    def test_an_edited_card_is_never_overwritten(self):
        """The family fixed a misheard name. The room changes afterwards
        (a new reply), and the next run must still leave the card as the
        person left it, and say so rather than skip it silently."""
        cards = _cards(_compile())
        vault = _vault(cards)
        path = diary_card.card_path(cards[0], bucket=BUCKET)
        vault[path] = vault[path].replace("Entry 0", "Bart's first day")
        changed = replace(cards[0], replies=[("homer", "Woohoo.")])

        plan = diary_card.plan(vault, [changed, *cards[1:]], bucket=BUCKET, complete=True)
        assert path not in plan.writes
        assert plan.kept == [path]

    def test_the_pages_show_the_persons_correction(self):
        """What a person writes on the card is what the diary says."""
        entry = _entry_for(_compile(), "memo-bart-zeugnis")
        text = diary_card.render(diary_card.to_card(entry, _extraction(), room_id=ROOM, media={}))
        fixed = text.replace("Principal Skinner called", "Principal Seymour Skinner called").replace(
            'date: "2026-03-16"', 'date: "2026-03-17"')
        page = diary.render_month([diary_card.to_entry(diary_card.parse(fixed))], room_id=ROOM)

        assert "Tuesday, 17 March" in page
        assert "Principal Seymour Skinner called" in page

    def test_a_moved_date_moves_the_card_the_compiler_owns(self):
        cards = _cards(_compile())
        moved = replace(cards[0], on=date(2026, 1, 2))
        plan = diary_card.plan(_vault(cards), [moved, *cards[1:]], bucket=BUCKET, complete=True)

        assert plan.deletes == [diary_card.card_path(cards[0], bucket=BUCKET)]
        assert list(plan.writes) == [diary_card.card_path(moved, bucket=BUCKET)]

    def test_a_card_whose_messages_are_gone_is_removed_after_a_full_read(self):
        """A message deleted in the room takes its card with it, but only
        when the run read the whole room: a bounded preview (`--limit`)
        has not seen the older messages and must not treat them as gone."""
        cards = _cards(_compile())
        gone = diary_card.card_path(cards[0], bucket=BUCKET)

        full = diary_card.plan(_vault(cards), cards[1:], bucket=BUCKET, complete=True)
        partial = diary_card.plan(_vault(cards), cards[1:], bucket=BUCKET, complete=False)
        assert full.deletes == [gone]
        assert partial.deletes == []

    def test_an_edited_card_outlives_its_messages(self):
        cards = _cards(_compile())
        vault = _vault(cards)
        gone = diary_card.card_path(cards[0], bucket=BUCKET)
        vault[gone] = vault[gone].replace("Entry 0", "Keep this")

        plan = diary_card.plan(vault, cards[1:], bucket=BUCKET, complete=True)
        assert plan.deletes == [] and plan.kept == [gone]


# ── What a model read, held to the household's vocabulary ─────────────

from stack.ontology import Ontology  # noqa: E402

SEED_ONTOLOGY = Ontology.load(_REPO_ROOT / "stacklets" / "memory" / "seeds" / "ontology.toml")
PEOPLE = {"bart": "Bart", "bartholomew": "Bart", "marge": "Marge", "homer": "Homer"}


def _read(**raw) -> diary_card.Extraction:
    return diary_card.extraction_from(raw, ontology=SEED_ONTOLOGY, language="en",
                                      people=PEOPLE, model="test-model")


class TestWhatAModelReadIsHeldToTheHouseholdsVocabulary:
    """The card's tags and people mean the same thing as a document's:
    topics from the ontology, people from the household's own pages.
    Anything else the model offers is dropped, because a tag nobody
    defined splits search, and a person nobody is becomes a stranger in
    the family's diary."""

    def test_a_topic_is_named_the_way_the_household_names_it(self):
        assert _read(topics=["school"]).tags == ["Education"]
        assert _read(topics=["Schule"]).tags == ["Education"]

    def test_a_document_type_offered_as_a_topic_is_dropped(self):
        assert _read(topics=["Invoice", "Education"]).tags == ["Education"]

    def test_a_topic_the_ontology_does_not_know_is_dropped(self):
        assert _read(topics=["Unicorns"]).tags == []

    def test_a_misheard_name_does_not_become_a_person(self):
        """Whisper heard "Bark". The household has no Bark."""
        assert _read(persons=["Bark", "Marge"]).persons == ["Marge"]

    def test_a_person_is_named_by_their_canonical_name(self):
        read = _read(persons=["Bartholomew", "bart"])
        assert read.persons == ["Bart"]
        assert read.tags == ["Person: Bart"]

    def test_topics_come_before_people_in_the_tags_like_on_a_document(self):
        assert _read(topics=["school"], persons=["Marge"]).tags == ["Education", "Person: Marge"]

    def test_the_words_come_through_trimmed_and_facts_stay_facts(self):
        read = _read(title="  Report card ", summary="Marge  records\na message.",
                     facts=["Bart: helped", "", 7], quotes=["One.", "Two.", "Three.", "Four."])
        assert read.title == "Report card"
        assert read.summary == "Marge records a message."
        assert read.facts == ["Bart: helped"]
        assert read.quotes == ["One.", "Two.", "Three."]
        assert read.model == "test-model"

    def test_an_answer_that_is_not_an_object_reads_as_nothing(self):
        empty = diary_card.extraction_from(None, ontology=SEED_ONTOLOGY, language="en",
                                           people=PEOPLE, model="m")
        assert empty == diary_card.Extraction(model="m")


class TestTheSeedOntologyCoversFamilyLife:
    """The seed was written for documents. The diary is the first reader
    that is about family life, and without topics for it the model files
    a saxophone concert under "Memory" and a bike ride under "Child"."""

    def test_family_life_resolves_to_its_own_topics(self):
        assert _read(topics=["concert"]).tags == ["Music"]
        assert _read(topics=["swimming"]).tags == ["Sport"]
        assert _read(topics=["birthday"]).tags == ["Celebration"]
        assert _read(topics=["playdate"]).tags == ["Friends"]
        assert _read(topics=["drawing"]).tags == ["Hobby"]
        assert _read(topics=["family time"]).tags == ["Family Life"]

    def test_the_household_language_names_them(self):
        read = diary_card.extraction_from(
            {"topics": ["Konzert", "Geburtstag"]}, ontology=SEED_ONTOLOGY,
            language="de", people=PEOPLE, model="")
        assert read.tags == ["Musik", "Feier"]

    def test_the_family_life_topics_share_no_word_with_anything_else(self):
        """A name or synonym shared by two topics resolves to whichever
        comes first, silently. The family-life topics must not take a
        word another topic or document type already answers to."""
        life = {"music", "sport", "hobby", "celebration", "friends", "family_life"}
        owners: dict[tuple[str, str], set[str]] = {}
        for kind, items in (("topic", SEED_ONTOLOGY.topics),
                            ("doctype", SEED_ONTOLOGY.doctypes)):
            for key, item in items.items():
                for lang in ("de", "en"):
                    for word in [item.name(lang), *item.synonyms_for(lang)]:
                        owners.setdefault((lang, word.lower()), set()).add(f"{kind}.{key}")
        shared = {w: o for w, o in owners.items()
                  if len(o) > 1 and o & {f"topic.{k}" for k in life}}
        assert shared == {}
        assert life <= set(SEED_ONTOLOGY.topics)
