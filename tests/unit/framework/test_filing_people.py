"""The rule that places a capture's names against the household's people (`stack.filing.people`).

Pure logic, so it has tests of its own: the three outcomes of a name, the
names read from a note's words, and the sender as the last resort. The
capture as the archivist runs it is tested in
`tests/unit/stacklets/test_capture_places_people.py`. The household is the
Simpsons, declared in person files under a fabricated vault.
"""

from __future__ import annotations

from stack.filing.people import People

SIMPSONS = {"homer": "Homer", "mr. simpson": "Homer", "lisa": "Lisa", "marge": "Marge"}


def _vault(tmp_path, *people: tuple[str, str, tuple[str, ...]]):
    """A vault whose shared bucket declares `people` as (id, name, aliases)."""
    folder = tmp_path / "vault" / "family" / "people"
    folder.mkdir(parents=True)
    for pid, name, aliases in people:
        said = "".join(f"\n  - {a}" for a in aliases)
        (folder / f"{pid}.md").write_text(
            f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n"
            + (f"aliases:{said}\n" if aliases else "") + f"---\n\n# {name}\n")
    return tmp_path / "vault"


def test_a_known_name_is_placed_and_a_strange_one_is_not():
    """Given the model names Mr. Simpson and Mr. Burns, then the card is
    about Homer, and Mr. Burns is the one name nobody goes by."""
    placed = People(SIMPSONS).place(["Mr. Simpson", "Mr. Burns"])

    assert (placed.names, placed.unplaced) == (["Homer"], ["Mr. Burns"])


def test_a_name_in_the_words_joins_and_a_capitalised_word_does_not():
    """Given "Lisa came along. Will it rain in May?", then Lisa joins the
    card and neither Will nor May does: nobody in the household has
    those names."""
    placed = People(SIMPSONS).place(["Homer"], said="Lisa came along. Will it rain in May?")

    assert placed.names == ["Homer", "Lisa"]


def test_a_household_member_called_will_is_found_in_the_words():
    """Given a household with a Will, then "Will it rain in May?" names
    him: only the household's own names match, as on a diary entry."""
    assert People({"will": "Will"}).place([], said="Will it rain in May?").names == ["Will"]


def test_with_no_name_left_the_card_is_about_its_sender():
    """Given the model names only Mr. Burns for Homer's note, then the
    card is about Homer; a sender nobody declared counts as known too."""
    assert People(SIMPSONS).place(["Mr. Burns"], sender="Homer").names == ["Homer"]
    assert People().place(["Mr. Burns"], sender="Marge").names == ["Marge"]


def test_a_bookmark_reads_no_names_from_its_words():
    """Given a bookmark's answer whose title names Lisa, then nobody joins
    from it: a page's words are someone else's."""
    answer = {"title": "Lisa came along", "persons": []}

    assert People(SIMPSONS).place_answer(answer, note=False, sender="Marge").names == ["Marge"]
    assert People(SIMPSONS).place_answer(answer, note=True, sender="Marge").names == ["Lisa"]


def test_a_note_names_the_people_of_words_the_card_does_not_keep():
    """Given a clean note's answer that names Homer and a summary the card
    drops ("Lisa came along"), then the note names Homer and Lisa: a diary
    entry from the same words names both, and a bookmark still names nobody
    from them."""
    answer = {"title": "Lake weekend", "persons": ["Homer"], "summary": ""}

    assert People(SIMPSONS).place_answer(answer, note=True, said="Lisa came along.").names == ["Homer", "Lisa"]
    assert People(SIMPSONS).place_answer(answer, note=False, said="Lisa came along.").names == ["Homer"]


def test_the_person_files_place_every_name_a_person_goes_by(tmp_path):
    """Given Homer's file with the alias "Mr. Simpson", then his full name,
    his first name and his alias all place him as Homer."""
    people = People.of(_vault(tmp_path, ("homer", "Homer Simpson", ("Mr. Simpson",))))

    assert people.place(["Homer Simpson", "homer", "Mr. Simpson"]).names == ["Homer"]


def test_without_person_files_the_family_names_are_known(tmp_path):
    """Given a vault without person files and the accounts Homer and Lisa,
    then Lisa is known by name and Mr. Burns is not."""
    (tmp_path / "vault").mkdir()
    placed = People.of(tmp_path / "vault", account_names="Homer,Lisa").place(["lisa", "Mr. Burns"])

    assert (placed.names, placed.unplaced) == (["Lisa"], ["Mr. Burns"])


def test_a_spelling_on_a_wiki_page_places_the_person(tmp_path):
    """Given Homer's wiki page lists "Homie", then "Homie" is Homer."""
    vault = _vault(tmp_path, ("homer", "Homer Simpson", ()))
    page = tmp_path / "brain" / "homer" / "about.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\nkind: person\ncanonical: Homer\nsynonyms:\n  - Homie\n---\n\n# Homer\n")

    assert People.of(vault, pages=tmp_path / "brain").place(["Homie"]).names == ["Homer"]
