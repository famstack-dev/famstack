"""How big the wiki compile's prompts are, and how they change as a family files.

A page's prompt is everything the model reads to write it, and the model
runs on the family's own Mac: a prompt that grows with every filing makes
each compile slower until it no longer fits the model's context, and one
that changes ahead of the new filing cannot reuse the prompt cache. These
tests read the prompts the compile sends as the curator runs it, on a
fabricated family of 50 and of 500 filings, in the scenarios
`compile_prompts.py` describes and measures.

What holds today is asserted. The limits the compile is meant to meet
(workspace design `memory-compile` §2) and does not meet yet are strict
expected failures, each with today's number in its reason, so the item
that meets one turns it green. The home and person pages fold the cards
filed since their base into it (`stacklets/memory/bot/fold.py`), so the
budget and the flatness hold for them; topic pages still read every
source they have. A prompt's size is estimated at 3 characters a token.

The measurement takes about 40 seconds at 500 filings, alone on the Mac:
each step of the curator's loop is a process of its own, and so is every
compile it starts. It runs in the background from the moment the lane has
collected its tests, and these tests are collected last (memory's
conftest), so they find it made instead of a worker waiting for it. The
baseline table of one size, by hand:

    PYTHONPATH=lib python -m tests.memory.unit.compile_prompts 500 /tmp/prompts
"""

from __future__ import annotations

import re

import pytest

from tests.memory.unit.compile_prompts import (
    DAY_PAGES, SIZES, Prompts, SCENARIOS, end_of_sources, shared_prefix, start, tokens, wait_for)

CONTEXT = 8_192  # tokens: the limit every call's prompt must stay within (design §2)


@pytest.fixture(scope="session")
def measured() -> dict[int, Prompts]:
    """The prompts at each size, as this run's background measurement made them."""
    start()  # a no-op when the lane started it at collection
    return {size: wait_for(size) for size in SIZES}


def open_items(page: str) -> list[str]:
    """The open items a page lists, as `- [ ] <item>` lines."""
    return re.findall(r"^\s*- \[ \] (.+?)\s*$", page, re.M)


# ── What holds today ────────────────────────────────────────────────────

def test_every_scenario_is_measured_and_every_request_names_its_page(measured):
    """Given the scenarios at both sizes, when the model is asked, then each
    request names a page the stand-in knows: an unknown prompt would be
    answered wrongly and measured under no page."""
    for size, prompts in measured.items():
        assert set(prompts.calls) == set(SCENARIOS), size
        for scenario, calls in prompts.calls.items():
            assert all(page != "unknown" for page, _ in calls), (size, scenario)
        assert prompts.calls["seed"], size


def test_a_ticked_list_item_shows_at_once_and_asks_the_model_nothing(measured):
    """Given the memory after a night, when Marge ticks an item on the
    groceries list, then the camping topic page, which shows the list's open
    items, drops it at once, and the model is asked nothing: the home page
    shows no list, and a person or topic page reads its open items from the
    list (#345)."""
    for size, prompts in measured.items():
        before, after = (open_items(prompts.camping[when]) for when in ("before", "after"))
        assert "Milk" in before and "Milk" not in after, (size, before, after)
        assert set(before) - set(after) == {"Milk"}, size
        assert prompts.calls["tick"] == [], size


def test_a_rebuild_repeats_the_previous_prompt_up_to_the_new_filing(measured):
    """Given a document filed in the day, when a diary entry follows it, then
    the home page's and Marge's page's new prompts repeat the previous prompt
    through its last source and add the entry after it, so the model's
    prompt cache covers everything it read before (design §7).

    Within a day: the night makes a new base, so the first compile after it
    reads a prompt the night never sent."""
    short = {}
    for size, prompts in measured.items():
        for page in DAY_PAGES:
            before, after = prompts.of("document", page), prompts.of("diary", page)
            if shared_prefix(before, after) < end_of_sources(before):
                short[(size, page)] = (f"{shared_prefix(before, after)} of "
                                       f"{end_of_sources(before)} characters")
    assert not short, short


# ── The limits ──────────────────────────────────────────────────────────

def folded(page: str) -> bool:
    """Whether `page` folds its cards into a base: the home and person pages."""
    return page == "home" or page.startswith("person:")


# The scenarios after one filing, and the nights after them. The seed's
# night builds every page from no base, in as many calls as its cards
# take, and the burst's 40 filings are drawn at random per size, so
# neither is the same work at 50 and at 500 filings.
AFTER_ONE_FILING = ("document", "diary", "correction", "quiet", "next day")


def test_every_prompt_fits_the_context(measured):
    """Given any scenario at either size, then every home and person page's
    prompt is at most 8,192 tokens: what a small local model holds, with
    room for the answer."""
    over = {(size, page): tokens(p) for size, prompts in measured.items()
            for calls in prompts.calls.values() for page, p in calls
            if folded(page) and tokens(p) > CONTEXT}
    assert not over, over


def test_a_pages_prompt_stays_flat_as_the_memory_grows(measured):
    """Given one filing, or a night after, at 50 and at 500 filings, then
    each home and person page's prompt at 500 is within 10% of the same
    page's at 50: a page reads its base and what is new, not everything
    filed."""
    small, large = (measured[size].calls for size in SIZES)
    grown = {}
    for scenario in AFTER_ONE_FILING:
        before, after = dict(small[scenario]), dict(large[scenario])
        for page in before.keys() & after.keys():
            ratio = tokens(after[page]) / max(1, tokens(before[page]))
            if folded(page) and ratio > 1.1:
                grown[(scenario, page)] = round(ratio, 1)
    assert not grown, grown


def test_a_day_with_nothing_filed_asks_nothing_for_people_or_topics(measured):
    """Given the nightly compile, when the next night comes with nothing
    filed, then no person or topic page is asked of the model again."""
    asked = {size: [page for page, _ in prompts.calls["next day"]
                    if page.startswith(("person:", "topic:"))]
             for size, prompts in measured.items()}
    assert not any(asked.values()), asked
