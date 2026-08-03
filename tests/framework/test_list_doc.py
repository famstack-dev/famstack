"""What a list page is, and what changed between two versions of one.

A family's list lives in `todos.md` and more than one thing writes it: the
curator merging extracted action items, a person editing it in Forgejo, and
(soon) an agent rewriting it wholesale. The failure that matters when an agent
holds the pen is not a malformed document, it is a *quiet* one: six of
twenty-five items gone and a cheerful confirmation. So this module's job is not
"is this valid markdown" -- that is easy and worthless -- it is "say exactly
what this edit did, and be loud about what it destroyed".

These tests are written from the caller's side and pin the two promises that
matter: rewriting a list without changing it changes nothing, and losing an
item is always named out loud.

The fixtures are the real list from a family's Road-Trip room (June to August
2026), because that list is what taught us the lesson: thirteen items became
twenty-seven entries and never a single one ticked off.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from stack.list_doc import diff, parse  # noqa: E402

# Marge's list, as she actually posted it.
BUS = """# Road-Trip

- [ ] Fenstertasche
- [ ] Wände für die Markise
- [ ] Alternative Dachbox
- [ ] Verbesserung Stauraum innen
- [ ] Abdichtung Zwischenraum Markise/Bus
- [ ] Kochlöffel
- [ ] Update Packliste
- [ ] Verbesserung Küche
"""

# The same list after she marked three of them done.
BUS_CHECKED = """# Road-Trip

- [x] Fenstertasche
- [ ] Wände für die Markise
- [ ] Alternative Dachbox
- [ ] Verbesserung Stauraum innen
- [x] Abdichtung Zwischenraum Markise/Bus
- [x] Kochlöffel
- [ ] Update Packliste
- [ ] Verbesserung Küche
"""

TWO_SECTIONS = """# Road-Trip

## Verbesserungen

- [ ] Wände für die Markise
- [ ] Alternative Dachbox

## Packliste

- [ ] Strandtasche
- [ ] Laufstall
"""


class TestReadingAListPage:

    def test_it_finds_the_items_and_their_state(self):
        items = parse(BUS_CHECKED)

        assert len(items) == 8
        assert [i.text for i in items][:2] == ["Fenstertasche", "Wände für die Markise"]
        assert [i.text for i in items if i.done] == [
            "Fenstertasche", "Abdichtung Zwischenraum Markise/Bus", "Kochlöffel",
        ]

    def test_a_page_with_no_headings_is_one_unnamed_list(self):
        """Every list that exists today looks like this, so it has to keep
        parsing as a list rather than as a schema violation."""
        assert {i.section for i in parse(BUS)} == {""}

    def test_headings_split_a_page_into_named_lists(self):
        """Marge asked for exactly this: 'Es sollen zwei Listen sein.'"""
        by_section = {}
        for item in parse(TWO_SECTIONS):
            by_section.setdefault(item.section, []).append(item.text)

        assert by_section == {
            "Verbesserungen": ["Wände für die Markise", "Alternative Dachbox"],
            "Packliste": ["Strandtasche", "Laufstall"],
        }

    def test_prose_around_the_items_is_not_an_item(self):
        items = parse("# Road-Trip\n\nSome notes here.\n\n- [ ] Kochlöffel\n\nMore prose.\n")

        assert [i.text for i in items] == ["Kochlöffel"]


class TestSayingWhatAnEditDid:

    def test_rewriting_a_list_unchanged_changes_nothing(self):
        """The promise that would have saved the Road-Trip list.

        Re-posting the same list six times produced twenty-seven entries
        because each pass re-worded the items. An edit that says the same
        thing must register as saying the same thing.
        """
        change = diff(BUS, BUS)

        assert not change.any(), change.summary()

    def test_ticking_items_off_reads_as_ticking_off(self):
        change = diff(BUS, BUS_CHECKED)

        assert change.struck == [
            "Fenstertasche", "Abdichtung Zwischenraum Markise/Bus", "Kochlöffel",
        ]
        assert change.removed == []
        assert change.added == []

    def test_unticking_is_reported_as_its_own_thing(self):
        change = diff(BUS_CHECKED, BUS)

        assert change.reopened == [
            "Fenstertasche", "Abdichtung Zwischenraum Markise/Bus", "Kochlöffel",
        ]
        assert change.struck == []

    def test_a_dropped_item_is_named_out_loud(self):
        """The dangerous class. An agent rewriting a list can quietly lose
        items, and a count alone ('8 items -> 7') is not something a family
        member can check. The names are the point.
        """
        without_kochloeffel = BUS.replace("- [ ] Kochlöffel\n", "")

        change = diff(BUS, without_kochloeffel)

        assert change.removed == ["Kochlöffel"]
        assert change.destructive() is True

    def test_ticking_something_off_is_not_destructive(self):
        """Striking is the everyday case and must not cry wolf."""
        assert diff(BUS, BUS_CHECKED).destructive() is False

    def test_rewording_is_reported_as_rewording_not_as_loss(self):
        """Exactly what the classifier did to this list.

        'Alternative Dachbox' came back as 'suchen', 'recherchieren',
        'prüfen' and 'besorgen' on successive passes. Reporting each as a
        deletion plus an unrelated addition would bury the signal in noise,
        so a near-match is paired and named for what it is.
        """
        reworded = BUS.replace("Alternative Dachbox", "Alternative Dachbox suchen")

        change = diff(BUS, reworded)

        assert change.reworded == [("Alternative Dachbox", "Alternative Dachbox suchen")]
        assert change.removed == []

    def test_a_reordered_rewrite_counts_as_loss_not_rewording(self):
        """Deliberately conservative, and the reason is the curator.

        "Verbesserung Stauraum innen" coming back as "Stauraum innen
        verbessern" is not a harmless restatement: it is the family's own
        words being replaced by the model's, which is what defeated dedup
        and grew the list. Only an obvious extension ("X" -> "X <verb>") is
        forgiven. Widening this would start hiding exactly the loss the
        module exists to surface.
        """
        reordered = BUS.replace("Verbesserung Stauraum innen",
                                "Stauraum innen verbessern")

        change = diff(BUS, reordered)

        assert change.removed == ["Verbesserung Stauraum innen"]
        assert change.destructive() is True

    def test_a_genuinely_different_item_is_not_paired_with_a_deletion(self):
        """Pairing has to stay conservative: guessing that an unrelated new
        item 'replaces' a deleted one would hide the deletion, which is the
        one thing this module exists to prevent."""
        swapped = BUS.replace("- [ ] Kochlöffel\n", "- [ ] Moskitonetz Schiebetür\n")

        change = diff(BUS, swapped)

        assert change.removed == ["Kochlöffel"]
        assert [i.text for i in change.added] == ["Moskitonetz Schiebetür"]
        assert change.reworded == []

    def test_splitting_one_list_into_two_moves_items_rather_than_losing_them(self):
        """Marge's actual request: split the list at a given point. The items
        are the same items; only their heading changed. A validator that
        called this eight deletions would block the very edit she asked for.
        """
        before = "# Road-Trip\n\n- [ ] Wände für die Markise\n- [ ] Strandtasche\n"
        after = ("# Road-Trip\n\n## Verbesserungen\n\n- [ ] Wände für die Markise\n"
                 "\n## Packliste\n\n- [ ] Strandtasche\n")

        change = diff(before, after)

        assert change.removed == []
        assert change.destructive() is False
        assert sorted(change.moved) == [
            ("Strandtasche", "", "Packliste"),
            ("Wände für die Markise", "", "Verbesserungen"),
        ]


class TestTheSummaryTheCallerReadsBack:

    def test_it_names_what_was_lost(self):
        summary = diff(BUS, BUS.replace("- [ ] Kochlöffel\n", "")).summary()

        assert "Kochlöffel" in summary
        assert "removed" in summary.lower()

    def test_an_unchanged_edit_says_so_plainly(self):
        assert "no change" in diff(BUS, BUS).summary().lower()

    def test_it_reads_as_a_commit_message_for_the_ordinary_case(self):
        """The semantic diff is also the commit line, so intent comes out of
        what actually changed rather than a string the caller invents."""
        summary = diff(BUS, BUS_CHECKED).summary()

        assert summary.startswith("ticked off 3")
