"""Detecting explicitly-written todo lists, and maintaining `todos.md`.

Detection is deliberately narrow: a note becomes a list only when its first
line *announces* it ("Liste …:", "Todo:"). Prose never becomes a list — that is
the guard against manufacturing household todos, the same stance the capture
classifier takes by leaving `action_items` out (see test_capture_prompt.py).
Zero LLM, so there is no over-eager model to misfire.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "stacklets" / "memory" / "bot" / "cli"))

import pytest  # noqa: E402

from todo_list import (  # noqa: E402
    add_items,
    detect_list,
    read_todos,
    render_todo_doc,
    set_todo_done,
    update_todo_doc,
)


class TestDetectList:

    def test_german_marker_with_colon(self):
        body = ("Liste Bus Erweiterungen:\n"
                "Fenster Tasche\nWände für die Markise\nKochlöffel")
        title, items = detect_list(body)
        assert title == "Liste Bus Erweiterungen"
        assert items == ["Fenster Tasche", "Wände für die Markise", "Kochlöffel"]

    def test_todo_marker(self):
        title, items = detect_list("Todo:\nbuy milk\nbook tickets")
        assert title == "Todo"
        assert items == ["buy milk", "book tickets"]

    def test_marker_without_colon(self):
        title, items = detect_list("Einkaufsliste\nMilch\nBrot")
        assert title == "Einkaufsliste"
        assert items == ["Milch", "Brot"]

    def test_blank_lines_ignored(self):
        _, items = detect_list("Todo:\n\nbuy milk\n\n\nbook tickets\n")
        assert items == ["buy milk", "book tickets"]

    # ── the false-positive guards ──

    def test_prose_is_not_a_list(self):
        # The Reddit-thread / manufacture-a-todo case.
        assert detect_list(
            "We had a great time camping this weekend. Bart loved it.") is None

    def test_status_message_is_not_a_list(self):
        assert detect_list(
            "Fenstertasche ist bestellt, Thema 1 kann abgehakt werden") is None

    def test_question_is_not_a_list(self):
        assert detect_list("Welche Themen sind noch auf der Liste?") is None

    def test_marker_but_no_items(self):
        assert detect_list("Todo:") is None

    def test_empty(self):
        assert detect_list("") is None


class TestRenderTodoDoc:

    def test_fresh_doc_is_obsidian_tasks(self):
        doc = render_todo_doc("Bus Erweiterungen", ["Fenster Tasche", "Kochlöffel"])
        assert doc.startswith("# Bus Erweiterungen\n")
        assert "- [ ] Fenster Tasche" in doc
        assert "- [ ] Kochlöffel" in doc


class TestAddItems:

    def test_appends_new_items(self):
        existing = "# Bus\n\n- [ ] Fenster Tasche\n"
        out = add_items(existing, ["Kochlöffel", "Dachbox"])
        assert "- [ ] Kochlöffel" in out
        assert "- [ ] Dachbox" in out
        assert out.count("Fenster Tasche") == 1

    def test_skips_duplicate_open_items(self):
        existing = "# Bus\n\n- [ ] Fenster Tasche\n"
        assert add_items(existing, ["Fenster Tasche"]).count("Fenster Tasche") == 1

    def test_preserves_done_items(self):
        existing = "# Bus\n\n- [x] Fenster Tasche\n- [ ] Dachbox\n"
        out = add_items(existing, ["Kochlöffel"])
        assert "- [x] Fenster Tasche" in out
        assert "- [ ] Kochlöffel" in out

    def test_does_not_resurrect_a_done_item(self):
        # Re-sending an item she already ticked off must not re-open it.
        existing = "# Bus\n\n- [x] Fenster Tasche\n"
        out = add_items(existing, ["Fenster Tasche"])
        assert "- [ ] Fenster Tasche" not in out
        assert out.count("Fenster Tasche") == 1


class TestUpdateTodoDoc:

    def test_creates_when_missing(self):
        out = update_todo_doc(None, "Bus", ["Fenster Tasche"])
        assert out.startswith("# Bus\n")
        assert "- [ ] Fenster Tasche" in out

    def test_appends_when_present(self):
        existing = "# Bus\n\n- [ ] Fenster Tasche\n"
        out = update_todo_doc(existing, "Bus", ["Kochlöffel"])
        assert "- [ ] Fenster Tasche" in out
        assert "- [ ] Kochlöffel" in out


class TestReadTodos:
    """The read side the CLI list command goes through: a rendered
    `todos.md` split into open and done task texts, ignoring the title
    and blank lines."""

    def test_splits_open_and_done(self):
        doc = ("# Bus\n\n"
               "- [ ] Fenster Tasche\n"
               "- [x] Kochlöffel\n"
               "- [X] Markise\n")
        open_items, done_items = read_todos(doc)
        assert open_items == ["Fenster Tasche"]
        assert done_items == ["Kochlöffel", "Markise"]

    def test_ignores_title_and_prose(self):
        doc = "# Einkauf\n\nSome note.\n- [ ] Milch\n"
        assert read_todos(doc) == (["Milch"], [])

    def test_empty_doc(self):
        assert read_todos("# Bus\n") == ([], [])


class TestSetTodoDone:
    DOC = (
        "# Itchy & Scratchy Land\n\n"
        "- [ ] pick up the wristbands\n"
        "- [ ] buy sunscreen for Bart\n"
        "- [x] charge the camera\n"
    )

    def test_strike_open_item(self):
        new, matched = set_todo_done(self.DOC, "buy sunscreen for Bart", done=True)
        assert matched == "buy sunscreen for Bart"
        assert "- [x] buy sunscreen for Bart\n" in new
        # every other line is left exactly as it was
        assert "- [ ] pick up the wristbands\n" in new
        assert "- [x] charge the camera\n" in new

    def test_unstrike_done_item(self):
        new, matched = set_todo_done(self.DOC, "charge the camera", done=False)
        assert matched == "charge the camera"
        assert "- [ ] charge the camera\n" in new

    def test_prefix_match(self):
        new, matched = set_todo_done(self.DOC, "buy sunscreen", done=True)
        assert matched == "buy sunscreen for Bart"
        assert "- [x] buy sunscreen for Bart\n" in new

    def test_mid_word_string_is_not_a_prefix(self):
        # "sunscreen" is inside a task but does not start it -> no match
        with pytest.raises(ValueError, match="no todo matching"):
            set_todo_done(self.DOC, "sunscreen", done=True)

    def test_exact_wins_over_longer_sibling(self):
        doc = "# L\n- [ ] milk\n- [ ] milk and eggs\n"
        new, matched = set_todo_done(doc, "milk", done=True)
        assert matched == "milk"
        assert new == "# L\n- [x] milk\n- [ ] milk and eggs\n"

    def test_prefers_the_state_changing_task(self):
        # same text open and done: striking targets the open one, not the done
        doc = "# L\n- [ ] milk\n- [x] milk\n"
        new, matched = set_todo_done(doc, "milk", done=True)
        assert matched == "milk"
        assert new == "# L\n- [x] milk\n- [x] milk\n"

    def test_no_match_raises(self):
        with pytest.raises(ValueError, match="no todo matching"):
            set_todo_done(self.DOC, "walk the dog", done=True)

    def test_ambiguous_prefix_lists_the_matches(self):
        doc = "# L\n- [ ] call the dentist\n- [ ] call the vet\n"
        with pytest.raises(ValueError) as ei:
            set_todo_done(doc, "call", done=True)
        msg = str(ei.value)
        assert "more than one match" in msg
        assert "call the dentist" in msg and "call the vet" in msg

    def test_striking_a_done_item_is_a_noop(self):
        # already `[x]`: the doc comes back unchanged, so edit_file makes no commit
        new, matched = set_todo_done(self.DOC, "charge the camera", done=True)
        assert new == self.DOC
        assert matched == "charge the camera"
