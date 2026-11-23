"""A new note's name, and a card on file as a replay names it (`stack.filing.identity`).

A note's first filing is written at the first free name of its path,
`<name>.md`, `<name>-2.md`, ...: two notes with the same link and title stay
two notes. A replay names the card as the card is, not as the model would
answer now.
"""

from stack.filing.identity import free_name, notes_of, stored_filing
from stack.memory import File

GRILL = "marge/notes/2026/11/grill-charcoal-0c401e.md"


def test_a_free_name_is_the_path_itself():
    assert free_name(GRILL, ["marge/notes/2026/11/tent-3a338e.md"]) == GRILL


def test_a_taken_name_gets_the_next_number_before_the_suffix():
    """The suffix goes before `.md`, so the card keeps its title and hash in its name."""
    assert free_name(GRILL, [GRILL]) == "marge/notes/2026/11/grill-charcoal-0c401e-2.md"


def test_a_name_whose_sidecar_alone_is_taken_is_not_free():
    """A sidecar left without its card still names that card's sources."""
    taken = [GRILL, "marge/notes/2026/11/grill-charcoal-0c401e-2.json"]

    assert free_name(GRILL, taken) == "marge/notes/2026/11/grill-charcoal-0c401e-3.md"


def test_a_captures_notes_are_under_its_place():
    """A personal topic's notes are under the person's topic, not the person's own notes."""
    assert (notes_of("marge"), notes_of("homer/camping")) == ("marge/notes", "homer/camping/notes")


def test_a_card_on_file_is_named_with_the_people_and_tags_it_has():
    text = ("---\ntype: note\ncard_id: note-0c401e0c40\ntitle: Grill charcoal\ndate: \"2026-11-22\"\n"
            "persons:\n  - marge\ntags:\n  - grill\n---\n# Grill charcoal\n")

    stored = stored_filing(File(GRILL, text, "blob"))

    assert (stored.path, stored.card_id, stored.captured_at) == (GRILL, "note-0c401e0c40", "2026-11-22")
    assert stored.card == {"title": "Grill charcoal", "persons": ["marge"], "tags": ["grill"]}


def test_a_card_whose_frontmatter_does_not_parse_is_named_by_its_path():
    stored = stored_filing(File(GRILL, "---\ntitle: [unclosed\n---\n", "blob"))

    assert (stored.path, stored.card_id, stored.card["persons"]) == (GRILL, "", [])
