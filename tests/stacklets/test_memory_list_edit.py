"""`stack memory list-edit` — one item changes, nothing else moves.

The verb exists because whole-page rewrites lose state the caller never
meant to touch: in the agent lab, a category restructure dropped an item's
`[x]` mark, and prompt rules did not reliably repair it. The contract under
test: the caller only names an item; the store finds it, changes exactly
that line, and answers with what happened. Ambiguity and misses are
instructive refusals, never guesses, because a wrong guess strikes a
family member's item silently.

These tests drive the pure transform through its public shape
(text in, text out, sentence, kind) — the same calls the CLI makes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "cli"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


list_edit = _load(
    "memory_cli_list_edit",
    _REPO_ROOT / "stacklets" / "memory" / "cli" / "list-edit.py",
)

PAGE = """---
type: note
date: 2026-09-14
---

# Shopping list

## Dairy

- [ ] oat milk
- [x] coffee beans

## Household

- [ ] dish soap
"""


def test_tick_by_partial_name_changes_only_that_line():
    new, sentence, kind = list_edit.apply_list_edit(PAGE, "tick", "oat")
    assert kind == "changed"
    assert sentence == "ticked off 1: oat milk"
    assert "- [x] oat milk" in new
    # Everything else is untouched, byte for byte.
    assert new.replace("- [x] oat milk", "- [ ] oat milk") == PAGE


def test_ambiguous_name_is_refused_and_names_the_candidates():
    page = PAGE + "- [ ] oat cookies\n"
    new, sentence, kind = list_edit.apply_list_edit(page, "tick", "oat")
    assert kind == "refuse"
    assert new == page
    assert "oat milk" in sentence and "oat cookies" in sentence


def test_exact_match_wins_over_substring():
    page = PAGE + "- [ ] milk\n"
    new, sentence, kind = list_edit.apply_list_edit(page, "tick", "milk")
    assert kind == "changed"
    assert sentence == "ticked off 1: milk"
    assert "- [ ] oat milk" in new


def test_unknown_item_is_refused_and_lists_what_is_open():
    new, sentence, kind = list_edit.apply_list_edit(PAGE, "tick", "bananas")
    assert kind == "refuse"
    assert new == PAGE
    assert "oat milk" in sentence and "dish soap" in sentence
    # Done items are not offered as candidates for a tick.
    assert "coffee beans" not in sentence


def test_add_lands_in_the_named_section():
    new, sentence, kind = list_edit.apply_list_edit(
        PAGE, "add", "butter", section="Dairy")
    assert kind == "changed"
    assert sentence == "added 1: butter"
    dairy = new.split("## Dairy")[1].split("## Household")[0]
    assert "- [ ] butter" in dairy


def test_add_duplicate_is_an_honest_noop():
    new, sentence, kind = list_edit.apply_list_edit(PAGE, "add", "Oat Milk")
    assert kind == "noop"
    assert new == PAGE
    assert "already on the list" in sentence


def test_remove_names_what_it_destroyed():
    new, sentence, kind = list_edit.apply_list_edit(PAGE, "remove", "dish soap")
    assert kind == "changed"
    assert sentence == "REMOVED 1: dish soap"
    assert "dish soap" not in new


def test_clear_done_removes_only_ticked_items_and_names_them():
    page = PAGE.replace("- [ ] dish soap", "- [x] dish soap")
    new, sentence, kind = list_edit.apply_list_edit(page, "clear-done", "")
    assert kind == "changed"
    assert sentence == "REMOVED 2: coffee beans; dish soap"
    assert "coffee beans" not in new and "dish soap" not in new
    assert "- [ ] oat milk" in new


def test_clear_done_on_a_clean_list_is_a_noop():
    page = PAGE.replace("- [x] coffee beans", "- [ ] coffee beans")
    new, sentence, kind = list_edit.apply_list_edit(page, "clear-done", "")
    assert kind == "noop"
    assert new == page


def test_reset_reopens_every_ticked_item():
    new, sentence, kind = list_edit.apply_list_edit(PAGE, "reset", "")
    assert kind == "changed"
    assert sentence == "reopened 1: coffee beans"
    assert "- [x]" not in new


def test_untick_reports_a_reopening():
    new, sentence, kind = list_edit.apply_list_edit(PAGE, "untick", "coffee")
    assert kind == "changed"
    assert sentence == "reopened 1: coffee beans"
    assert "- [ ] coffee beans" in new


def test_batch_add_is_one_change_naming_every_item():
    new, sentence, kind = list_edit.apply_list_edits(
        PAGE, "add", ["butter", "flour"])
    assert kind == "changed"
    assert sentence == "added 2: butter; flour"
    assert "- [ ] butter" in new and "- [ ] flour" in new


def test_batch_tick_across_the_page():
    new, sentence, kind = list_edit.apply_list_edits(
        PAGE, "tick", ["oat milk", "dish soap"])
    assert kind == "changed"
    assert sentence == "ticked off 2: oat milk; dish soap"
    assert "- [x] oat milk" in new and "- [x] dish soap" in new


def test_batch_reports_changed_and_misses_together():
    new, sentence, kind = list_edit.apply_list_edits(
        PAGE, "tick", ["oat milk", "bananas"])
    assert kind == "changed"
    assert "ticked off 1: oat milk" in sentence
    assert "no item matching 'bananas'" in sentence
    assert "- [x] oat milk" in new


def test_batch_of_one_matches_the_single_form():
    a = list_edit.apply_list_edits(PAGE, "tick", ["oat milk"])
    b = list_edit.apply_list_edit(PAGE, "tick", "oat milk")
    assert a == b
