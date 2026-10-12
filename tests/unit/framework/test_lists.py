"""The family's lists: one file each, one checkbox line per item.

A list is read by Obsidian, edited in Forgejo, changed by the memory
commands and the agent, and rendered by the wiki, so the format is the
contract (docs/design/brain/vault-format.md, `list`). These tests pin:

- An item's fields are the ones Obsidian's Tasks plugin reads: `📅` due,
  `✅` done, at the end of the line.
- People and source records are relative Markdown links, which
  Obsidian, Forgejo and the wiki all follow, and they survive a move to
  another file.
- Nothing the family wrote is reinterpreted: a link inside the sentence,
  a web link, an unknown field or a note under a heading stays as it is.
- A store list keeps its sections in walking order, whatever order the
  items arrive in.
"""

from __future__ import annotations

import pytest

from stack.lists import (
    Item,
    Link,
    ListError,
    format_item,
    insert_item,
    list_path,
    new_list,
    parse_item,
    person_link,
    read_list,
    relink,
)

GROCERIES = list_path("einkaufsliste", bucket="family")
STORE = ["Gemüse & Obst", "Brot & Backwaren", "Milchprodukte & Eier", "Drogerie"]


def _groceries() -> str:
    return new_list("Einkaufsliste", description="Was wir im Supermarkt brauchen",
                    topics=["einkaeufe-besorgungen"], sections=STORE)


def _add(text: str, name: str, section: str = "", **fields) -> str:
    return insert_item(text, Item(name, section=section, **fields), path=GROCERIES)


def _body(text: str) -> str:
    return text.split("\n---\n", 1)[1]


class TestAnItemLine:
    def test_due_and_done_dates_are_the_obsidian_tasks_fields(self):
        item = parse_item("- [x] Pack sunscreen 📅 2026-10-12 ✅ 2026-10-10", path=GROCERIES)
        assert (item.text, item.done, item.due, item.done_on) == \
            ("Pack sunscreen", True, "2026-10-12", "2026-10-10")

    def test_people_are_relative_links_to_their_person_files(self):
        item = parse_item("- [ ] Sign the field trip form [Homer](../people/homer.md) "
                          "[Marge](../people/marge.md) 📅 2026-10-20", path=GROCERIES)
        assert item.people == ("homer", "marge")
        assert item.text == "Sign the field trip form"

    def test_a_link_to_a_record_names_where_the_item_came_from(self):
        item = parse_item("- [ ] Pay the hospital invoice "
                          "[Rechnung](../documents/2026/09/2026-09-30-hospital-invoice-p63.md)",
                          path=GROCERIES)
        assert item.sources == ("family/documents/2026/09/2026-09-30-hospital-invoice-p63.md",)
        assert item.people == ()

    def test_written_back_it_reads_the_same(self):
        line = ("- [ ] Book the lodge [Homer](../people/homer.md) "
                "[Buchung](../documents/2026/10/lodge-p70.md) 📅 2026-10-14")
        assert format_item(parse_item(line, path=GROCERIES), path=GROCERIES) == line

    def test_a_new_item_is_written_with_its_fields_at_the_end(self):
        item = Item("Book the lodge", links=(person_link("homer", "Homer", bucket="family"),),
                    due="2026-10-14")
        assert format_item(item, path=GROCERIES) == \
            "- [ ] Book the lodge [Homer](../people/homer.md) 📅 2026-10-14"

    def test_its_links_follow_it_to_another_file(self):
        """An archived item still opens the same person file."""
        item = parse_item("- [x] Milch [Marge](../people/marge.md) ✅ 2026-10-10", path=GROCERIES)
        archive = "family/lists/archive/einkaufsliste/2026-10.md"
        assert format_item(item, path=archive) == \
            "- [x] Milch [Marge](../../../people/marge.md) ✅ 2026-10-10"

    def test_any_other_line_is_not_an_item(self):
        assert parse_item("## Milchprodukte & Eier", path=GROCERIES) is None
        assert parse_item("- Milch, without a checkbox", path=GROCERIES) is None


class TestWhatTheFamilyWroteStays:
    def test_a_link_inside_the_sentence_is_text_not_an_assignment(self):
        line = "- [ ] Call [Homer](../people/homer.md) about the car"
        item = parse_item(line, path=GROCERIES)
        assert item.people == () and item.text == "Call [Homer](../people/homer.md) about the car"
        assert format_item(item, path=GROCERIES) == line

    def test_a_web_link_stays_in_the_text(self):
        """Things to order online often carry the shop's link."""
        line = "- [ ] Druckerpatronen [Shop](https://example.com/ink)"
        item = parse_item(line, path=GROCERIES)
        assert item.links == () and format_item(item, path=GROCERIES) == line

    def test_an_unknown_tasks_field_is_kept(self):
        line = "- [ ] Steuererklärung [Homer](../people/homer.md) ⏫"
        item = parse_item(line, path=GROCERIES)
        assert format_item(item, path=GROCERIES) == line

    def test_another_tasks_status_is_kept(self):
        line = "- [-] Kino-Gutschein"
        item = parse_item(line, path=GROCERIES)
        assert not item.done and format_item(item, path=GROCERIES) == line

    def test_a_note_under_a_heading_survives_an_insert(self):
        text = _add(_groceries(), "Äpfel", "Gemüse & Obst")
        text = text.replace("- [ ] Äpfel\n", "- [ ] Äpfel\nNur die vom Bauern!\n")
        text = _add(text, "Birnen", "Gemüse & Obst")
        assert "- [ ] Äpfel\nNur die vom Bauern!\n- [ ] Birnen\n" in text


class TestAList:
    def test_reads_its_title_description_topics_and_sections(self):
        lst = read_list(_groceries(), path=GROCERIES)
        assert (lst.slug, lst.title, lst.description) == \
            ("einkaufsliste", "Einkaufsliste", "Was wir im Supermarkt brauchen")
        assert lst.topics == ["einkaeufe-besorgungen"] and lst.sections == STORE

    def test_each_item_knows_its_section(self):
        text = _add(_add(_groceries(), "Joghurt", "Milchprodukte & Eier"), "Batterien")
        lst = read_list(text, path=GROCERIES)
        assert [(i.text, i.section) for i in lst.items] == \
            [("Batterien", ""), ("Joghurt", "Milchprodukte & Eier")]

    def test_open_items_leave_out_the_ticked_ones(self):
        text = _add(_add(_groceries(), "Milch", "Milchprodukte & Eier", done=True), "Eier",
                    "Milchprodukte & Eier")
        assert [i.text for i in read_list(text, path=GROCERIES).open_items] == ["Eier"]

    def test_a_list_needs_only_a_title(self):
        text = new_list("Garage aufräumen")
        assert text == "---\ntype: list\ntitle: Garage aufräumen\n---\n"
        assert read_list(text, path="family/lists/garage.md").items == []

    def test_a_file_that_is_not_a_list_is_refused(self):
        with pytest.raises(ListError, match="not a list"):
            read_list("---\ntype: note\ntitle: Einkauf\ntimestamp: x\n---\n",
                      path="family/lists/einkauf.md")


class TestTheWalkingOrder:
    """Sections stay in the order of `sections`, whatever order items arrive in."""

    def test_sections_appear_in_store_order(self):
        text = _groceries()
        for name, section in [("Zahnpasta", "Drogerie"), ("Joghurt", "Milchprodukte & Eier"),
                              ("Äpfel", "Gemüse & Obst"), ("Brezeln", "Brot & Backwaren")]:
            text = _add(text, name, section)
        assert _body(text) == (
            "\n## Gemüse & Obst\n- [ ] Äpfel\n"
            "\n## Brot & Backwaren\n- [ ] Brezeln\n"
            "\n## Milchprodukte & Eier\n- [ ] Joghurt\n"
            "\n## Drogerie\n- [ ] Zahnpasta\n")

    def test_an_item_joins_the_end_of_its_section(self):
        text = _add(_add(_groceries(), "Joghurt", "Milchprodukte & Eier"), "Zahnpasta", "Drogerie")
        text = _add(text, "Eier", "milchprodukte & eier")
        assert "## Milchprodukte & Eier\n- [ ] Joghurt\n- [ ] Eier\n\n## Drogerie" in text

    def test_an_item_without_a_section_goes_above_the_first_heading(self):
        text = _add(_add(_groceries(), "Joghurt", "Milchprodukte & Eier"), "Batterien")
        assert _body(text) == "\n- [ ] Batterien\n\n## Milchprodukte & Eier\n- [ ] Joghurt\n"

    def test_a_section_the_list_does_not_name_goes_last(self):
        text = _add(_add(_groceries(), "Zahnpasta", "Drogerie"), "Kerzen", "Deko")
        assert _body(text).endswith("## Drogerie\n- [ ] Zahnpasta\n\n## Deko\n- [ ] Kerzen\n")

    def test_a_list_without_sections_keeps_items_in_the_order_they_came(self):
        text = new_list("Garage aufräumen")
        for name in ["Werkbank leeren", "Fahrräder aufhängen"]:
            text = insert_item(text, Item(name), path="family/lists/garage.md")
        assert _body(text) == "\n- [ ] Werkbank leeren\n- [ ] Fahrräder aufhängen\n"


def test_link_targets_are_vault_paths():
    """A link outside the vault is not a field: it could never be resolved."""
    item = parse_item("- [ ] Milch [x](../../../etc/passwd.md)", path=GROCERIES)
    assert item.links == ()


def test_a_renamed_record_keeps_its_items_linked():
    old, new = "family/documents/2026/10/a-p1.md", "family/documents/2026/10/b-p1.md"
    text = insert_item(_groceries(), Item("Pay", links=(Link("Bill", old),), due="2026-10-30"),
                       path=GROCERIES)
    moved = relink(text, old, new, path=GROCERIES)
    assert read_list(moved, path=GROCERIES).items[0].sources == (new,)
    assert "📅 2026-10-30" in moved

