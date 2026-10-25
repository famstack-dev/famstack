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

import re
from dataclasses import replace
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

from stacklets.memory.bot import diary  # noqa: E402
from stacklets.memory.bot import diary_card  # noqa: E402
from stack.card_render import render_file  # noqa: E402
from stack.frontmatter import parse as parse_frontmatter  # noqa: E402
from .test_memory_diary import _compile, _entry_for  # noqa: E402

ROOM = "!memories:test"
BUCKET = "family"


def _extraction(**kw) -> diary_card.Extraction:
    base = dict(
        title="Bart's report card", description="Marge tells Bart she is proud of him.",
        persons=["bart", "marge"], tags=["Topic:School"],
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
            bucket="family", room_id=ROOM, media=media)
        # What the card adds on purpose (its title, its summary and the
        # anchor its link lands on) is set aside: this pins that nothing
        # of the entry itself is lost.
        from_cards = diary.pages_for(
            [replace(diary_card.to_entry(c), gist="", moments=[], title="", entry_id="")
             for c in cards],
            bucket="family", room_id=ROOM, media=diary_card.media_of(cards))
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
        """It carries the OKF fields every record has, so search, the
        agent and an OKF export read it like a note or a document."""
        entry = _entry_for(_compile(), "memo-bart-zeugnis")
        text = diary_card.render(diary_card.to_card(entry, _extraction(), room_id=ROOM, media={}))
        fm = parse_frontmatter(text)

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
    """The diary files as a compile of `cards` writes them into an empty vault."""
    return diary_card.plan({}, cards, bucket=BUCKET, complete=True).writes


class TestADiaryCardKeepsItsId:
    """A diary card gets `card_id` when the compile first writes it (ADR-016),
    and every later compile and correction keeps it."""

    def test_a_new_card_gets_one(self):
        cards = _cards(_compile())
        for text in _vault(cards).values():
            assert re.fullmatch(r"diary-[0-9a-f]{10}", parse_frontmatter(text)["card_id"])

    def test_a_card_whose_date_moves_keeps_it(self):
        cards = _cards(_compile())
        vault = _vault(cards)
        before = parse_frontmatter(vault[diary_card.card_path(cards[0], bucket=BUCKET)])["card_id"]
        moved = replace(cards[0], on=date(2026, 3, 1), confidence="corrected")
        plan = diary_card.plan(vault, [moved, *cards[1:]], bucket=BUCKET, complete=True,
                               rebuild=True)
        after = plan.writes[diary_card.card_path(moved, bucket=BUCKET)]
        assert parse_frontmatter(after)["card_id"] == before

    def test_an_id_the_migration_added_leaves_the_card_the_compilers(self):
        """Migration 0002 adds the line to a card the compiler signed before
        ids existed. The card must still read as the compiler's, or every
        later compile would leave it alone as a person's."""
        card = _cards(_compile())[0]
        signed = diary_card.render(card)
        with_id = signed.replace("type: diary\n", "type: diary\ncard_id: diary-0b6e2d91aa\n", 1)
        assert not diary_card.edited_by_hand(with_id)
        plan = diary_card.plan({diary_card.card_path(card, bucket=BUCKET): with_id}, [card],
                               bucket=BUCKET, complete=True)
        assert plan.writes == {} and plan.kept == []

    def test_the_report_names_each_card_by_the_id_it_was_written_with(self):
        """The archivist posts the cards a run filed, and their links use the id."""
        cards = _cards(_compile())
        plan = diary_card.plan({}, cards, bucket=BUCKET, complete=True)
        report = _diary_cli()._report(cards, plan, {}, set(), bucket=BUCKET, room_id=ROOM,
                                      posted=set())
        written = {parse_frontmatter(text)["card_id"] for text in plan.writes.values()}
        assert {c["card_id"] for c in report["cards"]} == written


def _diary_cli():
    """The diary command, `stacklets.memory.bot.cli.diary`, a module of the
    memory bot's `cli` package, which the docs stacklet's `cli` does not
    shadow."""
    from stacklets.memory.bot.cli import diary
    return diary


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
        vault = _vault(cards)
        changed = replace(cards[0], replies=[("homer", "Mmm, report card.")])
        plan = diary_card.plan(vault, [changed, *cards[1:]], bucket=BUCKET, complete=True)
        path = diary_card.card_path(changed, bucket=BUCKET)
        card_id = parse_frontmatter(vault[path])["card_id"]
        assert plan.writes == {path: diary_card.render(replace(changed, card_id=card_id))}

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

    def test_a_moved_date_moves_the_card_the_compiler_owns_on_a_rebuild(self):
        """The date comes from the model's reading of the room. A new
        reading of an unchanged entry rewrites no card until the admin
        asks for a rebuild, and then the card moves to its new day."""
        cards = _cards(_compile())
        moved = replace(cards[0], on=date(2026, 1, 2))
        plain = diary_card.plan(_vault(cards), [moved, *cards[1:]], bucket=BUCKET, complete=True)
        plan = diary_card.plan(_vault(cards), [moved, *cards[1:]], bucket=BUCKET, complete=True,
                               rebuild=True)

        assert plain.writes == {} and plain.deletes == []
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
from stack.people import Person  # noqa: E402

SEED_ONTOLOGY = Ontology.load(_REPO_ROOT / "stacklets" / "memory" / "seeds" / "ontology.toml")
PEOPLE = {"bart": "Bart", "bartholomew": "Bart", "marge": "Marge", "homer": "Homer"}
# The person files. Homer's is not written yet: the household knows his name,
# but no declared person goes by it.
DECLARED = [Person("bart", "Bart Simpson", ("Bartholomew",)), Person("marge", "Marge Simpson")]


def _read(**raw) -> diary_card.Extraction:
    return diary_card.extraction_from(raw, ontology=SEED_ONTOLOGY, language="en",
                                      people=PEOPLE, declared=DECLARED, model="test-model")


def _read_with_lisa(**raw) -> diary_card.Extraction:
    return diary_card.extraction_from(raw, ontology=SEED_ONTOLOGY, language="en",
                                      people={**PEOPLE, "lisa": "Lisa"},
                                      declared=[*DECLARED, Person("lisa", "Lisa Simpson")],
                                      model="test-model")


class TestWhatAModelReadIsHeldToTheHouseholdsVocabulary:
    """The card's tags and people mean the same thing as a document's:
    topics from the ontology, people from the household's own pages.
    Anything else the model offers is dropped, because a tag nobody
    defined splits search, and a person nobody is becomes a stranger in
    the family's diary."""

    def test_a_topic_is_tagged_by_its_id_whatever_the_household_calls_it(self):
        """ADR-017 §4: `tags` holds the topic's id, as on every other card.
        The label a person reads comes from the ontology, in the household's
        language, so a German and an English household tag one subject alike."""
        assert _read(topics=["school"]).tags == ["education"]
        assert _read(topics=["Schule"]).tags == ["education"]
        assert _read(topics=["Medical"]).tags == ["medical"]
        german = diary_card.extraction_from(
            {"topics": ["Gesundheit"]}, ontology=SEED_ONTOLOGY, language="de",
            people=PEOPLE, declared=DECLARED, model="")
        assert german.tags == ["medical"]

    def test_a_topic_named_twice_is_tagged_once(self):
        assert _read(topics=["Medical", "Gesundheit", "medical"]).tags == ["medical"]

    def test_a_document_type_offered_as_a_topic_is_dropped(self):
        assert _read(topics=["Invoice", "Education"]).tags == ["education"]

    def test_a_topic_the_ontology_does_not_know_is_dropped(self):
        assert _read(topics=["Unicorns"]).tags == []

    def test_a_misheard_name_does_not_become_a_person(self):
        """Whisper heard "Bark". The household has no Bark."""
        assert _read(persons=["Bark", "Marge"]).persons == ["marge"]

    def test_a_person_is_named_by_their_id(self):
        """ADR-017 §4: `persons` holds ids, by whatever name the entry used."""
        assert _read(persons=["Bartholomew", "bart"]).persons == ["bart"]

    def test_a_name_no_declared_person_goes_by_stays_off_the_card_and_is_reported(self):
        read = _read(persons=["Homer", "Bart"])
        assert read.persons == ["bart"]
        assert read.unknown == ["Homer"]

    def test_people_are_not_tags_like_on_a_document(self):
        """`persons` says who; a `Person: <name>` tag would say it twice."""
        assert _read(topics=["school"], persons=["Marge"]).tags == ["education"]

    def test_the_words_come_through_trimmed_and_facts_stay_facts(self):
        read = _read(title="  Report card ", summary="Marge  records\na message.",
                     facts=["Bart: helped", "", 7], quotes=["One.", "Two.", "Three.", "Four."])
        assert read.title == "Report card"
        assert read.summary == "Marge records a message."
        assert read.facts == ["Bart: helped"]
        assert read.quotes == ["One.", "Two.", "Three."]
        assert read.model == "test-model"

    def test_a_fact_is_the_fact_not_the_list_marker_the_model_copied(self):
        """Shown a card's facts as a list, a model hands them back with the
        markers on, and the callout would render "- - Date"."""
        assert _read(facts=["- Lisa: built the tower", "* Place: lake", "• Bart: dug the moat",
                            "- - Maggie: first tooth"]).facts == [
            "Lisa: built the tower", "Place: lake", "Bart: dug the moat", "Maggie: first tooth"]

    def test_a_fact_says_who_or_what_it_is_about_not_what_kind_it_is(self):
        """"Action: Maggie knocked it over" repeats the summary as a form.
        A fact names who or what it is about; the card's date, people and
        summary already carry the rest."""
        read = _read(facts=["Action: Maggie knocked it over", "Date: 2026-03-17",
                            "Person: Maggie", "Event: first tooth", "Speaker: Marge",
                            "Maggie: first tooth", "Place: Springfield Lake",
                            "Swimming badge: 25 metres"])
        assert read.facts == ["Maggie: first tooth", "Place: Springfield Lake",
                              "Swimming badge: 25 metres"]

    def test_everyone_the_card_names_is_someone_it_is_about(self):
        """A card that says "Marge and Bart or Lisa talk about school" is
        about all three until a reply settles who it was, whatever the
        model put in its list of people."""
        ex = _read_with_lisa(persons=["Marge"], title="School fire drill",
                             summary="Marge and Bart or Lisa talk about a fire drill.")
        assert ex.persons == ["marge", "bart", "lisa"]

    def test_a_name_inside_another_word_names_no_one(self):
        ex = _read_with_lisa(persons=[], summary="Marge bakes Bartlett pears.")
        assert ex.persons == ["marge"]

    def test_a_name_written_as_an_ordinary_word_names_no_one(self):
        ex = diary_card.extraction_from(
            {"summary": "Marge will bake a cake."}, ontology=SEED_ONTOLOGY,
            language="en", people={**PEOPLE, "will": "Will"},
            declared=[*DECLARED, Person("will", "Will")], model="test-model")
        assert ex.persons == ["marge"]

    def test_an_answer_that_is_not_an_object_reads_as_nothing(self):
        empty = diary_card.extraction_from(None, ontology=SEED_ONTOLOGY, language="en",
                                           people=PEOPLE, declared=DECLARED, model="m")
        assert empty == diary_card.Extraction(model="m")


class TestTheSeedOntologyCoversFamilyLife:
    """The seed was written for documents. The diary is the first reader
    that is about family life, and without topics for it the model files
    a saxophone concert under "Memory" and a bike ride under "Child"."""

    def test_family_life_resolves_to_its_own_topics(self):
        assert _read(topics=["concert"]).tags == ["music"]
        assert _read(topics=["swimming"]).tags == ["sport"]
        assert _read(topics=["birthday"]).tags == ["celebration"]
        assert _read(topics=["playdate"]).tags == ["friends"]
        assert _read(topics=["drawing"]).tags == ["hobby"]
        assert _read(topics=["family time"]).tags == ["family_life"]

    def test_the_households_own_words_find_them(self):
        read = diary_card.extraction_from(
            {"topics": ["Konzert", "Geburtstag"]}, ontology=SEED_ONTOLOGY,
            language="de", people=PEOPLE, declared=DECLARED, model="")
        assert read.tags == ["music", "celebration"]

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


# ── Corrections ───────────────────────────────────────────────────────


def _card():
    entry = _entry_for(_compile(), "memo-bart-zeugnis")
    return diary_card.to_card(entry, _extraction(), room_id=ROOM, media={})


class TestACorrectionMakesTheCardTheFamilys:
    """A correction is a family member's reply in the card's thread. It is
    applied once, to the card as it is in the vault, and from then on the
    card is the family's: the nightly compile leaves it alone, like a card
    edited by hand. The vault commit that applies it keeps the words."""

    def test_a_correction_updates_what_the_model_read_and_leaves_the_words(self):
        card = _card()
        fixed = diary_card.correct(card, _extraction(
            title="Lisa's report card", persons=["Lisa", "Marge"],
            facts=["Report card: Lisa's"]), event_id="$fix")

        assert fixed.title == "Lisa's report card"
        assert fixed.persons == ["Lisa", "Marge"]
        assert fixed.facts == ["Report card: Lisa's"]
        assert fixed.body == card.body
        assert fixed.replies == card.replies
        assert fixed.event_ids == [*card.event_ids, "$fix"]

    def test_a_correction_can_move_the_date(self):
        fixed = diary_card.correct(_card(), _extraction(date="2026-03-17"), event_id="$fix")

        assert fixed.on == date(2026, 3, 17)
        assert fixed.confidence == "corrected"
        assert diary_card.to_entry(fixed).basis == diary.basis_text("corrected")

    def test_a_correction_can_say_who_the_memory_was_for(self):
        """"This was for Marge, not Homie": the reading took a nickname for
        the person spoken to, and the family puts it right."""
        card = replace(_card(), addressee="Homie")
        fixed = diary_card.correct(card, _read(addressee="marge"), event_id="$fix")

        assert fixed.addressee == "Marge"

    def test_a_correction_that_names_no_one_keeps_who_it_was_for(self):
        card = replace(_card(), addressee="Bart")
        assert diary_card.correct(card, _read(), event_id="$fix").addressee == "Bart"

    def test_someone_outside_the_household_is_kept_as_the_family_named_them(self):
        """A memo can be for "the kids" or for Grandpa: the family's words
        stand when they name no household member."""
        card = replace(_card(), addressee="Bart")
        fixed = diary_card.correct(card, _read(addressee="  the kids "), event_id="$fix")
        assert fixed.addressee == "the kids"

    def test_a_correction_without_a_date_keeps_the_date(self):
        fixed = diary_card.correct(_card(), _extraction(), event_id="$fix")

        assert fixed.on == date(2026, 3, 16)
        assert fixed.confidence == "spoken"

    def test_the_model_cannot_redate_an_entry_outside_a_correction(self):
        """The spoken date or the day it was sent is the room's own
        evidence. A model's guess does not override it; a person does."""
        entry = _entry_for(_compile(), "memo-bart-zeugnis")
        card = diary_card.to_card(entry, _extraction(date="2020-01-01"), room_id=ROOM, media={})

        assert card.on == date(2026, 3, 16)

    def test_a_corrected_card_is_the_familys_and_the_compile_keeps_it(self):
        cards = _cards(_compile())
        fixed = diary_card.correct(cards[0], _extraction(title="Fixed"), event_id="$fix")
        vault = _vault(cards)
        del vault[diary_card.card_path(cards[0], bucket=BUCKET)]
        vault[diary_card.card_path(fixed, bucket=BUCKET)] = diary_card.render(fixed, family_owned=True)

        assert diary_card.edited_by_hand(diary_card.render(fixed, family_owned=True))
        plan = diary_card.plan(vault, cards, bucket=BUCKET, complete=True)
        assert plan.writes == {} and plan.deletes == []

    def test_a_corrected_card_is_kept_without_being_reported_every_night(self):
        """"Kept" tells the family the room moved on under a card they
        edited. A corrected card is theirs by design; saying so every
        night would be noise."""
        cards = _cards(_compile())
        fixed = diary_card.correct(cards[0], _extraction(title="Fixed"), event_id="$fix")
        vault = _vault(cards)
        vault[diary_card.card_path(cards[0], bucket=BUCKET)] = diary_card.render(fixed, family_owned=True)
        changed = replace(cards[0], replies=[("homer", "A new reply.")])

        plan = diary_card.plan(vault, [changed, *cards[1:]], bucket=BUCKET, complete=True)
        assert plan.kept == [] and plan.writes == {}

    def test_the_model_offers_a_date_only_as_an_iso_day(self):
        assert _read(date="17 March").date == ""
        assert _read(date="2026-03-17").date == "2026-03-17"


# ── The card on the diary page ────────────────────────────────────────


def _page_for(card) -> str:
    return diary.render_month([diary_card.to_entry(card)], room_id=ROOM)


class TestTheDiaryPageShowsTheCard:
    """Each entry on the page opens with its card: the title, who recorded
    it and what it is, and the summary as a short narrative line. Then
    the family's words, every one of them. A correction to the card is
    therefore a correction to the page."""

    def _note(self, **kw):
        entry = diary.Entry(on=date(2026, 9, 20), confidence="sent", basis="",
                            kind="voice", sender="marge", body="We went to the lake today.",
                            event_ids=["$lake"], duration_ms=7000)
        for k, v in kw.items():
            setattr(entry, k, v)
        return entry

    def test_the_title_heads_the_entry_and_says_who_and_what(self):
        card = diary_card.to_card(self._note(), _extraction(title="A day at the lake"),
                                  room_id=ROOM, media={})
        page = _page_for(card)

        assert "### A day at the lake" in page
        assert "*Marge · Voice note, 0:07*" in page

    def test_the_card_is_anchored_by_its_entry_id(self):
        """`/go/diary/<entry_id>` lands on the month page at this anchor.
        The heading's own anchor follows the title, which a correction
        rewrites; the entry id never changes."""
        card = diary_card.to_card(self._note(), _extraction(title="A day at the lake"),
                                  room_id=ROOM, media={})
        page = _page_for(card)

        assert f'<a id="{card.entry_id}"></a>\n### A day at the lake' in page

    LONG = ("We went to the lake today. " + "Bart built a sand tower by the water, "
            "Lisa counted the ducks, and Maggie slept in the shade the whole time. " * 5)

    def test_the_summary_opens_the_entry_and_every_word_follows(self):
        card = diary_card.to_card(self._note(body=self.LONG),
                                  _extraction(summary="Marge tells of the lake."),
                                  room_id=ROOM, media={})
        page = _page_for(card)

        assert page.index("Marge tells of the lake.") < page.index("We went to the lake today.")

    def test_a_short_entry_is_its_words_without_a_summary(self):
        """A summary of two sentences says them again (memory cards review,
        C1), so a short entry has none, whatever the model wrote."""
        card = diary_card.to_card(self._note(), _extraction(summary="Marge tells of the lake."),
                                  room_id=ROOM, media={})
        page = _page_for(card)

        assert card.summary == ""
        assert "Marge tells of the lake." not in page
        assert "We went to the lake today." in page

    def test_a_short_entrys_card_has_no_empty_block(self):
        card = diary_card.to_card(self._note(), _extraction(summary="Marge tells of the lake.",
                                                            facts=[]),
                                  room_id=ROOM, media={})
        text = diary_card.render(card)
        shown = render_file(text).markdown

        assert "[!summary]" not in text
        assert "We went to the lake today." in shown
        assert "\n\n\n" not in shown

    def test_a_correction_keeps_a_short_entry_without_a_summary(self):
        card = diary_card.to_card(self._note(), _extraction(), room_id=ROOM, media={})
        fixed = diary_card.correct(card, _extraction(summary="Lisa went to the lake."),
                                   event_id="$fix")
        assert fixed.summary == ""

    def test_a_correction_shows_on_the_page(self):
        card = diary_card.to_card(self._note(body=self.LONG),
                                  _extraction(summary="Bart built the tower."),
                                  room_id=ROOM, media={})
        fixed = diary_card.correct(card, _extraction(summary="Lisa built the tower.",
                                                     title="Lisa's tower"), event_id="$fix")
        page = _page_for(fixed)

        assert "### Lisa's tower" in page and "Lisa built the tower." in page
        assert "Bart built the tower." not in page

    def test_a_letter_to_one_person_keeps_its_title_and_no_summary(self):
        """A message spoken to one person is posted whole, as agreed for
        letters: nothing generated stands between the reader and it."""
        card = diary_card.to_card(self._note(addressee="Lisa"),
                                  _extraction(title="For Lisa", summary="Marge talks to Lisa."),
                                  room_id=ROOM, media={})
        page = _page_for(card)

        assert "### For Lisa" in page and "*Marge, for Lisa · Voice note, 0:07*" in page
        assert "Marge talks to Lisa." not in page
