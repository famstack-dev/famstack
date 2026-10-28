"""The quality eval's scoring, on fixed home pages.

The quality eval (`tests/e2e/eval/wiki_compile.py`) asks a real model to
write the wiki for a fabricated family and scores what it wrote against
the facts the family's first filings plant (`tests/e2e/eval/wiki_score.py`).
A wrong score would make every later comparison of the compile wrong, so
the scoring is pinned here on pages written by hand in the shape the home
page's template gives them: a fact in its section, in another section,
missing, corrected; a citation that resolves and one that does not.
"""

from __future__ import annotations

from tests.e2e.eval.wiki_score import (
    Cell, compare, measures, score, score_citations, score_fact,
)
from tests.memory.unit.fabricated_family import Fact

CARDS = {
    "/family/documents/2026/01/car-insurance-policy-p4.md":
        "> [!summary]\n> The car is insured with Duff Insurance for $640 a year.",
    "/family/notes/2026/01/grampa-s-visit-0009.md":
        "> [!summary]\n> Abe Simpson, Homer's father, visited from the retirement castle.",
    "/family/notes/2026/01/dentist-for-lisa-0015.md":
        "> [!summary]\n> Lisa sees Dr. Wolfe on 2026-10-21.",
    "/lisa/saxophone/notes/2026/05/saxophone-lessons-moved-001a.md":
        "> [!summary]\n> Lisa's saxophone lesson moves from Tuesday to every Thursday.",
}

PAGE = """# The Simpsons
> 742 Evergreen Terrace, Springfield

The Simpsons of Springfield [2].

## Schedule
- **21 Oct 2026** · Lisa sees Dr. Wolfe [3]
- **Every Thursday** · Lisa's saxophone lesson [4]

## At a glance
- **Car insurance** · Duff Insurance [1]

## Relatives
- **Abe Simpson** · Homer's father [2]

## Insurance
- **Home insurance, Springfield Mutual** · The house [9]

## References

- [1] [Car insurance policy](/family/documents/2026/01/car-insurance-policy-p4.md) - 2026-01-02
- [2] [Grampa's visit](/family/notes/2026/01/grampa-s-visit-0009.md) - 2026-01-03
- [3] [Dentist for Lisa](/family/notes/2026/01/dentist-for-lisa-0015.md) - 2026-01-05
- [4] [Saxophone lessons moved](/lisa/saxophone/notes/2026/05/saxophone-lessons-moved-001a.md)
"""


def read(link: str) -> str | None:
    return CARDS.get(link)


def fact(section: str, name: str, value: str, was: str = "") -> Fact:
    return Fact(section, name, value, card="", was=was)


def test_a_fact_in_its_section_is_found_with_a_citation_that_holds_it():
    """Given the relative on the page under Relatives, citing the card that
    names him, then the fact is found there and cited."""
    found = score_fact(fact("relatives", "Abe Simpson", "Homer's father"), PAGE, read)
    assert (found.status, found.where, found.cited) == ("found", "Relatives", True)


def test_a_fact_under_another_heading_is_elsewhere():
    """Given the car insurer under At a glance, not Insurance, then the fact
    is in the wrong section, and its citation still holds it."""
    found = score_fact(fact("insurance", "Car insurance", "Duff Insurance"), PAGE, read)
    assert (found.status, found.where, found.cited) == ("elsewhere", "At a glance", True)


def test_a_fact_the_page_does_not_state_is_missing():
    found = score_fact(fact("helpers", "Dr. Julius Hibbert", "family doctor"), PAGE, read)
    assert (found.status, found.cited) == ("missing", None)


def test_the_address_is_found_under_the_familys_name_without_a_citation():
    """Given the address in the blockquote under the heading, then it is
    found in its place, and no citation is asked of it: the page shows none."""
    found = score_fact(fact("address", "Home", "742 Evergreen Terrace, Springfield"), PAGE, read)
    assert (found.status, found.cited) == ("found", None)


def test_a_date_ahead_is_found_as_the_schedule_prints_it():
    """Given the dentist on 2026-10-21, printed as 21 Oct 2026, citing the
    card that says 2026-10-21, then it is found and cited."""
    found = score_fact(fact("schedule", "Lisa's dentist appointment", "2026-10-21"), PAGE, read)
    assert (found.status, found.where, found.cited) == ("found", "Schedule", True)


def test_a_citation_to_a_card_without_the_fact_does_not_count():
    """Given Abe Simpson's line citing the car insurance policy, then a fact
    cited to a card that does not hold it is not cited."""
    page = PAGE.replace("**Abe Simpson** · Homer's father [2]", "**Abe Simpson** · Homer's father [1]")
    found = score_fact(fact("relatives", "Abe Simpson", "Homer's father"), page, read)
    assert (found.status, found.cited) == ("found", False)


def test_a_corrected_fact_is_found_and_the_old_value_is_stale_when_still_shown():
    corrected = fact("recurring", "Saxophone lesson", "every Thursday", was="every Tuesday")
    assert score_fact(corrected, PAGE, read).stale is False
    still = PAGE.replace("Lisa's saxophone lesson [4]",
                         "Lisa's saxophone lesson [4]\n- **Every Tuesday** · Saxophone lesson [4]")
    found = score_fact(corrected, still, read)
    assert (found.status, found.stale) == ("found", True)


def test_a_citation_resolves_when_its_row_leads_to_a_card_that_exists():
    """Given six citations of sources 1 to 4, whose rows lead to cards that
    exist, and of 9, which has no row (the model cited a source it was not
    given), then five of six resolve and 9 is named."""
    found = score_citations(PAGE, read, name="index.md")
    assert (found.total, found.resolved, found.unresolved) == (6, 5, ["index.md[9]"])


def test_a_citation_whose_card_is_gone_does_not_resolve():
    """Given the card source 2 leads to gone, then both citations of it fail."""
    found = score_citations(PAGE, lambda link: None if "grampa" in link else read(link))
    assert (found.total, found.resolved) == (6, 3)
    assert found.unresolved == ["[2]", "[2]", "[9]"]


def test_a_scenarios_score_counts_facts_and_citations():
    facts = [fact("relatives", "Abe Simpson", "Homer's father"),
             fact("insurance", "Car insurance", "Duff Insurance"),
             fact("helpers", "Dr. Julius Hibbert", "family doctor")]
    result = score(PAGE, {"index.md": PAGE}, facts, read)
    assert {k: result[k] for k in ("facts_found", "facts_total", "facts_cited",
                                   "citations_total", "citations_resolved")} == {
        "facts_found": 1, "facts_total": 3, "facts_cited": 1,
        "citations_total": 6, "citations_resolved": 5}


def test_two_runs_compare_per_scenario_with_the_spread_of_their_repeats():
    """Given run a with two repeats that disagree on the facts found and run
    b with one, then the table shows a's mean and spread, b's value, and
    the difference of the means."""
    def run(facts: list[int], tokens: int) -> tuple[list[dict], dict]:
        calls = [{"size": 50, "repeat": r, "scenario": "seed", "prompt_tokens": tokens,
                  "completion_tokens": 10, "seconds": 2.0, "ok": True}
                 for r in range(1, len(facts) + 1)]
        scores = {"50": {str(r): {"seed": {"facts_found": f, "facts_cited": f,
                                            "citations_resolved": 4, "citations_total": 5}}
                         for r, f in enumerate(facts, start=1)}}
        return calls, scores

    a, b = measures(*run([21, 23], 1000)), measures(*run([24], 400))
    table = compare(a, b, names=("before", "after"))
    assert "#### 50 filings, seed" in table
    assert "| facts found | 22 (21–23) | 24 | +2 |" in table
    assert "| prompt tokens | 1,000 | 400 | -600 |" in table
    assert "| citations resolved | 80 | 80 | 0 |" in table
    assert Cell([]).text() == "-"
