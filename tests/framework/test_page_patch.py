"""Applying a model's structured edits to a page, with no file involved.

`apply_patch` is the tool nanobot advertises as the default way to change a
file, so it is the one the model reaches for. Its edits are plain text
substitutions against a file on disk; a family memory page is not on disk, so
these tests pin the same operation performed on a string.

Two promises, and the second is the reason the module exists separately from
the tool at all:

  * the semantics match nanobot's exactly, because the model was trained on
    those rules and told them again in the tool description, and
  * an edit that no longer fits the page is refused with a reason, never
    guessed at -- that refusal is what a stale read looks like when somebody
    else changed the page first.

The fixture is the camping list as the rig actually had it the day two writers
raced on it: the archivist appended three items at 16:14:24 and the agent
rewrote the page at 16:14:26 from a read taken ten seconds earlier.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from stack.page_patch import PatchError, apply_edits, edits_from  # noqa: E402

CAMPING = """# Camping

## Ausruestung

- [ ] Wände für die Markise mitbringen
- [x] Fenstertasche prüfen
- [x] Kühlbox mitbringen

## Vorbereitung

- [ ] Wetter checken
"""


def _replace(old, new):
    return [{"path": "vault/family/camping/todos.md", "action": "replace",
             "old_text": old, "new_text": new}]


# ── the ordinary edits a family makes ────────────────────────────────────

def test_ticking_off_an_item_changes_only_that_item():
    """The commonest edit there is, and the one with the most to lose.

    Everything the caller did not name has to come back byte for byte;
    a patch that reflowed the rest would be a whole-page rewrite wearing
    a patch's clothes.
    """
    out = apply_edits(CAMPING, _replace("- [ ] Wetter checken",
                                        "- [x] Wetter checken"))

    assert "- [x] Wetter checken" in out
    assert out == CAMPING.replace("- [ ] Wetter checken", "- [x] Wetter checken")


def test_an_add_puts_the_new_item_at_the_end():
    """`add` appends, exactly as the tool does on a real file."""
    out = apply_edits(CAMPING, [{"action": "add",
                                 "new_text": "- [ ] Heringe nachkaufen\n"}])

    assert out.endswith("- [ ] Heringe nachkaufen\n")
    assert CAMPING in out, "appending must not disturb what was already there"


def test_an_add_does_not_weld_itself_onto_the_last_line():
    """A page saved without a trailing newline is still a page.

    Without this, appending to it silently merges two items into one
    line, which reads as an edit nobody made.
    """
    out = apply_edits("- [ ] Kühlbox", [{"action": "add",
                                         "new_text": "- [ ] Heringe"}])

    assert out == "- [ ] Kühlbox\n- [ ] Heringe\n"


def test_edits_apply_in_order_and_see_each_other():
    """One call can change a line and then build on the result.

    The model batches naturally ("tick that off and add this"), and each
    edit is matched against the page as the previous one left it.
    """
    out = apply_edits(CAMPING, [
        {"action": "replace", "old_text": "## Vorbereitung",
         "new_text": "## Vorbereitung (August)"},
        {"action": "replace", "old_text": "## Vorbereitung (August)",
         "new_text": "## Vorher zu erledigen"},
    ])

    assert "## Vorher zu erledigen" in out
    assert "## Vorbereitung" not in out


def test_the_family_wording_survives_verbatim():
    """Their words, their umlauts, their abbreviations, untouched.

    A date written "bis 15.8." is how they wrote it; nothing here parses,
    normalises, or improves it, which is exactly why no schema is needed
    for dates to work.
    """
    out = apply_edits(CAMPING, [{"action": "add",
                                 "new_text": "- [ ] Zeltheringe nachkaufen bis 15.8.\n"}])

    assert "- [ ] Zeltheringe nachkaufen bis 15.8." in out


# ── the edits that must be refused ───────────────────────────────────────

def test_an_edit_for_a_line_that_is_gone_is_refused_with_why():
    """The stale-read case, which is the whole point of patching server-side.

    When another writer got there first, the honest answer is that the
    line is not there any more. Applying it anyway -- or worse, falling
    back to a whole-page write -- is how the other writer's change
    disappears without a trace.
    """
    with pytest.raises(PatchError) as raised:
        apply_edits(CAMPING, _replace("- [ ] Heringe mitbringen",
                                      "- [x] Heringe mitbringen"))

    message = str(raised.value)
    assert "not found" in message
    assert "Heringe mitbringen" in message, "the error has to name the line"
    assert "read it again" in message, "and say what to do about it"


def test_an_ambiguous_edit_is_refused_rather_than_guessed():
    """Two identical lines under different headings is a normal list.

    Picking one for the model would tick off the wrong item and report
    success, which is the exact failure this whole path exists to stop.
    """
    twice = "## A\n- [ ] Milch\n\n## B\n- [ ] Milch\n"

    with pytest.raises(PatchError) as raised:
        apply_edits(twice, _replace("- [ ] Milch", "- [x] Milch"))

    assert "more than once" in str(raised.value)
    assert "surrounding lines" in str(raised.value), "say how to disambiguate"


def test_a_replace_without_old_text_is_refused():
    """Otherwise it is an append pretending to be a substitution."""
    with pytest.raises(PatchError):
        edits_from([{"action": "replace", "new_text": "x"}])


def test_an_unknown_action_is_refused_by_name():
    """A typo'd action must not silently do nothing and report success."""
    with pytest.raises(PatchError) as raised:
        edits_from([{"action": "delete", "new_text": ""}])

    assert "delete" in str(raised.value)


def test_no_edits_at_all_is_refused():
    """An empty patch that returned "ok" would be a claimed change nobody made."""
    with pytest.raises(PatchError):
        apply_edits(CAMPING, [])
